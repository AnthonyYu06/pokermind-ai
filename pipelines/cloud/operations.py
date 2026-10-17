"""Scoped status/stop/reconciliation. These commands never start compute."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from packages.pokermind_core.contracts import digest
from .budget import Ledger, DynamoBackend
from .observe import observe, reconcile

RETAINED = 'No data deleted or reservation refunded. S3 versions, disks, images, logs and the ledger remain billable until reviewed separately.'


def scope(plan, policy, key, token):
    host = urlparse(plan.get('workspace_host', ''))
    if not re.fullmatch(r'\d{12}', plan.get('account_id', '')) or plan['account_id'] == '000000000000':
        raise ValueError('A real account scope is required; no cloud request was made')
    if (host.scheme != 'https' or not host.hostname or not host.hostname.endswith('.cloud.databricks.com')
            or host.username or host.password or host.port or host.query or host.fragment or host.path not in ('', '/')):
        raise ValueError('Expected a scoped AWS Databricks workspace hostname')
    if not re.fullmatch(re.escape(policy['project']) + r':\d{4}-\d{2}', key) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', token):
        raise ValueError('Use the project ledger key and original submission token')


def owned_run(workspace, ledger, plan, policy, key, token):
    scope(plan, policy, key, token)
    state, _ = ledger.backend.read(key)
    entry = (state or {}).get('reservations', {}).get(token)
    if not entry or entry['plan_hash'] != digest(plan) or not entry.get('job_id'):
        raise ValueError('No matching bound job/run reservation; inspect uncertain submissions before stopping anything')
    job = workspace.api_client.do('GET', '/api/2.1/jobs/get', query={'job_id': entry['job_id']})
    tags = job['settings'].get('tags', {})
    expected = {'Project': policy['project'], 'PlanHash': digest(plan), 'Reservation': token}
    if any(tags.get(k) != v for k, v in expected.items()):
        raise ValueError('Job ownership does not match the project reservation')
    if not entry.get('run_id'):
        runs = workspace.api_client.do('GET', '/api/2.1/jobs/runs/list', query={'job_id': entry['job_id'], 'limit': 100})
        if runs.get('has_more') or len(runs.get('runs', [])) != 1:
            raise ValueError('Ambiguous lost acknowledgment; inspect this scoped job, no new run was submitted')
        entry = {**entry, 'run_id': runs['runs'][0]['run_id']}
    run = workspace.api_client.do('GET', '/api/2.1/jobs/runs/get', query={'run_id': entry['run_id']})
    if run.get('job_id') != entry['job_id']:
        raise ValueError('Run belongs to another job')
    return {'key': key, 'token': token, **entry}, observe(workspace, entry['run_id'])


def stop_run(workspace, ledger, plan, policy, key, token):
    reservation, observation = owned_run(workspace, ledger, plan, policy, key, token)
    if observation['compute_stopped']:
        return {'status': 'already-stopped', 'observation': observation, 'retained': RETAINED}
    workspace.api_client.do('POST', '/api/2.1/jobs/runs/cancel', body={'run_id': reservation['run_id']})
    actions = [{'operation': 'cancel-run', 'run_id': reservation['run_id']}]; errors = []
    # A dedicated JOB cluster with matching project/token can be terminated if
    # run cancellation does not stop it. Never terminate arbitrary/shared compute.
    for cluster in observation['clusters']:
        if cluster['state'] == 'TERMINATED': continue
        try:
            current = workspace.api_client.do('GET', '/api/2.0/clusters/get', query={'cluster_id': cluster['cluster_id']})
            tags = current.get('custom_tags', {})
            if current.get('cluster_source') != 'JOB' or tags.get('Project') != policy['project'] or tags.get('Reservation') != token:
                raise ValueError('Cluster ownership is unverified; only run cancellation was requested')
            workspace.api_client.do('POST', '/api/2.0/clusters/delete', body={'cluster_id': cluster['cluster_id']})
            actions.append({'operation': 'terminate-job-cluster', 'cluster_id': cluster['cluster_id']})
        except Exception as exc:
            errors.append({'cluster_id': cluster['cluster_id'], 'error': str(exc)})
    return {'status': 'stop-requested', 'termination_confirmed': False, 'actions': actions, 'errors': errors, 'retained': RETAINED}


def app_status(client, instance_id, policy, stop=False):
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', instance_id): raise ValueError('Invalid EC2 instance ID')
    response = client.describe_instances(InstanceIds=[instance_id])
    instances = [i for r in response['Reservations'] for i in r['Instances']]
    if len(instances) != 1: raise ValueError('Expected one scoped app instance')
    instance = instances[0]; tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
    if tags.get('Project') != policy['project'] or tags.get('Owner') != policy['owner'] or tags.get('Name') != 'pokermind-private-app':
        raise ValueError('Instance is not the project-owned private app')
    state = instance['State']['Name']; stopped = state in ('stopped', 'terminated')
    if stop and not stopped and state != 'stopping':
        if instance.get('RootDeviceType') != 'ebs': raise ValueError('Only EBS-backed app instances can be stopped')
        client.stop_instances(InstanceIds=[instance_id])
    return {'instance_id': instance_id, 'state_observed': state, 'compute_stopped': stopped,
            'stop_requested': stop and not stopped, 'retained': RETAINED}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['status', 'stop', 'reconcile', 'app-status', 'app-stop'])
    p.add_argument('--plan', type=Path, required=True); p.add_argument('--policy', type=Path, default=Path('configs/cost-policy.json'))
    p.add_argument('--key'); p.add_argument('--token'); p.add_argument('--instance-id'); p.add_argument('--billing', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args(argv)
    try:
        plan, policy = json.loads(a.plan.read_text()), json.loads(a.policy.read_text())
        scope(plan, policy, a.key or policy['project'] + ':2000-01', a.token or 'app_scope')
        if not a.command.startswith('app-') and (not a.key or not a.token): raise ValueError('--key and --token are required')
        import boto3
        session = boto3.Session(region_name=plan['region'])
        if session.client('sts').get_caller_identity()['Account'] != plan['account_id']: raise ValueError('AWS account scope mismatch')
        if a.command.startswith('app-'):
            result = app_status(session.client('ec2'), a.instance_id or '', policy, a.command == 'app-stop')
        else:
            from databricks.sdk import WorkspaceClient
            workspace = WorkspaceClient(host=plan['workspace_host']); ledger = Ledger(DynamoBackend(session.client('dynamodb'), plan['budget_table']))
            if a.command == 'stop': result = stop_run(workspace, ledger, plan, policy, a.key, a.token)
            else:
                reservation, observation = owned_run(workspace, ledger, plan, policy, a.key, a.token)
                if a.command == 'reconcile':
                    if not a.billing: raise ValueError('--billing is required; no automatic refunds')
                    ledger.update(a.key, a.token, status=reservation['status'], run_id=reservation['run_id'])
                    result = reconcile(ledger, reservation, observation, a.billing)
                else: result = {'reservation': reservation, 'observation': observation, 'retained': RETAINED}
        rendered = json.dumps(result, indent=2)
        if a.output:
            a.output.parent.mkdir(parents=True, exist_ok=True)
            with a.output.open('x') as f: f.write(rendered + '\n')
        print(rendered)
        return 2 if result.get('errors') else 0
    except ImportError as exc: p.exit(2, f'Install the local cloud SDK dependencies: {exc}\n')
    except Exception as exc: p.exit(2, f'PokerMind cloud operation: {exc}\n')


if __name__ == '__main__': raise SystemExit(main())
