"""Use the frozen v3 model as a labeled companion in eligible historical review.

The scoring dataset is separate from the training dataset. Exact equity stays
the reference; this offline batch entrypoint never accepts a current-state form.
"""
import argparse
import json
from pathlib import Path
from packages.pokermind_core.contracts import canonical, digest
from packages.pokermind_core.eligibility import utcnow
from pipelines.spark.workload import local_session, score_rows, structural_rows
from training.river import checksum
from training.equity import checked_manifest
from services.api.store import Store


def run(root, report_path):
    root, report_path = Path(root).resolve(), Path(report_path).resolve()
    report = json.loads(report_path.read_text()); spec = report['selected']
    if report['version'] != 'river-structure-v1' or not all(report['promotion_gates'].get(spec['model'], {}).values()) or not report['promotion_gates'].get(spec['model']):
        raise ValueError('Selected model has not passed its declared synthetic quality gates')
    weights = report_path.parent / spec['model_path']
    if checksum(weights) != spec['sha256']:
        raise ValueError('Artifact checksum mismatch')
    import shutil
    retained = root / 'models/river-v3' / (spec['sha256'] + '.pt'); retained.parent.mkdir(parents=True, exist_ok=True)
    if retained.exists() and checksum(retained) != spec['sha256']:
        raise ValueError('Retained model artifact is corrupt')
    if not retained.exists(): shutil.copyfile(weights, retained)
    (retained.parent / (spec['sha256'] + '.json')).write_text(canonical({'report_sha256': checksum(report_path), 'selected': spec, 'training_dataset_id': report['dataset_id']}))
    old = json.loads((root / json.loads((root / 'research.json').read_text())['report']).read_text())
    manifest = checked_manifest(root / 'lake/datasets' / (old['dataset_id'] + '.json'), root)
    spark = local_session(root / 'lake/river-scoring-events')
    try:
        versions = manifest['delta_versions']
        labels = spark.read.format('delta').option('versionAsOf', versions['labels']).load(str(root / 'lake/labels'))
        silver = spark.read.format('delta').option('versionAsOf', versions['silver']).load(str(root / 'lake/silver'))
        joined = labels.join(silver.select('decision_id', 'observation_hash'), 'decision_id').select('decision_id', 'observation_hash', 'hero', 'board', 'features', 'equity')
        encoded = spark.createDataFrame(joined.rdd.mapPartitions(structural_rows), joined.schema)
        model = spark.sparkContext.broadcast(weights.read_bytes())
        try:
            scored = spark.createDataFrame(encoded.rdd.mapPartitions(lambda rows: score_rows(rows, model, spec)),
                                          'decision_id string, observation_hash string, reference double, prediction double, artifact_hash string')
            uri = root / 'lake' / ('river-scores-' + spec['sha256'][:16] + '-' + manifest['dataset_id'][:16])
            if not uri.exists(): scored.write.format('delta').mode('errorifexists').save(str(uri))
            scored = spark.read.format('delta').option('versionAsOf', 0).load(str(uri))
            store = Store(root); count = 0
            with store.connect() as conn:
                for row in scored.toLocalIterator():
                    _, hand, obs = store.decision(row.decision_id)
                    if obs['observation_hash'] != row.observation_hash:
                        raise ValueError('Historical observation changed')
                    value = {'status': 'available', 'equity': row.reference, 'ci95': [row.reference, row.reference], 'samples': 990,
                             'method': 'exact enumeration', 'model_version': 'uniform-hu-equity-v1', 'range': 'One opponent, uniform legal range',
                             'assumptions': 'Exact showdown reference; neural companion passed synthetic river gates. No action EV or real-player validation.',
                             'observation_hash': row.observation_hash, 'data_hash': digest(hand), 'dataset_id': manifest['dataset_id'],
                             'research': {'prediction': row.prediction, 'status': 'research-only', 'model_version': spec['sha256'][:16], 'run_id': spec['run_id'],
                                          'training_dataset_id': report['dataset_id'], 'synthetic_gates_passed': True}}
                    conn.execute('INSERT OR REPLACE INTO analysis_results VALUES (?,?,?,?,?)',
                                 (row.decision_id, value['model_version'], value['data_hash'], utcnow().isoformat(), canonical(value)))
                    count += 1
            result = {'rows': count, 'training_dataset_id': report['dataset_id'], 'scoring_dataset_id': manifest['dataset_id'],
                      'model_sha256': spec['sha256'], 'exact_reference_retained': True, 'scope': 'local Spark; eligible synthetic historical decisions'}
            (root / 'lake/river-scoring-report.json').write_text(json.dumps(result, indent=2)); print(json.dumps(result, indent=2)); return result
        finally: model.destroy()
    finally: spark.stop()


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, default=Path('.data')); p.add_argument('--report', type=Path, default=Path('.data/research-river-v3/models/report.json'))
    a = p.parse_args(); run(a.data, a.report)
