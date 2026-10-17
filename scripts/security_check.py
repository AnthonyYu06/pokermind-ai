"""Repository checks, not a replacement for a dedicated secret scanner."""
from pathlib import Path
import json,re,subprocess

root=Path(__file__).resolve().parents[1]
policy=json.loads((root/'configs/cost-policy.json').read_text())
assert policy['cloud_enabled'] is False and policy['monthly_budget_usd']==0
assert policy['personal_data_upload_authorized'] is False
names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,text=True).splitlines()
patterns=[re.compile(r'AKIA[0-9A-Z]{16}'),re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]
for name in names:
    path=root/name
    if name.endswith(('.png','.jpg','.svg')):continue
    if path.stat().st_size>2_000_000:raise SystemExit(f'Unexpected large source artifact: {name}')
    text=path.read_text(errors='ignore')
    if any(pattern.search(text) for pattern in patterns):raise SystemExit(f'Possible secret in {name}')
print('Source secret-pattern and disabled-cloud policy checks passed. This is a limited local check.')
