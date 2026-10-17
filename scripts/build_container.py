"""Build only against a local Docker socket, using reviewed official image digests."""
import argparse
import json
from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(); parser.add_argument('--tag', default='pokermind-local-verified:20261016'); args = parser.parse_args()
host = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
if not host.startswith('unix://'):
    raise SystemExit('This build command permits a local Docker socket only; no remote build spending.')
public = ROOT / '.runtime/docker-public'; public.mkdir(parents=True, exist_ok=True)
(public / 'config.json').write_text(json.dumps({'auths': {}}))
images = json.loads((ROOT / 'infra/container-images.json').read_text())
subprocess.run(['docker', '--config', str(public), '--host', host, 'build', '--platform', images['platform'],
                '--build-arg', 'NODE_IMAGE=' + images['images']['node']['digest'], '--build-arg', 'PYTHON_IMAGE=' + images['images']['python']['digest'],
                '-f', 'infra/Dockerfile', '-t', args.tag, '.'], cwd=ROOT, check=True, timeout=600)
