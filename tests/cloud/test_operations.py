from copy import deepcopy
import json
import pytest
from packages.pokermind_core.contracts import digest
from pipelines.cloud.budget import Ledger
from pipelines.cloud.operations import owned_run, stop_run, app_status, main
from test_budget import MemoryCAS, approved, NOW


def context():
    policy, plan = approved(); ledger = Ledger(MemoryCAS()); r = ledger.reserve(policy, plan, 'request_1', NOW)
    ledger.update(r['key'], r['token'], status='submitted', job_id=10, run_id=20)
    return {**policy, 'cloud_enabled': False}, plan, ledger, r


class Workspace:
    def __init__(self, policy, plan):
        self.api_client = self; self.mutations = []; self.foreign_job = False; self.foreign_cluster = False
        self.tags = {'Project': policy['project'], 'PlanHash': digest(plan), 'Reservation': 'request_1'}
    def do(self, method, path, query=None, body=None):
        if method == 'POST': self.mutations.append((path, body)); return {}
        if path.endswith('/jobs/get'): return {'settings': {'tags': {**self.tags, **({'Project': 'foreign'} if self.foreign_job else {})}}}
        if path.endswith('/runs/list'): return {'runs': [{'run_id': 20}]}
        if path.endswith('/runs/get'): return {'job_id': 10, 'state': {'life_cycle_state': 'RUNNING'}, 'tasks': [{'cluster_instance': {'cluster_id': 'c1'}}]}
        return {'cluster_id': 'c1', 'cluster_source': 'API' if self.foreign_cluster else 'JOB', 'custom_tags': self.tags,
                'state': 'RUNNING', 'executors': [{'node_id': 'node1'}]}


def test_emergency_stop_works_with_spending_disabled_and_never_refunds():
    policy, plan, ledger, r = context(); workspace = Workspace(policy, plan)
    before = ledger.backend.read(r['key'])
    result = stop_run(workspace, ledger, plan, policy, r['key'], r['token'])
    assert result['status'] == 'stop-requested' and not result['termination_confirmed']
    assert len(workspace.mutations) == 2
    assert ledger.backend.read(r['key']) == before


def test_stop_refuses_another_projects_job_and_never_terminates_shared_cluster():
    policy, plan, ledger, r = context(); workspace = Workspace(policy, plan); workspace.foreign_job = True
    with pytest.raises(ValueError, match='ownership'): stop_run(workspace, ledger, plan, policy, r['key'], r['token'])
    assert not workspace.mutations
    workspace.foreign_job = False; workspace.foreign_cluster = True
    result = stop_run(workspace, ledger, plan, policy, r['key'], r['token'])
    assert result['errors'] and len(workspace.mutations) == 1 and workspace.mutations[0][0].endswith('/cancel')


def test_lost_submit_acknowledgment_can_be_stopped_without_resubmission():
    policy, plan, ledger, r = context(); workspace = Workspace(policy, plan)
    state, revision = ledger.backend.read(r['key']); state['reservations'][r['token']].pop('run_id')
    ledger.backend.compare_and_set(r['key'], revision, state)
    result = stop_run(workspace, ledger, plan, policy, r['key'], r['token'])
    assert result['actions'][0] == {'operation': 'cancel-run', 'run_id': 20}
    assert all('run-now' not in path for path, _ in workspace.mutations)


def test_example_scope_refuses_before_sdk_construction(capsys):
    with pytest.raises(SystemExit) as exc:
        main(['status', '--plan', 'configs/cloud-run.example.json', '--key', 'pokermind:2026-10', '--token', 'request_1'])
    assert exc.value.code == 2 and 'no cloud request' in capsys.readouterr().err


def test_ec2_stop_is_scoped_and_does_not_terminate_or_delete():
    boto3 = pytest.importorskip('boto3'); from botocore.stub import Stubber
    client = boto3.client('ec2', region_name='us-east-1', aws_access_key_id='fixture', aws_secret_access_key='fixture')
    policy = {'project': 'pokermind', 'owner': 'fixture'}; instance = 'i-0123456789abcdef0'
    details = {'InstanceId': instance, 'State': {'Code': 16, 'Name': 'running'}, 'RootDeviceType': 'ebs',
               'Tags': [{'Key': k, 'Value': v} for k, v in {'Project': 'pokermind', 'Owner': 'fixture', 'Name': 'pokermind-private-app'}.items()]}
    with Stubber(client) as stub:
        stub.add_response('describe_instances', {'Reservations': [{'Instances': [details]}]}, {'InstanceIds': [instance]})
        stub.add_response('stop_instances', {'StoppingInstances': []}, {'InstanceIds': [instance]})
        result = app_status(client, instance, policy, stop=True)
        assert result['stop_requested'] and not result['compute_stopped']
    wrong = deepcopy(details); wrong['Tags'][0]['Value'] = 'foreign'
    with Stubber(client) as stub:
        stub.add_response('describe_instances', {'Reservations': [{'Instances': [wrong]}]}, {'InstanceIds': [instance]})
        with pytest.raises(ValueError, match='project-owned'): app_status(client, instance, policy, stop=True)
