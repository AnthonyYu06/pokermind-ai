import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def test_cloud_commands_cannot_spend_by_default():
    policy=json.loads((ROOT/'configs/cost-policy.json').read_text())
    assert policy['cloud_enabled'] is False and policy['per_run_budget_usd']==0
    result=subprocess.run([sys.executable,'-m','services.api.cli','cloud-run'],capture_output=True,text=True)
    assert result.returncode!=0 and 'disabled' in result.stderr
    terraform=(ROOT/'infra/terraform/variables.tf').read_text()
    assert 'cloud_enabled' in terraform and 'per_run_budget_usd > 0' in terraform


def test_infrastructure_access_and_expiry_contract():
    source=(ROOT/'infra/terraform/main.tf').read_text()
    assert 'ingress {' not in source
    assert 'http_tokens                 = "required"' in source
    assert 'force_destroy = false' in source
    assert 's3:PutObject' not in source # Serving role cannot mutate data.
    bootstrap=(ROOT/'infra/terraform/bootstrap.sh.tftpl').read_text()
    assert 'OnBootSec=${minutes}min' in bootstrap
    assert bootstrap.index('pokermind-expiry.timer')<bootstrap.index('dnf install')
