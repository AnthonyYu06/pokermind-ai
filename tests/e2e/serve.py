"""Isolated loopback browser fixture; never touches the user's application data."""
import hashlib
import json
from pathlib import Path
import sys
import os
import tempfile
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from services.api.app import create_app
from services.api.store import Store
import uvicorn

with tempfile.TemporaryDirectory(prefix='pokermind-browser-') as directory:
    root = Path(directory); Store(root).seed_demo()
    payload = (ROOT / 'benchmarks/results/river-v3-summary.json').read_bytes()
    (root / 'evidence').mkdir(); (root / 'evidence/river.json').write_bytes(payload)
    (root / 'river-research.json').write_text(json.dumps({'path': 'evidence/river.json', 'sha256': hashlib.sha256(payload).hexdigest()}))
    if os.environ.get('POKERMIND_COACH_REPORT'):
        from scripts.prepare_river_coach import prepare
        prepare(Path(os.environ['POKERMIND_COACH_REPORT']), root)
    if os.environ.get('POKERMIND_NFSP_SOURCE'):
        from scripts.install_nfsp_research import install
        install(Path(os.environ['POKERMIND_NFSP_SOURCE']),root)
    uvicorn.run(create_app(root), host='127.0.0.1', port=8019, log_level='warning')
