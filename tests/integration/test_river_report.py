import hashlib
import json
from pathlib import Path
from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.store import Store
from services.api.backup import restore


def test_river_evidence_round_trip_and_checksum_enforcement(tmp_path):
    root = tmp_path / 'app'; store = Store(root)
    evidence = root / 'evidence'; evidence.mkdir()
    payload = json.dumps({'status': 'measured', 'scored_rows': 15998}).encode()
    path = evidence / 'river.json'; path.write_bytes(payload)
    (root / 'river-research.json').write_text(json.dumps({'path': 'evidence/river.json', 'sha256': hashlib.sha256(payload).hexdigest()}))
    with TestClient(create_app(root)) as client:
        assert client.get('/api/research/river').json()['scored_rows'] == 15998
    backup = tmp_path / 'backup'; store.backup(backup); restore(backup, tmp_path / 'restored')
    with TestClient(create_app(tmp_path / 'restored')) as client:
        assert client.get('/api/research/river').json()['status'] == 'measured'
    path.write_text('{}')
    with TestClient(create_app(root), raise_server_exceptions=False) as client:
        assert client.get('/api/research/river').status_code >= 400
