"""Identical Spark transformations on local Spark and classic Databricks jobs.

All table paths are immutable per attempt. A completed manifest is the commit
boundary; failed attempts never overwrite a published snapshot. No cloud client
or cluster is created here. The guarded launcher supplies an existing session.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import socket
import time
import uuid

from packages.pokermind_core.contracts import canonical, digest
from pipelines.spark.local import reconstruct_partition, label_partition

BRONZE_SCHEMA = 'hand_id string, session_id string, source_kind string, content_hash string, canonical string, declaration string'
OBS_SCHEMA = ('decision_id string, hand_id string, action_index int, actor int, street string, prefix_length int, observation string, '
              'observation_hash string, recorded_action string, hero array<string>, board array<string>, features array<double>, deal_family string, split string')
RIVER_SCHEMA = ('decision_id string, hand_id string, deal_family string, split string, hero array<string>, board array<string>, '
                'features array<double>, observation_hash string')


def generate_hands(rows, seed):
    from packages.simulation.generate import generate_hand
    for row in rows:
        i = int(row.id); hand = generate_hand(seed + i, table_size=2, session=f'batch-{seed}-{i // 32}')
        hid = digest(['synthetic', hand.session_id, hand.source_hand_id])[:24]
        yield hid, hand.session_id, 'synthetic', digest(hand), canonical(hand), None


def generate_rivers(rows, seed):
    from packages.pokermind_core.cards import DECK
    from packages.features.equity import deal_family
    from packages.models.river import encode_structure
    for row in rows:
        cards = random.Random(seed + int(row.id)).sample(DECK, 7)
        hero, board = cards[:2], cards[2:]
        family = deal_family(hero, board)
        split = 'train' if int(family[:8], 16) % 10 < 7 else 'validation' if int(family[:8], 16) % 10 < 9 else 'test'
        yield 'river:' + family, family, family, split, hero, board, encode_structure(hero, board), digest({'hero': hero, 'board': board})


def structural_rows(rows):
    from packages.models.river import encode_structure
    for row in rows:
        values = row.asDict(); values['features'] = encode_structure(row.hero, row.board)
        yield tuple(values[k] for k in row.__fields__)


def worker_identity(rows):
    from pyspark import TaskContext
    count = sum(1 for _ in rows); context = TaskContext.get()
    yield socket.gethostname(), os.environ.get('SPARK_EXECUTOR_ID', 'unavailable'), context.partitionId(), context.attemptNumber(), count


def data_identity(frame, columns):
    """Order-independent bounded aggregate: no raw corpus on the driver."""
    from pyspark.sql import functions as F
    hashed = frame.select(F.sha2(F.to_json(F.struct(*columns)), 256).alias('h'))
    # Four 64-bit chunks sum in decimal128; include count/min/max. This is a
    # reproducibility diagnostic, not a cryptographic multiset commitment.
    row = hashed.agg(F.count('*').alias('n'), F.min('h').alias('min'), F.max('h').alias('max'),
                     *[F.sum(F.conv(F.substring('h', i * 16 + 1, 16), 16, 10).cast('decimal(38,0)')).cast('string').alias(f'sum{i}') for i in range(4)]).first().asDict()
    return row


def materialize(spark, lake, artifacts, *, count=512, seed=946000, partitions=2, mode='hands', exclude_families=(), fail_after=None, previous_manifest=None):
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F, Window
    from pyspark.sql.types import StructField, DoubleType, IntegerType, StructType
    from packages.models.river import VERSION
    from training.river import checksum
    if not 1 <= count <= 10_000_000 or not 1 <= partitions <= 512 or mode not in ('hands', 'river'):
        raise ValueError('Unsupported workload bounds')
    artifacts = Path(artifacts).resolve(); artifacts.mkdir(parents=True, exist_ok=False)
    source_files = [Path(__file__), Path(__file__).parents[2] / 'packages/models/river.py', Path(__file__).parents[2] / 'packages/pokermind_core/engine.py',
                    Path(__file__).parents[2] / 'packages/pokermind_core/equity.py', Path(__file__).parents[2] / 'packages/features/equity.py']
    source_hashes = {str(p.relative_to(Path(__file__).parents[2])): checksum(p) for p in source_files}
    attempt = artifacts.name
    table_root = lake.rstrip('/') + '/attempts/' + attempt
    started = time.monotonic(); tables = {}
    previous = json.loads(Path(previous_manifest).read_text()) if previous_manifest else None
    if previous and (mode != 'hands' or previous['mode'] != 'hands' or previous['source_kind'] != 'synthetic' or previous['features_version'] != VERSION):
        raise ValueError('Incremental source contract mismatch')
    def prior(name):
        table = previous['tables'][name]
        return spark.read.format('delta').option('versionAsOf', table['version']).load(table['uri'])
    def write(name, frame):
        uri = table_root + '/' + name
        frame.write.format('delta').mode('errorifexists').save(uri)
        version = int(DeltaTable.forPath(spark, uri).history(1).select('version').first()[0])
        tables[name] = {'uri': uri, 'version': version}
        if fail_after == name:
            raise RuntimeError('Injected failure after ' + name)
        return spark.read.format('delta').option('versionAsOf', version).load(uri)
    work = spark.range(count, numPartitions=partitions)
    if mode == 'hands':
        raw = spark.createDataFrame(work.rdd.mapPartitions(lambda rows: generate_hands(rows, seed)), BRONZE_SCHEMA)
        raw_uri = table_root + '/raw'
        raw.write.mode('errorifexists').json(raw_uri)
        incoming = spark.read.schema(BRONZE_SCHEMA).json(raw_uri).dropDuplicates(['hand_id'])
        if previous:
            old = prior('bronze')
            conflicts = incoming.alias('n').join(old.alias('o'), 'hand_id').filter(F.col('n.content_hash') != F.col('o.content_hash'))
            if conflicts.limit(1).count():
                raise ValueError('Conflicting revision: new attempt quarantined; prior snapshot unchanged')
            changed = incoming.join(old.select('hand_id'), 'hand_id', 'left_anti')
            bronze = write('bronze', old.unionByName(changed))
        else:
            bronze = write('bronze', incoming); changed = bronze
        observations = spark.createDataFrame(changed.repartition(partitions, 'hand_id').rdd.mapPartitions(reconstruct_partition), OBS_SCHEMA)
        changed_silver = observations.join(changed.select('hand_id', 'session_id', 'source_kind'), 'hand_id').withColumn(
            'decision_number', F.row_number().over(Window.partitionBy('hand_id').orderBy('action_index')))
        silver = write('silver', prior('silver').unionByName(changed_silver) if previous else changed_silver)
        if silver.filter('prefix_length != action_index').limit(1).count():
            raise ValueError('Observation cutoff violation')
        gold = write('gold', silver.groupBy('source_kind', 'street', 'recorded_action').count())
        label_input = changed_silver.filter(F.size('features') == 55).select('decision_id', 'hand_id', 'deal_family', 'split', 'hero', 'board', 'features', 'observation_hash')
        label_input = spark.createDataFrame(label_input.rdd.mapPartitions(structural_rows), label_input.schema)
    else:
        raw = spark.createDataFrame(work.rdd.mapPartitions(lambda rows: generate_rivers(rows, seed)), RIVER_SCHEMA)
        raw_uri = table_root + '/raw'; raw.write.mode('errorifexists').json(raw_uri)
        bronze = write('bronze', spark.read.schema(RIVER_SCHEMA).json(raw_uri).dropDuplicates(['deal_family']))
        silver = write('silver', bronze)
        gold = write('gold', silver.groupBy('split').count())
        label_input = silver
    if exclude_families:
        # Only small fingerprint lists are broadcast, never source histories.
        excluded = spark.createDataFrame([(x,) for x in sorted(set(exclude_families))], 'deal_family string')
        label_input = label_input.join(F.broadcast(excluded), 'deal_family', 'left_anti')
    fingerprints = label_input.withColumn('feature_hash', F.sha2(F.to_json('features'), 256))
    safe = fingerprints.groupBy('feature_hash').agg(F.countDistinct('split').alias('n')).filter('n=1').select('feature_hash')
    label_input = fingerprints.join(safe, 'feature_hash').drop('feature_hash').repartition(partitions, 'hand_id')
    schema = StructType(label_input.schema.fields + [StructField('equity', DoubleType()), StructField('label_samples', IntegerType()), StructField('label_se', DoubleType())])
    labels = spark.createDataFrame(label_input.rdd.mapPartitions(label_partition), schema)
    if previous:
        labels = prior('labels').unionByName(labels)
        # New overlaps can invalidate an old exact-feature family; filter across
        # the entire next snapshot, while the old snapshot stays immutable.
        fingerprints = labels.withColumn('feature_hash', F.sha2(F.to_json('features'), 256))
        safe = fingerprints.groupBy('feature_hash').agg(F.countDistinct('split').alias('n')).filter('n=1').select('feature_hash')
        labels = fingerprints.join(safe, 'feature_hash').drop('feature_hash')
    labels = write('labels', labels)
    if labels.groupBy('deal_family').agg(F.countDistinct('split').alias('n')).filter('n>1').count():
        raise ValueError('Family leakage')
    shard_root = artifacts / 'shards'
    labels.repartition(partitions, 'split').write.partitionBy('split').parquet(str(shard_root))
    files = [{'path': str(p.relative_to(artifacts)), 'split': p.parent.name.split('=', 1)[1], 'sha256': checksum(p), 'bytes': p.stat().st_size}
             for p in sorted(shard_root.rglob('*.parquet'))]
    counts = {r['split']: r['count'] for r in labels.groupBy('split').count().collect()}
    independent = {r['split']: r['n'] for r in labels.groupBy('split').agg(F.countDistinct('deal_family').alias('n')).collect()}
    workers = [r.asDict() for r in spark.createDataFrame(labels.rdd.mapPartitions(worker_identity), 'host string, executor string, partition int, attempt int, rows long').collect()]
    identity = data_identity(labels, ['decision_id', 'observation_hash', 'equity', 'features', 'split'])
    dataset_id = digest({'mode': mode, 'seed': seed, 'count': count, 'features': VERSION, 'identity': identity})
    manifest = {'schema_version': 2, 'dataset_id': dataset_id, 'source_kind': 'synthetic', 'mode': mode, 'features_version': VERSION,
                'seed': seed, 'generated_work_ids': count, 'unique_hands': bronze.count(), 'decisions': silver.count(), 'counts': counts,
                'independent_deal_families': independent, 'files': files, 'tables': tables, 'raw_uri': raw_uri,
                'label_method': 'exact river vs uniform legal opponent, 990 combinations', 'split_policy': '70/20/10 suit-canonical family hash', 'code_sha256': source_hashes,
                'excluded_prior_families': len(exclude_families), 'output_identity': identity, 'worker_evidence': workers,
                'spark_version': spark.version, 'spark_master': spark.sparkContext.master, 'spark_application_id': spark.sparkContext.applicationId,
                'seconds': time.monotonic() - started, 'partition_count': partitions, 'build_mode': 'incremental' if previous else 'full',
                'previous_dataset_id': previous['dataset_id'] if previous else None, 'scope': 'local' if spark.sparkContext.master.startswith('local') else 'cluster',
                'publication': 'Manifest commits a coherent set of pinned tables; table writes are not a multi-table transaction.'}
    (artifacts / 'query-plan.txt').write_text(gold._jdf.queryExecution().toString())
    path = artifacts / 'dataset.json'; path.write_text(json.dumps(manifest, indent=2))
    return path


def score_rows(rows, weights, spec):
    import io
    import torch
    from packages.models.river import RiverNet
    payload = weights.value
    if hashlib.sha256(payload).hexdigest() != spec['sha256']:
        raise ValueError('Broadcast model checksum mismatch')
    torch.set_num_threads(1)
    model = RiverNet(spec['structural'], spec['width'])
    model.load_state_dict(torch.load(io.BytesIO(payload), map_location='cpu', weights_only=True)); model.eval()
    # Bounded batches, one model load per partition (not per row).
    batch = []
    def infer(items):
        with torch.inference_mode():
            predictions = model(torch.tensor([r.features for r in items], dtype=torch.float32)).tolist()
        for row, prediction in zip(items, predictions):
            yield row.decision_id, row.observation_hash, float(row.equity), float(prediction), spec['sha256']
    for row in rows:
        batch.append(row)
        if len(batch) == 256:
            yield from infer(batch); batch.clear()
    if batch:
        yield from infer(batch)


def score(spark, manifest_path, report_path, lake, output):
    from training.river import checksum, checked_dataset
    manifest = checked_dataset(manifest_path); report_path = Path(report_path); report = json.loads(report_path.read_text())
    if report['manifest_hash'] != digest(manifest):
        raise ValueError('Scoring manifest mismatch')
    spec = report['selected']; model_path = report_path.parent / spec['model_path']
    if checksum(model_path) != spec['sha256']:
        raise ValueError('Model checksum mismatch')
    table = manifest['tables']['labels']
    labels = spark.read.format('delta').option('versionAsOf', table['version']).load(table['uri'])
    weights = spark.sparkContext.broadcast(model_path.read_bytes())
    uri = lake.rstrip('/') + '/scores/' + uuid.uuid4().hex
    try:
        scored = spark.createDataFrame(labels.rdd.mapPartitions(lambda rows: score_rows(rows, weights, spec)),
                                      'decision_id string, observation_hash string, reference double, prediction double, artifact_hash string')
        scored.write.format('delta').mode('errorifexists').save(uri)
        frame = spark.read.format('delta').option('versionAsOf', 0).load(uri)
        result = {'dataset_id': manifest['dataset_id'], 'manifest_hash': digest(manifest), 'model_sha256': spec['sha256'],
                  'mlflow_run_id': spec['run_id'], 'report_sha256': checksum(report_path), 'scores': {'uri': uri, 'version': 0},
                  'rows': frame.count(), 'output_identity': data_identity(frame, frame.columns), 'status': 'research-only', 'reference_retained': True,
                  'spark_application_id': spark.sparkContext.applicationId}
        Path(output).write_text(json.dumps(result, indent=2))
        return result
    finally:
        weights.destroy()


def local_session(event_dir, partitions=2):
    from pipelines.spark.local import configure_java, ROOT
    configure_java()
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession
    event_dir = Path(event_dir).resolve(); event_dir.mkdir(parents=True, exist_ok=True)
    builder = (SparkSession.builder.master('local[2]').appName('PokerMind-portable-batch')
               .config('spark.sql.extensions', 'io.delta.sql.DeltaSparkSessionExtension')
               .config('spark.sql.catalog.spark_catalog', 'org.apache.spark.sql.delta.catalog.DeltaCatalog')
               .config('spark.jars.ivy', str(ROOT / '.runtime/ivy')).config('spark.driver.bindAddress', '127.0.0.1')
               .config('spark.driver.host', '127.0.0.1').config('spark.ui.enabled', 'false')
               .config('spark.sql.shuffle.partitions', str(partitions)).config('spark.sql.adaptive.enabled', 'true')
               .config('spark.eventLog.enabled', 'true').config('spark.eventLog.compress', 'false').config('spark.eventLog.dir', event_dir.as_uri()))
    spark = configure_spark_with_delta_pip(builder).getOrCreate(); spark.sparkContext.setLogLevel('ERROR')
    return spark
