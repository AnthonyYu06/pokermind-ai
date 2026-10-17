import subprocess
import sys
from pathlib import Path


def test_existing_river_protocol_cannot_be_overwritten(tmp_path):
    protocol = tmp_path / 'pre-run-protocol.json'
    protocol.write_text('{"original": true}\n')
    log = tmp_path / 'run.log'
    log.write_text('Original run output\n')
    script = Path(__file__).resolve().parents[2] / 'scripts/run_river_research.py'
    result = subprocess.run([sys.executable, str(script), '--data', str(tmp_path)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert 'Choose a new --data directory' in result.stderr
    assert protocol.read_text() == '{"original": true}\n'
    assert log.read_text() == 'Original run output\n'
