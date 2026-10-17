"""Guarded unscheduled Jobs execution. Disabled policy is checked before any SDK.

The idempotency token and budget reservation survive network ambiguity. A failed
submission is not evidence that nothing ran; reservations are never auto-refunded.
"""
import argparse
import json
from pathlib import Path
import time
from packages.pokermind_core.contracts import digest
from .budget import Ledger, DynamoBackend, validate
from .plan import validate_plan, job_definition


def verify_job(expected, actual):
    def subset(a, b):
        if isinstance(a, dict): return isinstance(b, dict) and all(k in b and subset(v, b[k]) for k, v in a.items())
        if isinstance(a, list): return isinstance(b, list) and len(a) == len(b) and all(subset(x, y) for x, y in zip(a, b))
        return a == b
    if any(actual.get(k) for k in ('schedule', 'continuous', 'trigger')) or not subset(expected, actual):
        raise ValueError('Existing job configuration differs from the approved definition')
    if any('autoscale' in c.get('new_cluster', {}) for c in actual.get('job_clusters', [])):
        raise ValueError('Autoscaling is outside the approved fixed-worker plan')


def submit(plan, policy, token, *, ledger_factory=None, workspace_factory=None, identity=None):
    now = time.time()
    validate(policy, plan, now); validate_plan(plan)
    if ledger_factory is None:
        import boto3
        session = boto3.Session(region_name=plan['region'])
        identity = session.client('sts').get_caller_identity()['Account']
        ledger_factory = lambda: Ledger(DynamoBackend(session.client('dynamodb'), plan['budget_table']))
    if identity != plan['account_id']:
        raise ValueError('Authenticated AWS account differs from approved plan')
    ledger = ledger_factory(); reservation = ledger.reserve(policy, plan, token, now)
    if reservation['status'] == 'closed':
        raise ValueError('This reservation has already been reconciled')
    if reservation.get('run_id'):
        return {'run_id': reservation['run_id'], 'reservation': reservation, 'reused': True}
    if workspace_factory is None:
        from databricks.sdk import WorkspaceClient
        workspace_factory = lambda: WorkspaceClient(host=plan['workspace_host'])
    try:
        workspace = workspace_factory()
        # Shared clusters/retry settings belong to a saved job, not SubmitTask.
        # The job is unscheduled and its creation does not allocate compute.
        definition = job_definition(plan, policy, reservation)
        job_id = reservation.get('job_id')
        if job_id is None:
            listed = workspace.api_client.do('GET', '/api/2.1/jobs/list', query={'name': definition['name'], 'expand_tasks': True})
            matches = [j for j in listed.get('jobs', []) if j.get('settings', {}).get('name') == definition['name']]
            if listed.get('has_more') or len(matches) > 1:
                raise ValueError('Ambiguous job creation recovery; inspect matching unscheduled jobs')
            if matches: job_id = matches[0]['job_id']
            else: job_id = workspace.api_client.do('POST', '/api/2.1/jobs/create', body=definition)['job_id']
            ledger.update(reservation['key'], token, status='reserved', job_id=job_id)
        actual = workspace.api_client.do('GET', '/api/2.1/jobs/get', query={'job_id': job_id})
        verify_job(definition, actual['settings'])
        # Unified authentication; retries use one stable run-now token.
        result = workspace.api_client.do('POST', '/api/2.1/jobs/run-now', body={'job_id': job_id, 'idempotency_token': token})
        ledger.update(reservation['key'], token, status='submitted', run_id=result['run_id'])
        return {'job_id': job_id, 'run_id': result['run_id'], 'reservation': reservation, 'reused': False}
    except Exception:
        ledger.update(reservation['key'], token, status='unknown')
        raise


def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument('--plan', type=Path, required=True); p.add_argument('--token', required=True)
    p.add_argument('--policy', type=Path, default=Path('configs/cost-policy.json')); p.add_argument('--evidence', type=Path, default=Path('.data/cloud-execution'))
    a = p.parse_args(argv); plan, policy = json.loads(a.plan.read_text()), json.loads(a.policy.read_text()); started = time.time()
    result = submit(plan, policy, a.token)
    from databricks.sdk import WorkspaceClient
    from .observe import monitor
    workspace = WorkspaceClient(host=plan['workspace_host'])
    try:
        result['stop_evidence'] = monitor(workspace, result['run_id'], started + plan['timeout_seconds'], a.evidence / (a.token + '.json'), plan_hash=digest(plan))
    except KeyboardInterrupt:
        workspace.api_client.do('POST', '/api/2.1/jobs/runs/cancel', body={'run_id': result['run_id']})
        raise
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
