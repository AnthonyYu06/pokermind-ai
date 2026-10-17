"""Fresh, bounded research; previous test families are excluded before splitting."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from packages.pokermind_core.contracts import canonical, digest


def prior_families():
    import pyarrow.parquet as pq
    from packages.features.equity import deal_family
    result = set(); sources = []
    for root in [ROOT / '.data', ROOT / '.data/research-local-v2/equity']:
        pointer = root / 'research.json'
        if not pointer.exists():
            continue
        report = json.loads((root / json.loads(pointer.read_text())['report']).read_text())
        manifest = json.loads((root / 'lake/datasets' / (report['dataset_id'] + '.json')).read_text())
        sources.append(manifest['dataset_id'])
        for f in manifest['files']:
            for batch in pq.ParquetFile(root / f['path']).iter_batches(columns=['hero', 'board'], batch_size=256):
                for row in batch.to_pylist(): result.add(deal_family(row['hero'], row['board']))
    return result, sources


def worker(root, smoke):
    from pipelines.spark.workload import local_session, materialize, score
    from training.river import train
    from training.limits import preflight
    preflight(root)
    config = json.loads((ROOT / 'configs/river-research-v3.json').read_text())
    if smoke:
        config.update(states=256, seeds=[101], epochs=3, models=['structural'], smoke=True)
    root.mkdir(parents=True, exist_ok=True)
    files = ['configs/river-research-v3.json', 'packages/models/river.py', 'training/river.py', 'pipelines/spark/workload.py', 'scripts/run_river_research.py']
    source = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files}
    (root / 'pre-run-protocol.json').write_text(json.dumps({'protocol': config, 'source_sha256': source, 'created_before_data': True}, indent=2))
    previous, sources = prior_families()
    (root / 'prior-family-audit.json').write_text(canonical({'datasets': sources, 'canonical_visible_families': len(previous), 'hash': digest(sorted(previous))}))
    spark = local_session(root / 'spark-events')
    try:
        manifest = materialize(spark, str(root / 'lake'), root / 'dataset', count=config['states'], seed=config['generation_seed'], mode='river', exclude_families=previous)
    finally: spark.stop()
    report = train(manifest, root / 'models', config, tracking_uri='sqlite:///' + str(root / 'mlflow.sqlite3'))
    spark = local_session(root / 'spark-events')
    try: score(spark, manifest, report, str(root / 'lake'), root / 'scoring.json')
    finally: spark.stop()
    print(canonical({'manifest': str(manifest), 'report': str(report), 'score': str(root / 'scoring.json'), 'cloud_enabled': False}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, default=ROOT / '.data/research-river-v3'); p.add_argument('--smoke', action='store_true'); p.add_argument('--worker', action='store_true')
    a = p.parse_args(); root = a.data.resolve()
    if (root / 'pre-run-protocol.json').exists():
        p.error('This research workspace already has a protocol. Choose a new --data directory; existing evidence is immutable.')
    if a.worker:
        worker(root, a.smoke)
    else:
        from scripts.run_local_research import execute
        root.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--data', str(root)] + (['--smoke'] if a.smoke else [])
        execute(command, root / 'run.log', 3600)
