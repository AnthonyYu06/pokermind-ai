"""Partition-level batch inference; unpromoted predictions accompany exact equity."""
import json
import os
from pathlib import Path

from packages.pokermind_core.contracts import canonical,digest
from packages.pokermind_core.eligibility import utcnow
from services.api.store import Store
from .local import ROOT,configure_java,merge_snapshot


def score_partition(rows,path,checksum):
    import torch
    from training.equity import load_model
    torch.set_num_threads(1)
    model=load_model(Path(path),checksum)  # One artifact load per partition.
    for row in rows:
        with torch.no_grad():pred=float(model(torch.tensor([row.features],dtype=torch.float32))[0])
        yield (row.decision_id,row.observation_hash,float(row.equity),pred,checksum)


def run(root):
    configure_java()
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession
    from pyspark.sql.types import StructType,StructField,StringType,DoubleType
    from training.equity import checked_manifest
    pointer=json.loads((root/'research.json').read_text());report=json.loads((root/pointer['report']).read_text())
    # Seed chosen on validation only, never on final test performance.
    selected=min(report['runs'],key=lambda r:r['best_validation_mae'])
    manifest=checked_manifest(root/'lake/datasets'/f'{report["dataset_id"]}.json',root)
    builder=(SparkSession.builder.master('local[2]').appName('PokerMind-batch-score')
        .config('spark.sql.extensions','io.delta.sql.DeltaSparkSessionExtension')
        .config('spark.sql.catalog.spark_catalog','org.apache.spark.sql.delta.catalog.DeltaCatalog')
        .config('spark.jars.ivy',str(ROOT/'.runtime/ivy')).config('spark.driver.bindAddress','127.0.0.1')
        .config('spark.driver.host','127.0.0.1').config('spark.ui.enabled','false').config('spark.sql.shuffle.partitions','2'))
    spark=configure_spark_with_delta_pip(builder).getOrCreate();spark.sparkContext.setLogLevel('ERROR')
    try:
        versions=manifest['delta_versions']
        labels=spark.read.format('delta').option('versionAsOf',versions['labels']).load(str(root/'lake/labels'))
        silver=spark.read.format('delta').option('versionAsOf',versions['silver']).load(str(root/'lake/silver'))
        joined=labels.join(silver.select('decision_id','observation_hash'),'decision_id').select('decision_id','observation_hash','features','equity')
        schema=StructType([StructField(n,t) for n,t in [('decision_id',StringType()),('observation_hash',StringType()),('reference',DoubleType()),('prediction',DoubleType()),('artifact_hash',StringType())]])
        model_path=str(root/selected['model_path']);checksum=selected['sha256']
        scored=spark.createDataFrame(joined.repartition(2).rdd.mapPartitions(lambda rows:score_partition(rows,model_path,checksum)),schema).cache()
        version=merge_snapshot(spark,scored,root/'lake/research_scores','decision_id')
        store=Store(root);published=0
        with store.connect() as conn:
            for row in scored.toLocalIterator():
                _,hand,obs=store.decision(row.decision_id)
                if obs['observation_hash']!=row.observation_hash:raise ValueError('Stale scored observation')
                result={'status':'available','equity':row.reference,'ci95':[row.reference,row.reference],'samples':990,
                        'method':'exact enumeration','model_version':'uniform-hu-equity-v1','range':'One opponent, uniform legal range',
                        'assumptions':'Showdown pot share only; no action EV. Neural prediction below is unpromoted research.',
                        'observation_hash':row.observation_hash,'data_hash':digest(hand),'dataset_id':report['dataset_id'],
                        'research':{'prediction':row.prediction,'status':'research-only','model_version':row.artifact_hash[:16],'run_id':selected['run_id']}}
                conn.execute('INSERT OR REPLACE INTO analysis_results VALUES (?,?,?,?,?)',(row.decision_id,result['model_version'],result['data_hash'],utcnow().isoformat(),canonical(result)))
                published+=1
        output={'timestamp':utcnow().isoformat(),'rows':published,'delta_score_version':version,'dataset_id':report['dataset_id'],
                'artifact_hash':checksum,'mlflow_run_id':selected['run_id'],'promotion':'research-only; exact reference retained','execution':'local Spark, one machine'}
        (root/'lake/scoring-report.json').write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))
        return output
    finally:spark.stop()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=ROOT/'.data')
    run(parser.parse_args().data.resolve())
