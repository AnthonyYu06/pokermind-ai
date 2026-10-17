import hashlib
import json
import math

import pytest
torch = pytest.importorskip('torch')

from packages.models.river import RiverNet
from packages.models.river_inference import manifest, predict
from scripts.prepare_river_coach import prepare
from services.api.coach import RiverCoach, analyze, generate
from services.api.store import Store
from services.api.backup import restore


def bundle_fixture(tmp_path):
    source = tmp_path / 'trained'; source.mkdir()
    runs = []
    for seed, probability in enumerate([.2, .5, .8]):
        model = RiverNet(True, 8)
        with torch.no_grad():
            for parameter in model.parameters(): parameter.zero_()
            model.output.bias.fill_(math.log(probability / (1 - probability)))
        path = source / f'{seed}.pt'; torch.save(model.state_dict(), path)
        runs.append({'model': 'structural', 'structural': True, 'width': 8, 'seed': seed,
                     'model_path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'test': {'mae': .03}})
    report = {'version': 'river-structure-v1', 'dataset_id': 'test-fixture-not-research-evidence',
              'promotion_gates': {'structural': dict.fromkeys(['support', 'quality', 'baseline_gain'], True)}, 'runs': runs}
    path = source / 'report.json'; path.write_text(json.dumps(report))
    root = tmp_path / 'app'; prepare(path, root)
    return root, json.loads((root / 'river-coach.json').read_text()), path


def test_real_inference_three_seeds_hidden_cards_and_corruption(tmp_path):
    root, pointer, _ = bundle_fixture(tmp_path)
    result = analyze(root, generate(1), pointer)
    assert result['neural']['equity'] == pytest.approx(.5)
    assert [r['equity'] for r in result['neural']['members']] == pytest.approx([.2, .5, .8])
    assert result['ranking_source'] == 'neural' and result['seed_disagreement']
    hand = generate(1); hand.players[1].cards = ['As', 'Ah']
    assert analyze(root, hand, pointer) == result
    item = manifest(root, pointer)['members'][0]
    (root / item['path']).write_bytes(b'corrupted-after-caching')
    with pytest.raises(ValueError, match='checksum'):
        predict(root, pointer, ['As', 'Kh'], ['2c', '3c', '4h', '8s', 'Td'])
    fallback = analyze(root, generate(1), pointer)
    assert fallback['ranking_source'] == 'exact-reference'
    assert 'verification' in fallback['neural']['reason']


def test_bundle_pinning_backup_and_failed_gate_refusal(tmp_path):
    root, pointer, report_path = bundle_fixture(tmp_path)
    coach = RiverCoach(Store(root)); drill = coach.start()
    (root / 'river-coach.json').write_text('{}')
    compared = coach.compare(drill['drill_id'], None)
    assert compared['result']['neural']['bundle_sha256'] == pointer['sha256']
    (root / 'river-coach.json').write_text(json.dumps(pointer))
    Store(root).backup(tmp_path / 'backup'); restore(tmp_path / 'backup', tmp_path / 'restored')
    copied = RiverCoach(Store(tmp_path / 'restored')); new = copied.start()
    assert copied.compare(new['drill_id'], None)['result']['neural']['status'] == 'available'
    report = json.loads(report_path.read_text()); report['promotion_gates']['structural']['quality'] = False
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='gates'):
        prepare(report_path, root)
    assert json.loads((root / 'river-coach.json').read_text()) == pointer
