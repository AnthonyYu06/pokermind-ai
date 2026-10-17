"""Install the three checked river networks for opt-in synthetic practice."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.models.river_inference import VERSION, checked_bytes, manifest
from packages.pokermind_core.contracts import canonical


def prepare(report_path, destination):
    report_path = Path(report_path).resolve(); destination = Path(destination).resolve()
    data = report_path.read_bytes(); report = json.loads(data)
    if report['version'] != 'river-structure-v1': raise ValueError('Unsupported river research report')
    gates = report['promotion_gates']['structural']
    if any(gates.get(name) is not True for name in ['support', 'quality', 'baseline_gain']):
        raise ValueError('The structural research models have not passed their synthetic equity gates')
    runs = [r for r in report['runs'] if r['model'] == 'structural']
    if len(runs) != 3 or len({r['seed'] for r in runs}) != 3 or any(r['structural'] is not True for r in runs):
        raise ValueError('Expected three structural model seeds')
    artifacts = [(r, checked_bytes(report_path.parent, {'path': r['model_path'], 'sha256': r['sha256']}, 16 * 1024 * 1024)) for r in runs]
    folder = destination / 'models/river-coach'; folder.mkdir(parents=True, exist_ok=True)
    def immutable(path, payload):
        if path.exists():
            if path.read_bytes() != payload: raise ValueError('Immutable coach artifact conflict')
        else:
            with path.open('xb') as target: target.write(payload)
    members = []
    for run, weights in artifacts:
        path = folder / (run['sha256'] + '.pt'); immutable(path, weights)
        members.append({'path': path.relative_to(destination).as_posix(), 'sha256': run['sha256'],
                        'seed': run['seed'], 'width': run['width'], 'test_mae': run['test']['mae']})
    bundle = {'version': VERSION, 'features_version': report['version'], 'members': members,
              'training_dataset_id': report['dataset_id'], 'report_sha256': hashlib.sha256(data).hexdigest(),
              'member_test_mae_mean': mean(r['test']['mae'] for r in runs),
              'scope': 'Synthetic practice only; no action-strategy promotion or ensemble quality claim.'}
    payload = canonical(bundle).encode(); sha = hashlib.sha256(payload).hexdigest()
    path = folder / (sha + '.json'); immutable(path, payload)
    pointer = {'path': path.relative_to(destination).as_posix(), 'sha256': sha}
    manifest(destination, pointer)
    temporary = destination / ('river-coach-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(canonical(pointer)); temporary.replace(destination / 'river-coach.json')
    return {'status': 'installed', 'members': len(members), 'bundle_sha256': sha,
            'training_dataset_id': report['dataset_id'], 'cloud_enabled': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--report', type=Path, default=ROOT / '.data/research-river-v3/models/report.json')
    p.add_argument('--data', type=Path, default=ROOT / '.data')
    args = p.parse_args(); print(json.dumps(prepare(args.report, args.data), indent=2))
