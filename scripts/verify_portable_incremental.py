"""Compare overlap/incremental/full and measure compaction on isolated Delta tables."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from pipelines.spark.workload import local_session, materialize
from pipelines.spark.maintenance import compact, vacuum_allowed


def run(root):
    root = root.resolve(); root.mkdir(parents=True, exist_ok=False); spark = local_session(root / 'events')
    try:
        first = materialize(spark, str(root / 'lake'), root / 'initial', count=96, seed=3946000, partitions=4)
        overlap = materialize(spark, str(root / 'lake'), root / 'overlap', count=96, seed=3946000, partitions=4, previous_manifest=first)
        incremental = materialize(spark, str(root / 'lake'), root / 'incremental', count=128, seed=3946000, partitions=4, previous_manifest=overlap)
        full = materialize(spark, str(root / 'lake'), root / 'full', count=128, seed=3946000, partitions=4)
        a, b, c, d = [json.loads(p.read_text()) for p in [first, overlap, incremental, full]]
        assert a['output_identity'] == b['output_identity'] and a['dataset_id'] == b['dataset_id']
        assert c['output_identity'] == d['output_identity'] and c['dataset_id'] == d['dataset_id']
        table = c['tables']['labels']
        # Deliberately fragment an isolated copy so compaction performs real work.
        fragmented = str(root / 'fragmented-labels')
        spark.read.format('delta').option('versionAsOf', table['version']).load(table['uri']).repartition(8).write.format('delta').save(fragmented)
        maintenance = compact(spark, fragmented, [0])
        assert maintenance['before']['numFiles'] > maintenance['after']['numFiles']
        try: vacuum_allowed(table['uri'], [c])
        except ValueError: pass
        else: raise AssertionError('Pinned snapshot must forbid VACUUM')
        result = {'status': 'passed', 'initial_hands': a['unique_hands'], 'overlap_hands': b['unique_hands'], 'incremental_hands': c['unique_hands'],
                  'full_hands': d['unique_hands'], 'full_incremental_identity_match': True, 'duplicate_overlap_identity_match': True,
                  'compaction': maintenance, 'scope': 'local Spark only', 'spark': spark.version, 'python': sys.version.split()[0]}
        (root / 'verification.json').write_text(json.dumps(result, indent=2)); print(json.dumps(result, indent=2))
    finally: spark.stop()


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, required=True); run(p.parse_args().data)
