"""Optional, checksummed NFSP opponent for the internal Hold'em simulator."""
import io
import json
from functools import lru_cache

from packages.models.river_inference import checked_bytes, manifest


def research(root):
    pointer = root / 'holdem-nfsp.json'
    if not pointer.exists(): return {'status': 'not-run'}
    value = json.loads(checked_bytes(root, json.loads(pointer.read_text()), 8 * 1024 * 1024))
    if value.get('version') != 'nfsp-research-v1' or value.get('promotion', {}).get('eligible') is not False:
        raise ValueError('Unsupported or incorrectly promoted self-play report')
    return value


@lru_cache(maxsize=4)
def networks(data):
    try:
        import torch
    except ImportError as exc:
        raise ValueError('Local PyTorch research dependencies are required for this opponent') from exc
    from packages.strategy.nfsp import PolicyNet
    from packages.strategy.holdem_nfsp import DIM, FEATURE_VERSION
    payload = torch.load(io.BytesIO(data), map_location='cpu', weights_only=True)
    if (payload['version'], payload['features_version'], payload['domain'], payload['inputs'], payload['actions'], payload['stack_bb']) != (
            'nfsp-research-v1', FEATURE_VERSION, 'holdem', DIM, 7, 50):
        raise ValueError('Self-play policy contract mismatch')
    if type(payload['width']) is not int or not 1 <= payload['width'] <= 256 or len(payload['average']) != 2:
        raise ValueError('Unsupported self-play architecture')
    models = []
    for state in payload['average']:
        model = PolicyNet(DIM, 7, payload['width']); model.load_state_dict(state); model.eval(); models.append(model)
    return models, payload


def action_probabilities(root, data, observation):
    import torch
    from packages.strategy.holdem_nfsp import Features
    from packages.features.behavior import ACTIONS, candidates
    models, payload = networks(data)
    # Check the frozen feature bundle on each request, including after model caching.
    bundle = manifest(root, payload['river_pointer'])
    for item in bundle['members']: checked_bytes(root, item, 16 * 1024 * 1024)
    x, mask = Features(root, payload['river_pointer'])(observation)
    with torch.inference_mode():
        values = models[observation['acting_player'] - 1](x.unsqueeze(0))[0]
        if not torch.isfinite(values).all(): raise ValueError('Invalid self-play policy')
        p = values.masked_fill(~mask, -1e9).softmax(-1).tolist()
    choices = candidates(observation)
    return [{'label': label, 'action': choices[label], 'probability': p[i]} for i, label in enumerate(ACTIONS) if label in choices]
