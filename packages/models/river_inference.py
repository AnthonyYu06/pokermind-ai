"""Optional CPU inference from an immutable local research bundle."""
import hashlib
import io
import json
import math
from functools import lru_cache
from statistics import mean, stdev

from services.api.artifacts import safe_path

VERSION = 'river-coach-bundle-v1'


def checked_bytes(root, item, limit):
    path = safe_path(root, item['path'])
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Missing or oversized coach artifact')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError('Coach artifact checksum mismatch')
    return data


def manifest(root, pointer):
    value = json.loads(checked_bytes(root, pointer, 1024 * 1024))
    if value.get('version') != VERSION or value.get('features_version') != 'river-structure-v1':
        raise ValueError('Unsupported coach model contract')
    members = value['members']
    if len(members) != 3 or len({m['seed'] for m in members}) != 3:
        raise ValueError('Coach needs three independently seeded models')
    if any(type(m['width']) is not int or not 1 <= m['width'] <= 256 for m in members):
        raise ValueError('Unsupported coach model width')
    return value


@lru_cache(maxsize=6)
def load(data, width):
    import torch
    from .river import RiverNet
    model = RiverNet(structural=True, width=width)
    model.load_state_dict(torch.load(io.BytesIO(data), map_location='cpu', weights_only=True))
    model.eval()
    return model


def predict(root, pointer, hero, board):
    if pointer is None:
        return {'status': 'unavailable', 'reason': 'No trained river bundle was installed when this drill started.'}
    try:
        import torch
        from .river import encode_structure
    except ImportError:
        return {'status': 'unavailable', 'reason': 'Local PyTorch research dependencies are not installed.'}
    bundle = manifest(root, pointer)
    x = torch.tensor([encode_structure(hero, board)], dtype=torch.float32)
    predictions = []
    with torch.inference_mode():
        for item in bundle['members']:
            model = load(checked_bytes(root, item, 16 * 1024 * 1024), item['width'])
            value = float(model(x).item())
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('Invalid neural prediction')
            predictions.append({'seed': item['seed'], 'equity': value, 'sha256': item['sha256']})
    values = [p['equity'] for p in predictions]
    return {'status': 'available', 'equity': mean(values), 'seed_std': stdev(values),
            'members': predictions, 'bundle_sha256': pointer['sha256'],
            'training_dataset_id': bundle['training_dataset_id'],
            'member_test_mae_mean': bundle['member_test_mae_mean'],
            'note': 'Mean of three research networks. Seed spread is not a confidence interval; action quality is unvalidated.'}
