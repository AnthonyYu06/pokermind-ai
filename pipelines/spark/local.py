"""A real local Spark/Delta vertical slice; it never runs in request handlers."""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from packages.pokermind_core.contracts import Hand, canonical, digest
from packages.pokermind_core.eligibility import POLICY_VERSION, assess, utcnow
from services.api.store import Store

ROOT = Path(__file__).resolve().parents[2]


def configure_java():
    if not os.environ.get("JAVA_HOME"):
        candidates=sorted((ROOT/'.runtime').glob('jdk-17*/Contents/Home'))
        if candidates: os.environ['JAVA_HOME']=str(candidates[-1])
    java=str(Path(os.environ['JAVA_HOME'])/'bin/java') if os.environ.get('JAVA_HOME') else 'java'
    try:
        version=subprocess.run([java,'-version'],capture_output=True,text=True).stderr.splitlines()[0]
    except (FileNotFoundError,IndexError): raise ValueError("Install an ARM64 JDK 17+ and set JAVA_HOME; run make doctor")
    import re
    match=re.search(r'"(\d+)',version)
    if not match or int(match[1])<17: raise ValueError("Spark 4 needs Java 17+. Your default Java is too old; see docs/runtime.md")
    os.environ['PYSPARK_PYTHON']=sys.executable
    os.environ['PYSPARK_DRIVER_PYTHON']=sys.executable
    os.environ['SPARK_LOCAL_IP']='127.0.0.1'
    os.environ['PYTHONPATH']=str(ROOT)+os.pathsep+os.environ.get('PYTHONPATH','')
    return version


def reconstruct_partition(rows):
    from packages.pokermind_core.engine import reconstruct
    from packages.features.equity import full_deal_family, encode
    for row in rows:
        hand=Hand.model_validate_json(row.canonical)
        if not assess(hand.timestamps,row.source_kind,declaration=row.declaration).eligible:
            raise ValueError("Batch eligibility denied")
        _,observations=reconstruct(hand)
        board=[c for event in hand.events for c in event.cards]
        family=full_deal_family(hand.players,board)
        for obs in observations:
            s=obs['state']; actor=obs['acting_player']
            hero=next(p['cards'] for p in s['seats'] if p['seat']==actor)
            active=sum(not p['folded'] for p in s['seats'])
            features=encode(hero,s['board']) if s['street']=='river' and active==2 and len(hero)==2 else []
            yield (f'{row.hand_id}:{obs["action_index"]}',row.hand_id,obs['action_index'],actor,s['street'],
                   len(obs['public_action_prefix']),canonical(obs),obs['observation_hash'],
                   hand.events[obs['action_index']].kind,hero,s['board'],features,family,
                   'train' if int(family[:8],16)%10<7 else 'validation' if int(family[:8],16)%10<9 else 'test')


def label_partition(rows):
    from packages.pokermind_core.equity import equity
    from functools import lru_cache
    @lru_cache(maxsize=2048)
    def reference(hero,board):return equity(list(hero),list(board))
    for row in rows:
        label=reference(tuple(sorted(row.hero)),tuple(sorted(row.board)))
        yield (*tuple(row),label['equity'],label['samples'],label['standard_error'])


def merge_snapshot(spark,df,path,key):
    from delta.tables import DeltaTable
    clean=df.dropDuplicates([key])
    if DeltaTable.isDeltaTable(spark,str(path)):
        (DeltaTable.forPath(spark,str(path)).alias('t').merge(clean.alias('s'),f't.{key}=s.{key}')
         .whenMatchedUpdateAll().whenNotMatchedInsertAll().whenNotMatchedBySourceDelete().execute())
    else: clean.write.format('delta').mode('error').save(str(path))
    version=DeltaTable.forPath(spark,str(path)).history(1).select('version').first()[0]
    return version


def seed_training(store,count,seed):
    from packages.simulation.generate import generate_hand
    for offset in range(0,count,128):
        hands=[generate_hand(seed+i,table_size=2,session=f'training-{seed}-{i//32}') for i in range(offset,min(count,offset+128))]
        payload=('\n'.join(canonical(h) for h in hands)+'\n').encode()
        staged=store._stage(payload,f'synthetic-training-{seed}-{offset}.jsonl','synthetic');store.commit(staged['file_id'])


def _run(root:Path, *, seed=46000,count=256,incremental=False,fail_after=None,partitions=2,aqe=True):
    if not 1 <= count <= 10000:raise ValueError('Local generation accepts 1–10,000 unique hands')
    if not 1<=partitions<=8:raise ValueError('Local partitions must be 1–8')
    if count>1000:
        from training.limits import preflight
        preflight(root)
    started=time.perf_counter()
    java=configure_java()
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession, functions as F, Window
    from pyspark.sql.types import StructType,StructField,StringType,IntegerType,ArrayType,DoubleType,LongType
    from packages.simulation.generate import generate_hand
    store=Store(root)
    # The trusted synthetic entry point generates all records on this server.
    seed_training(store,count,seed)
    lake=root/'lake';lake.mkdir(exist_ok=True)
    input_path=lake/'eligible-input.jsonl'
    published=lake/'published.json'
    previous=json.loads(published.read_text()) if incremental and published.exists() else None
    prior_index=previous.get('source_index',{}) if previous else {}
    with store.connect() as conn:
        source_index={row['hand_id']:digest([row['content_hash'],row['declaration'],row['quality']]) for row in conn.execute("SELECT hand_id,content_hash,declaration,quality FROM hands WHERE source_kind='synthetic' AND quality='valid' ORDER BY hand_id")}
        changed={hid for hid,sha in source_index.items() if prior_index.get(hid)!=sha}
        removed=set(prior_index)-set(source_index)
        with input_path.open('w') as out:
            for hid in sorted(changed):
                row=conn.execute('SELECT * FROM hands WHERE hand_id=?',(hid,)).fetchone()
                store.require_hand(hid)
                out.write(canonical({k:row[k] for k in ('hand_id','session_id','source_kind','content_hash','canonical','declaration')})+'\n')
    def failure(stage):
        if fail_after==stage:raise RuntimeError('Injected pipeline failure after '+stage)
    ivy=ROOT/'.runtime'/'ivy';ivy.mkdir(parents=True,exist_ok=True)
    event_dir=lake/'spark-events';event_dir.mkdir(exist_ok=True)
    builder=(SparkSession.builder.master('local[2]').appName('PokerMind-local')
        .config('spark.sql.extensions','io.delta.sql.DeltaSparkSessionExtension')
        .config('spark.sql.catalog.spark_catalog','org.apache.spark.sql.delta.catalog.DeltaCatalog')
        .config('spark.jars.ivy',str(ivy)).config('spark.driver.bindAddress','127.0.0.1')
        .config('spark.driver.host','127.0.0.1').config('spark.ui.enabled','false')
        .config('spark.sql.shuffle.partitions',str(partitions)).config('spark.default.parallelism',str(partitions))
        .config('spark.eventLog.enabled','true').config('spark.eventLog.compress','false').config('spark.eventLog.dir',event_dir.resolve().as_uri())
        .config('spark.sql.adaptive.enabled',str(aqe).lower()))
    spark=configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel('ERROR')
    startup=time.perf_counter()-started
    versions={}
    try:
        schema='hand_id string, session_id string, source_kind string, content_hash string, canonical string, declaration string'
        changed_bronze=spark.read.schema(schema).json(str(input_path)).dropDuplicates(['hand_id'])
        invalidated=sorted(changed|removed)
        def retained(table,key):
            version=previous['report']['delta_versions'][table]
            frame=spark.read.format('delta').option('versionAsOf',version).load(str(lake/table))
            return frame.filter(~F.col(key).isin(invalidated)) if invalidated else frame
        bronze=retained('bronze','hand_id').unionByName(changed_bronze) if previous else changed_bronze
        versions['bronze']=merge_snapshot(spark,bronze,lake/'bronze','hand_id')
        failure('bronze')
        obs_schema=StructType([StructField(n,t,True) for n,t in [
            ('decision_id',StringType()),('hand_id',StringType()),('action_index',IntegerType()),('actor',IntegerType()),
            ('street',StringType()),('prefix_length',IntegerType()),('observation',StringType()),('observation_hash',StringType()),
            ('recorded_action',StringType()),('hero',ArrayType(StringType())),('board',ArrayType(StringType())),
            ('features',ArrayType(DoubleType())),('deal_family',StringType()),('split',StringType())]])
        silver=spark.createDataFrame(changed_bronze.repartition(partitions,'hand_id').rdd.mapPartitions(reconstruct_partition),obs_schema)
        # Recorded next action is a separate target column, never in features.
        silver=(silver.join(changed_bronze.select('hand_id','source_kind','session_id'),'hand_id')
            .withColumn('decision_number',F.row_number().over(Window.partitionBy('hand_id').orderBy('action_index'))))
        changed_silver=silver
        if previous:silver=retained('silver','hand_id').unionByName(silver)
        silver=silver.cache()
        if silver.filter(F.col('prefix_length')!=F.col('action_index')).count(): raise ValueError('Action-prefix cutoff failure')
        versions['silver']=merge_snapshot(spark,silver,lake/'silver','decision_id')
        failure('silver')
        # Streaming local reference and ordered Spark output must match, including
        # every observation hash (not just row counts). No raw corpus collect().
        reference_hash=hashlib.sha256();reference_decisions=0
        # Full verification streams the coherent Bronze snapshot, even in
        # incremental mode. This verification cost is included in wall time.
        for row in bronze.orderBy('hand_id').toLocalIterator():
            for observation in reconstruct_partition([row]):
                reference_hash.update((observation[0]+':'+observation[7]+'\n').encode())
                reference_decisions+=1
        spark_hash=hashlib.sha256()
        for row in silver.orderBy('hand_id','action_index').select('decision_id','observation_hash').toLocalIterator():
            spark_hash.update((row.decision_id+':'+row.observation_hash+'\n').encode())
        if reference_hash.hexdigest()!=spark_hash.hexdigest():raise ValueError('Local/Spark observation parity failed')
        gold=(silver.groupBy('source_kind','street','recorded_action').count()
              .withColumn('id',F.concat_ws(':','source_kind','street','recorded_action')))
        versions['gold']=merge_snapshot(spark,gold,lake/'gold','id')
        label_input=changed_silver.filter((F.size('features')==55)&(F.col('source_kind')=='synthetic')).select('decision_id','hand_id','deal_family','split','hero','board','features')
        label_schema=StructType(label_input.schema.fields+[StructField('equity',DoubleType()),StructField('label_samples',IntegerType()),StructField('label_se',DoubleType())])
        labels=spark.createDataFrame(label_input.rdd.mapPartitions(label_partition),label_schema).cache()
        if previous:labels=retained('labels','hand_id').unionByName(labels).cache()
        if labels.groupBy('deal_family').agg(F.countDistinct('split').alias('splits')).filter('splits>1').count():raise ValueError('Split leakage')
        versions['labels']=merge_snapshot(spark,labels,lake/'labels','decision_id')
        failure('labels')
        gold_counts={r['id']:r['count'] for r in gold.select('id','count').collect()}
        raw_label_count=labels.count()
        fingerprints=labels.withColumn('feature_hash',F.sha2(F.to_json('features'),256))
        safe=fingerprints.groupBy('feature_hash').agg(F.countDistinct('split').alias('splits')).filter('splits=1').select('feature_hash')
        labels=fingerprints.join(safe,'feature_hash').drop('feature_hash').cache()
        leakage_excluded=raw_label_count-labels.count()
        label_hash=hashlib.sha256()
        for row in labels.orderBy('hand_id','decision_id').select('decision_id','equity').toLocalIterator():
            label_hash.update(f'{row.decision_id}:{row.equity:.12f}\n'.encode())
        independent={r['split']:r['n'] for r in labels.groupBy('split').agg(F.countDistinct('deal_family').alias('n')).collect()}
        (lake/'query-plan.txt').write_text(gold._jdf.queryExecution().toString())
        partition_rows=[r.asDict() for r in silver.withColumn('partition',F.spark_partition_id()).groupBy('partition').count().collect()]
        code_files=['pipelines/spark/local.py','packages/pokermind_core/engine.py','packages/pokermind_core/equity.py','packages/features/equity.py','packages/simulation/generate.py']
        code_hash=digest({name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in code_files})
        dataset_hash=digest({'input_hash':digest(source_index),'code_hash':code_hash,'eligibility_policy':POLICY_VERSION,'features':'river-counts-v1','labels':'uniform-hu-equity-v1','split':'full-deal-family-hash-v1'})
        dataset=lake/'datasets'/dataset_hash
        if not dataset.exists():
            import uuid
            temporary=dataset.with_name(dataset.name+'.staging-'+uuid.uuid4().hex)
            labels.repartition(2).write.partitionBy('split').parquet(str(temporary))
            os.rename(temporary,dataset)
        files=[{'path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in sorted(dataset.rglob('*.parquet'))]
        counts={r['split']:r['count'] for r in labels.groupBy('split').count().collect()}
        manifest={'schema_version':1,'code_hash':code_hash,'dataset_id':dataset_hash,'source_kind':'synthetic','files':files,'delta_versions':versions,
                  'features_version':'river-counts-v1','label_method':'Exact river vs uniform legal opponent, 990 combinations',
                  'split_policy':'Full deal family (including both players and board), canonicalized over suit permutations: 70/20/10 hash split',
                  'counts':counts,'independent_deal_families':independent,'cross_split_feature_rows_excluded':leakage_excluded,'policy_version':POLICY_VERSION,'created_at':utcnow().isoformat()}
        manifest_path=lake/'datasets'/f'{dataset_hash}.json'
        if manifest_path.exists():
            # Frozen training manifests are never rewritten by later MERGEs.
            existing=json.loads(manifest_path.read_text())
            if existing['files']!=files:raise ValueError('Existing frozen dataset checksum mismatch')
            manifest=existing
        else:manifest_path.write_text(canonical(manifest))
        report={'experiment_id':dataset_hash[:16],'timestamp':utcnow().isoformat(),'scope':'local-smoke, not a distributed benchmark',
                'unique_hands':bronze.count(),'decisions':silver.count(),'label_rows':sum(counts.values()),'input_bytes':input_path.stat().st_size,
                'mode':'incremental' if previous else 'full','extracted_hands':len(changed),'removed_hands':len(removed),
                'source_index_hash':digest(source_index),
                'dataset_manifest_hash':digest(manifest),'worker_count':1,'executor_count':1,'local_threads':2,'partitions':partitions,'aqe':aqe,'partition_rows':partition_rows,'spark_application_id':spark.sparkContext.applicationId,'event_log_dir':str(event_dir.relative_to(root)),
                'runtime_versions':{'python':platform.python_version(),'spark':spark.version,'java':java},
                'hardware':platform.platform(),'startup_seconds':startup,'total_seconds':time.perf_counter()-started,
                'delta_versions':versions,'estimated_cost_usd':0,'actual_cost_usd':0,'cost_status':'local compute only',
                'output_hash':spark_hash.hexdigest(),'local_reference_match':True,'local_reference_decisions':reference_decisions,'label_hash':label_hash.hexdigest(),'gold_counts':gold_counts,
                'limitations':['Incremental extraction/reconstruction with full streaming parity verification; no speedup claim','No real histories in this training profile','No multi-machine execution','No atomic multi-table Delta transaction; manifest published after all writes'],
                'manifest':str(manifest_path.relative_to(root))}
        # Atomic pointer is the publication boundary across independently committed tables.
        temp=lake/'published.tmp';temp.write_text(canonical({'manifest':str(manifest_path),'report':report,'source_index':source_index}));os.replace(temp,lake/'published.json')
        (lake/'pipeline-report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        return manifest_path
    finally:spark.stop()


def run(root:Path, **kwargs):
    import fcntl
    root=root.resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'.pipeline.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another pipeline owns this workspace; wait for it to finish')
        try:return _run(root,**kwargs)
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=ROOT/'.data');parser.add_argument('--count',type=int,default=256)
    parser.add_argument('--seed',type=int,default=46000);parser.add_argument('--partitions',type=int,default=2);parser.add_argument('--no-aqe',action='store_true')
    parser.add_argument('--incremental',action='store_true');parser.add_argument('--fail-after',choices=['bronze','silver','labels'])
    args=parser.parse_args()
    try:run(args.data.resolve(),seed=args.seed,count=args.count,partitions=args.partitions,aqe=not args.no_aqe,incremental=args.incremental,fail_after=args.fail_after)
    except (ValueError,ImportError) as exc:raise SystemExit(f'PokerMind pipeline: {exc}. Install requirements-research.lock and Java 17 before retrying.')
