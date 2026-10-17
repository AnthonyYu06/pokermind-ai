"""Conservative local-only profile limits, independent from the disabled cloud policy."""
import json
import shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def protocol():
    return json.loads((ROOT/'configs/local-research-v2.json').read_text())


def preflight(root):
    limits = protocol()['limits']
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < limits['minimum_free_disk_gib'] * 1024**3:
        raise ValueError('Local research stopped: keep at least 6 GiB of free disk space')
    size = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    if size > limits['max_profile_bytes']:
        raise ValueError('Local research workspace exceeds its 2 GiB profile limit')
    return limits
