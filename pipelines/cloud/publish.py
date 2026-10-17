"""Build an app-consumable backup from a coherent synthetic batch snapshot."""
import json
from pathlib import Path
import tempfile
import zipfile
from packages.pokermind_core.contracts import canonical, digest
from packages.pokermind_core.eligibility import utcnow
from services.api.store import Store
from training.river import checksum


def export_snapshot(spark, manifest_path, scoring_path, destination, report_path=None):
    manifest = json.loads(Path(manifest_path).read_text()); scored = json.loads(Path(scoring_path).read_text())
    if manifest['source_kind'] != 'synthetic' or manifest['mode'] != 'hands' or digest(manifest) != scored['manifest_hash']:
        raise ValueError('Snapshot requires a matching synthetic historical-hand dataset')
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Snapshot output must be new')
    # SQLite runs on driver-local disk, never on a FUSE/S3 filesystem.
    with tempfile.TemporaryDirectory(prefix='pokermind-publication-') as folder:
        store = Store(Path(folder) / 'app'); table = manifest['tables']['bronze']
        bronze = spark.read.format('delta').option('versionAsOf', table['version']).load(table['uri'])
        buffer = []; batch = 0
        def ingest():
            nonlocal batch
            preview = store._stage(('\n'.join(buffer) + '\n').encode(), f'batch-{batch}.jsonl', 'synthetic')
            store.commit(preview['file_id']); buffer.clear(); batch += 1
        for row in bronze.select('canonical').toLocalIterator():
            buffer.append(row.canonical)
            if len(buffer) == 128: ingest()
        if buffer: ingest()
        scores = spark.read.format('delta').option('versionAsOf', scored['scores']['version']).load(scored['scores']['uri'])
        count = 0
        with store.connect() as conn:
            for row in scores.toLocalIterator():
                _, hand, observation = store.decision(row.decision_id)
                if observation['observation_hash'] != row.observation_hash:
                    raise ValueError('Published observation hash mismatch')
                value = {'status': 'available', 'equity': row.reference, 'ci95': [row.reference, row.reference], 'samples': 990,
                         'method': 'exact enumeration', 'model_version': 'uniform-hu-equity-v1', 'range': 'One opponent, uniform legal range',
                         'assumptions': 'Showdown pot share only; neural model is unpromoted research. No action EV.',
                         'observation_hash': row.observation_hash, 'data_hash': digest(hand), 'dataset_id': manifest['dataset_id'],
                         'research': {'prediction': row.prediction, 'status': 'research-only', 'model_version': row.artifact_hash[:16], 'run_id': scored['mlflow_run_id']}}
                conn.execute('INSERT OR REPLACE INTO analysis_results VALUES (?,?,?,?,?)', (row.decision_id, value['model_version'], value['data_hash'], utcnow().isoformat(), canonical(value)))
                count += 1
        if count != scored['rows']:
            raise ValueError('Snapshot score count mismatch')
        if report_path is not None:
            from packages.models.evidence import river_summary
            report_path = Path(report_path); report = json.loads(report_path.read_text())
            if checksum(report_path) != scored['report_sha256']:
                raise ValueError('Published model report checksum mismatch')
            summary = river_summary(manifest, report, scored, checksum(report_path),
                                    execution='Spark batch publication; ' + manifest['scope'] + ' execution; serving is offline')
            evidence = store.root / 'evidence'; evidence.mkdir(exist_ok=True)
            name = 'river-batch-' + digest(summary)[:16] + '.json'
            (evidence / name).write_text(canonical(summary))
            (evidence / ('report-' + checksum(report_path) + '.json')).write_text(report_path.read_text())
            (store.root / 'river-research.json').write_text(canonical({'path': 'evidence/' + name, 'sha256': checksum(evidence / name)}))
        backup = Path(folder) / 'backup'; store.backup(backup)
        with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            for path in sorted(backup.rglob('*')):
                if path.is_file(): archive.write(path, path.relative_to(backup))
    result = {'snapshot': destination.name, 'sha256': checksum(destination), 'bytes': destination.stat().st_size, 'hands': manifest['unique_hands'],
              'scores': count, 'dataset_id': manifest['dataset_id'], 'model_sha256': scored['model_sha256'], 'reference_retained': True}
    return result
