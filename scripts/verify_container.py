"""Test the base serving image locally with no external container networking."""
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
host = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
if not host.startswith('unix://'): raise SystemExit('Local Docker socket required')
docker = ['docker', '--config', str(ROOT / '.runtime/docker-public'), '--host', host]
image = 'pokermind-local-verified:20261016'
container = subprocess.check_output(docker + ['run', '-d', '--rm', '--platform', 'linux/amd64', '--network', 'none', '--read-only',
    '--tmpfs', '/tmp:uid=10001,gid=10001,size=128m', '--tmpfs', '/app/.data:uid=10001,gid=10001,size=128m', image], text=True).strip()
try:
    for _ in range(20):
        response = subprocess.run(docker + ['exec', container, 'python', '-c',
            "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2).read().decode())"], capture_output=True, text=True)
        if response.returncode == 0: break
        time.sleep(1)
    else: raise RuntimeError('Production entrypoint health failed')
    checks = '''import json,urllib.request
def request(path,data=None):
 body=None if data is None else json.dumps(data).encode()
 req=urllib.request.Request('http://127.0.0.1:8000'+path,data=body,headers={'Content-Type':'application/json','X-PokerMind-Local':'1'})
 return urllib.request.urlopen(req).read()
assert b'<html' in request('/')
request('/api/demo',{})
overview=json.loads(request('/api/overview'));assert len(overview['hands'])==48
replay=json.loads(request('/api/hands/'+overview['hands'][0]['hand_id']+'/replay'))
assert all(not seat['cards'] for state in replay['snapshots'] for seat in state['seats'] if seat['seat']!=replay['hero'])
print('Production HTTP frontend, demo, replay and hidden-card checks passed')
'''
    subprocess.run(docker + ['exec', '-i', container, 'python', '-'], input=checks, text=True, check=True)
    snapshot_checks = '''from pathlib import Path
import json,zipfile
from services.api.store import Store
from services.api.snapshots import install
from services.api.backup import checksum
root=Path('/tmp/snapshot-verification');root.mkdir()
source=Store(root/'source');source.seed_demo();source.backup(root/'backup')
archive=root/'snapshot.zip'
with zipfile.ZipFile(archive,'w') as z:
 for p in (root/'backup').rglob('*'):
  if p.is_file():z.write(p,p.relative_to(root/'backup'))
manifest=root/'publication.json';manifest.write_text(json.dumps({'source_kind':'synthetic','reference_retained':True,'sha256':checksum(archive),'bytes':archive.stat().st_size,'hands':48,'scores':0,'dataset_id':'linux-fixture'}))
result=install(manifest,archive,root/'installed',checksum(manifest));assert result['hands']==48
assert install(manifest,archive,root/'installed',checksum(manifest),if_installed=True)['status']=='already-installed'
print('Pinned snapshot verification and idempotent install passed in Linux serving image')
'''
    subprocess.run(docker + ['exec', '-i', container, 'python', '-'], input=snapshot_checks, text=True, check=True)
    details = json.loads(subprocess.check_output(docker + ['image', 'inspect', image]))[0]
    result = {'status': 'passed', 'image_id': details['Id'], 'health': json.loads(response.stdout), 'platform': 'linux/amd64',
              'host': 'macOS ARM64 via Docker emulation', 'entrypoint': 'unmodified production CMD', 'read_only_root': True,
              'network': 'none', 'user_id': 10001, 'hands': 48, 'production_http_checks': ['frontend', 'demo', 'replay', 'hidden_cards'],
              'pinned_snapshot_install': 'passed', 'base_images': json.loads((ROOT / 'infra/container-images.json').read_text())}
    (ROOT / 'benchmarks/results/linux-container.json').write_text(json.dumps(result, indent=2)); print(json.dumps(result, indent=2))
finally:
    subprocess.run(docker + ['stop', '--time', '3', container], check=True, stdout=subprocess.DEVNULL)
