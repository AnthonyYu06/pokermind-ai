"""Exercise all Databricks task bodies on local Spark, including app restore.

This is portability/recovery evidence, not proof of a Databricks or AWS run.
"""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))


def run(root, registry_smoke=False):
    from pipelines.cloud.job import stage
    from pipelines.spark.workload import local_session, materialize
    from services.api.snapshots import install
    from services.api.store import Store
    from training.river import checksum
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=False)
    plan = json.loads((ROOT / 'configs/cloud-run.example.json').read_text())
    plan.update(lake_uri=str(root / 'lake'), count=256, seed=2946000, partitions=2, tracking_uri='sqlite:///' + str(root / 'mlflow.sqlite3'),
                experiment='pokermind-portable-verification', registered_model='pokermind-portability' if registry_smoke else None)
    plan['research'].update(epochs=3, seeds=[101], models=['structural'])
    spark = local_session(root / 'events')
    results = {}; start = time.monotonic()
    try:
        for name in ['materialize', 'train', 'score', 'publish']:
            results[name] = stage(spark, root / 'artifacts', plan, name)
            assert stage(spark, root / 'artifacts', plan, name) == results[name], 'Retry did not reuse completed stage'
        publication = json.loads((root / 'artifacts' / results['publish']['path']).read_text())
        archive = root / 'artifacts' / publication['snapshot']
        assert checksum(archive) == publication['sha256']
        publication_path = root / 'artifacts' / results['publish']['path']
        install(publication_path, archive, root / 'restored', checksum(publication_path))
        assert install(publication_path, archive, root / 'restored', checksum(publication_path), if_installed=True)['status'] == 'already-installed'
        store = Store(root / 'restored')
        with store.connect() as conn:
            hand_count = conn.execute('SELECT COUNT(*) FROM hands').fetchone()[0]
            score_count = conn.execute('SELECT COUNT(*) FROM analysis_results').fetchone()[0]
            sample = conn.execute('SELECT decision_id,result FROM analysis_results LIMIT 1').fetchone()
        assert hand_count == publication['hands'] and score_count == publication['scores'] and score_count > 0
        from fastapi.testclient import TestClient
        from services.api.app import create_app
        with TestClient(create_app(root / 'restored'), base_url='http://localhost') as client:
            report = client.get('/api/research/river')
            assert report.status_code == 200 and report.json()['status'] == 'measured'
            assert report.json()['scored_rows'] == score_count
            assert all(not group['gates']['support'] for group in report.json()['groups'])
        if sample:
            _, _, observation = store.decision(sample['decision_id'])
            assert json.loads(sample['result'])['observation_hash'] == observation['observation_hash']
        # Partial writes never produce a dataset or publication manifest.
        try:
            materialize(spark, plan['lake_uri'], root / 'failure', count=12, seed=9, fail_after='silver')
        except RuntimeError as exc:
            assert 'Injected failure' in str(exc)
        else: raise AssertionError('Failure injection did not occur')
        assert not (root / 'failure/dataset.json').exists()
        assert stage(spark, root / 'artifacts', plan, 'publish') == results['publish']
        evidence = {'status': 'passed', 'scope': 'local task-body portability; no AWS/Databricks execution', 'stages': results,
                    'restored_hands': hand_count, 'restored_scores': score_count, 'retry_reuses_artifacts': True, 'failed_attempt_not_published': True,
                    'spark': spark.version, 'python': sys.version.split()[0], 'local_registry_tested': registry_smoke,
                    'seconds': time.monotonic() - start, 'cloud_enabled': False,
                    'pinned_snapshot_install': True, 'research_report_in_restored_app': True, 'insufficient_smoke_support_stays_failed': True}
        (root / 'verification.json').write_text(json.dumps(evidence, indent=2)); print(json.dumps(evidence, indent=2))
    finally: spark.stop()


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, required=True); p.add_argument('--registry-smoke', action='store_true')
    a = p.parse_args(); run(a.data, a.registry_smoke)
