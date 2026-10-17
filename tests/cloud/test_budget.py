from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
import json
from pathlib import Path
import pytest
from packages.pokermind_core.contracts import digest
from pipelines.cloud.budget import Ledger, cents, validate, DynamoBackend
from pipelines.cloud.launch import submit
from pipelines.cloud.plan import job_definition, validate_plan

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp()


class MemoryCAS:
    def __init__(self): self.rows = {}; self.lock = Lock()
    def read(self, key):
        with self.lock: return deepcopy(self.rows.get(key, (None, None)))
    def compare_and_set(self, key, version, value):
        with self.lock:
            if self.rows.get(key, (None, None))[1] != version: return False
            self.rows[key] = deepcopy(value), (version or 0) + 1
            return True


def approved():
    plan = json.loads((ROOT / 'configs/cloud-run.example.json').read_text())
    plan.update(account_id='123456789012', workspace_host='https://example.cloud.databricks.com', node_type_id='m5.xlarge',
                instance_profile_arn='arn:aws:iam::123456789012:instance-profile/test', cluster_policy_id='policy123', expires_at='2026-10-31T23:00:00Z',
                lake_uri='s3://test-bucket/pokermind/synthetic', publication_uri='s3://test-published/snapshots/pokermind', wheel_sha256='a' * 64, quote_status='reviewed', upper_cost_cents=90)
    plan['cost_lines'] = [{'category': category, 'upper_cents': 10, 'source_url': 'https://example.com/quote', 'checked_at': '2026-10-16'} for category in
                         ['driver_compute', 'worker_compute', 'databricks_dbu', 'storage', 'requests', 'network', 'logs', 'retained_storage', 'budget_ledger']]
    policy = dict(cloud_enabled=True, per_run_budget_usd=1, monthly_budget_usd=2, max_run_minutes=15, max_workers=2, allowed_instance_types=['m5.xlarge'],
                  max_retries=0, max_concurrent_jobs=2, max_generated_rows=1000, max_storage_bytes=1073741824, project='test', owner='fixture', approved_plan_sha256=digest(plan))
    return policy, plan


def test_disabled_policy_cannot_instantiate_sdk():
    policy = json.loads((ROOT / 'configs/cost-policy.json').read_text())
    def forbidden(): raise AssertionError('Network factory must not be called')
    with pytest.raises(ValueError, match='disabled'):
        submit({}, policy, 'token1234', ledger_factory=forbidden, workspace_factory=forbidden)


def test_atomic_concurrent_reservations_never_overspend():
    policy, plan = approved(); ledger = Ledger(MemoryCAS())
    def attempt(i):
        try: return ledger.reserve(policy, plan, f'request_{i}', NOW)
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(attempt, range(20)))
    successes = [r for r in results if r]
    assert len(successes) == 2
    state, _ = ledger.backend.read(successes[0]['key'])
    assert state['charged_cents'] == 180
    assert ledger.reserve(policy, plan, successes[0]['token'], NOW)['upper_cents'] == 90


def test_unknown_and_expired_runs_do_not_refund_reservations():
    policy, plan = approved(); ledger = Ledger(MemoryCAS()); r = ledger.reserve(policy, plan, 'request_1', NOW)
    ledger.update(r['key'], r['token'], status='unknown')
    with pytest.raises(ValueError, match='expired'):
        ledger.require(r['key'], r['token'], digest(plan), r['expires'] + 1)
    assert ledger.backend.read(r['key'])[0]['charged_cents'] == 90
    with pytest.raises(ValueError, match='verified stop'):
        ledger.update(r['key'], r['token'], status='closed', actual_cents=0)
    ledger.update(r['key'], r['token'], status='closed', actual_cents=120, stop_evidence='terminal run record', billing_evidence='billing line items')
    assert ledger.backend.read(r['key'])[0]['charged_cents'] == 120


def test_limits_plan_binding_and_budget_boundary():
    policy, plan = approved()
    validate(policy, plan, NOW); validate_plan(plan)
    for update in [{'workers': 3}, {'gpus': 1}, {'count': 1001}, {'source_kind': 'real'}, {'timeout_seconds': 901}]:
        changed = {**plan, **update}; approved_policy = {**policy, 'approved_plan_sha256': digest(changed)}
        with pytest.raises(ValueError): validate(approved_policy, changed, NOW)
    with pytest.raises(ValueError, match='exact plan'): validate(policy, {**plan, 'seed': 1}, NOW)
    with pytest.raises(ValueError): cents(float('nan'))
    with pytest.raises(ValueError): cents(.001)


def test_job_definition_has_actual_dependency_chain_and_bounds():
    policy, plan = approved(); reservation = Ledger(MemoryCAS()).reserve(policy, plan, 'request_1', NOW)
    job = job_definition(plan, policy, reservation)
    assert job['timeout_seconds'] == 900
    assert job['job_clusters'][0]['new_cluster']['num_workers'] == 1
    assert job['tasks'][3]['depends_on'] == [{'task_key': 'score'}]
    assert all(t['max_retries'] == 0 and not t['retry_on_timeout'] for t in job['tasks'])
    assert 'existing_cluster_id' not in str(job)


def test_network_ambiguity_retains_money_and_reuses_token(monkeypatch):
    policy, plan = approved(); ledger = Ledger(MemoryCAS()); monkeypatch.setattr('pipelines.cloud.launch.time.time', lambda: NOW)
    class Client:
        api_client = None
        def __init__(self): self.api_client = self; self.calls = []; self.job = None
        def do(self, method, path, body=None, query=None):
            if path.endswith('/list'): return {'jobs': []}
            if path.endswith('/create'):
                self.job = body; return {'job_id': 9}
            if path.endswith('/get'): return {'settings': self.job}
            self.calls.append(body)
            if len(self.calls) == 1: raise TimeoutError('Submission acknowledgement lost')
            return {'run_id': 123}
    client = Client()
    args = dict(ledger_factory=lambda: ledger, workspace_factory=lambda: client, identity=plan['account_id'])
    with pytest.raises(TimeoutError): submit(plan, policy, 'request_1', **args)
    result = submit(plan, policy, 'request_1', **args)
    assert result['run_id'] == 123
    assert [c['idempotency_token'] for c in client.calls] == ['request_1', 'request_1']
    assert ledger.backend.read('test:2026-10')[0]['charged_cents'] == 90
    assert submit(plan, policy, 'request_1', **args)['reused']


def test_modified_or_scheduled_jobs_are_rejected():
    from pipelines.cloud.launch import verify_job
    policy, plan = approved(); definition = job_definition(plan, policy, {'key': 'test:2026-10', 'token': 'request_1'})
    verify_job(definition, deepcopy(definition))
    scheduled = deepcopy(definition); scheduled['schedule'] = {'pause_status': 'UNPAUSED'}
    with pytest.raises(ValueError): verify_job(definition, scheduled)
    changed = deepcopy(definition); changed['job_clusters'][0]['new_cluster']['num_workers'] = 8
    with pytest.raises(ValueError): verify_job(definition, changed)
