import json
from itertools import permutations
import pytest
np = pytest.importorskip('numpy')
torch = pytest.importorskip('torch')
pq = pytest.importorskip('pyarrow.parquet')
from packages.models.river import encode_structure, RiverNet, DIM
from training.river import fit, checksum, checked_dataset


def test_structural_encoder_is_invariant_and_not_equity_label():
    hero, board = ['As', 'Kh'], ['Qc', 'Jh', 'Td', '3c', '2s']
    expected = encode_structure(hero, board)
    assert len(expected) == DIM and all(0 <= x <= 1 for x in expected)
    for perm in permutations('cdhs'):
        rename = dict(zip('cdhs', perm))
        convert = lambda cards: [c[0] + rename[c[1]] for c in reversed(cards)]
        assert encode_structure(convert(hero), convert(board)) == expected
    with pytest.raises(ValueError):
        encode_structure(hero, board[:-1] + ['As'])


def fixture_dataset(root):
    import pyarrow as pa
    torch.manual_seed(9)
    records = [{'features': torch.rand(DIM).tolist(), 'equity': .1 + i * .07, 'deal_family': str(i)} for i in range(12)]
    files = []
    for split in ['train', 'validation', 'test']:
        path = root / (split + '.parquet'); pq.write_table(pa.Table.from_pylist(records), path)
        files.append({'path': path.name, 'split': split, 'sha256': checksum(path)})
    manifest = {'dataset_id': 'fixture', 'features_version': 'river-structure-v1', 'source_kind': 'synthetic', 'files': files, 'counts': dict.fromkeys(['train', 'validation', 'test'], 12)}
    (root / 'dataset.json').write_text(json.dumps(manifest))
    return manifest


def test_river_resume_exact_and_frozen_shard_integrity(tmp_path):
    data = tmp_path / 'data'; data.mkdir(); manifest = fixture_dataset(data)
    cfg = {'threads': 2, 'width': 16, 'learning_rate': .003, 'batch_size': 6, 'epochs': 6, 'patience': 20}
    full, part, resumed = [tmp_path / p for p in ['full', 'part', 'resumed']]
    for p in [full, part, resumed]: p.mkdir()
    a, curve, best = fit(manifest, data, cfg, 11, True, full)
    fit(manifest, data, {**cfg, 'epochs': 3}, 11, True, part)
    b, curve2, best2 = fit(manifest, data, cfg, 11, True, resumed, resume=part / 'checkpoint.pt')
    assert curve == curve2 and best == best2
    assert all(torch.equal(v, b.state_dict()[k]) for k, v in a.state_dict().items())
    assert checked_dataset(data / 'dataset.json') == manifest
    (data / 'test.parquet').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        checked_dataset(data / 'dataset.json')


def test_partition_generator_reproducible_and_disjoint():
    from types import SimpleNamespace
    from pipelines.spark.workload import generate_rivers, generate_hands
    rows = [SimpleNamespace(id=i) for i in range(6)]
    a = list(generate_rivers(rows, 99))
    assert a == list(generate_rivers(rows[:2], 99)) + list(generate_rivers(rows[2:], 99))
    assert len({r[2] for r in a}) == 6
    assert list(generate_hands(rows, 99)) == list(generate_hands(rows, 99))
