import io
import json
from pathlib import Path
import pytest
from pipelines.cloud.observe import distributed_gate, reconcile
from pipelines.spark.maintenance import vacuum_allowed


def test_local_threads_never_satisfy_distributed_gate():
    manifest = {'scope': 'local', 'worker_evidence': [{'host': 'a', 'rows': 3}, {'host': 'b', 'rows': 3}]}
    observation = {'clusters': [{'executors': [{'node_id': 'node1'}, {'node_id': 'node2'}]}]}
    assert not distributed_gate(manifest, observation, 2)['passed']
    assert distributed_gate({**manifest, 'scope': 'cluster'}, observation, 2)['passed']
    assert not distributed_gate({**manifest, 'scope': 'cluster', 'worker_evidence': [{'host': 'a', 'rows': 6}]}, observation, 2)['passed']


def test_vacuum_refuses_pinned_tables():
    with pytest.raises(ValueError, match='retained'):
        vacuum_allowed('s3://lake/table', [{'tables': {'labels': {'uri': 's3://lake/table', 'version': 0}}}])
    assert vacuum_allowed('s3://lake/unreferenced', [])['deletion_authorized'] is False


def test_storage_limit_counts_old_object_versions(tmp_path):
    from pipelines.cloud.job import check_storage
    class Client:
        def get_paginator(self, operation):
            assert operation == 'list_object_versions'; return self
        def paginate(self, **kwargs):
            yield {'Versions': [{'Size': 60, 'IsLatest': True}, {'Size': 50, 'IsLatest': False}]}
    with pytest.raises(ValueError, match='Retained S3'):
        check_storage(Client(), tmp_path, {'lake_uri': 's3://lake/prefix', 'publication_uri': 's3://published/snapshots', 'max_storage_bytes': 100})


def test_stage_pointer_cannot_escape_artifact_root(tmp_path):
    pytest.importorskip('torch')
    from pipelines.cloud.job import stage
    from packages.pokermind_core.contracts import digest
    outside=tmp_path/'outside';outside.write_bytes(b'not a stage')
    root=tmp_path/'artifacts';root.mkdir()
    (root/'materialize.json').write_text(json.dumps({'path':'../outside','sha256':'anything','plan_hash':digest({})}))
    with pytest.raises(ValueError, match='escaped'):
        stage(None,root,{},'materialize')


def test_cost_reconciliation_rejects_running_compute(tmp_path):
    path = tmp_path / 'billing.json'; path.write_text(json.dumps({'run_id': 3, 'status': 'final'}))
    with pytest.raises(ValueError, match='verified stopped'):
        reconcile(None, {'run_id': 3}, {'run_id': 3, 'compute_stopped': False}, path)


def test_monitor_keeps_worker_evidence_after_termination(tmp_path, monkeypatch):
    from pipelines.cloud.observe import monitor
    monkeypatch.setattr('time.sleep', lambda _: None)
    class Workspace:
        def __init__(self): self.api_client = self; self.calls = 0
        def do(self, method, path, query=None, body=None):
            if path.endswith('/runs/get'):
                self.calls += 1
                return {'state': {'life_cycle_state': 'RUNNING' if self.calls == 1 else 'TERMINATED'}, 'tasks': [{'cluster_instance': {'cluster_id': 'c'}}]}
            return {'cluster_id': 'c', 'state': 'RUNNING' if self.calls == 1 else 'TERMINATED',
                    'executors': [{'node_id': 'worker1'}, {'node_id': 'worker2'}] if self.calls == 1 else []}
    import time
    result = monitor(Workspace(), 1, time.time() + 60, tmp_path / 'run.json')
    assert result['compute_stopped'] and result['observed_executor_ids'] == ['worker1', 'worker2']


def test_monitor_cancels_when_tracking_fails(tmp_path):
    from pipelines.cloud.observe import monitor
    class Workspace:
        def __init__(self): self.api_client = self; self.cancelled = False
        def do(self, method, path, query=None, body=None):
            if path.endswith('/cancel'): self.cancelled = True; return {}
            raise OSError('Tracking unavailable')
    workspace = Workspace()
    with pytest.raises(RuntimeError, match='reservation retained'):
        monitor(workspace, 1, 0, tmp_path / 'run.json')
    assert workspace.cancelled


def test_real_sdk_serializes_job_contract_without_credentials():
    jobs = pytest.importorskip('databricks.sdk.service.jobs')
    from test_budget import approved
    from pipelines.cloud.plan import job_definition
    policy, plan = approved()
    job = job_definition(plan, policy, {'key': 'test:2026-10', 'token': 'request_1'})
    for task in job['tasks']:
        serialized = jobs.Task.from_dict(task).as_dict()
        assert serialized['python_wheel_task'] == task['python_wheel_task']
        assert serialized['job_cluster_key'] == 'bounded_batch'
    assert jobs.JobCluster.from_dict(job['job_clusters'][0]).as_dict()['new_cluster']['spark_version'] == plan['runtime']
    from pipelines.cloud.launch import verify_job
    verify_job(job, jobs.JobSettings.from_dict(job).as_dict())


def test_dynamodb_uses_consistent_reads_and_conditional_writes():
    boto3 = pytest.importorskip('boto3')
    from botocore.stub import Stubber, ANY
    from pipelines.cloud.budget import DynamoBackend
    client = boto3.client('dynamodb', region_name='us-east-1', aws_access_key_id='fixture', aws_secret_access_key='fixture')
    with Stubber(client) as stub:
        stub.add_response('get_item', {}, {'TableName': 'ledger', 'Key': {'id': {'S': 'month'}}, 'ConsistentRead': True})
        stub.add_response('put_item', {}, {'TableName': 'ledger', 'Item': ANY, 'ConditionExpression': 'attribute_not_exists(id)'})
        stub.add_client_error('put_item', service_error_code='ConditionalCheckFailedException', expected_params={
            'TableName': 'ledger', 'Item': ANY, 'ConditionExpression': '#r = :r', 'ExpressionAttributeNames': {'#r': 'revision'}, 'ExpressionAttributeValues': {':r': {'N': '1'}}})
        backend = DynamoBackend(client, 'ledger')
        assert backend.read('month') == (None, None)
        assert backend.compare_and_set('month', None, {'charged_cents': 90})
        assert not backend.compare_and_set('month', 1, {'charged_cents': 180})


def test_s3_publication_writes_manifest_last(tmp_path):
    boto3 = pytest.importorskip('boto3'); pytest.importorskip('torch')
    from botocore.stub import Stubber, ANY
    from pipelines.cloud.job import publish_s3
    from training.river import checksum
    from packages.pokermind_core.contracts import canonical
    data = tmp_path / 'snapshot.zip'; data.write_bytes(b'fixture backup')
    sha = checksum(data); publication = {'snapshot': data.name, 'sha256': sha}
    (tmp_path / 'publication.json').write_text(canonical(publication))
    client = boto3.client('s3', region_name='us-east-1', aws_access_key_id='fixture', aws_secret_access_key='fixture')
    prefix = 'snapshots/pokermind/' + sha
    with Stubber(client) as stub:
        for name, mime in [('snapshot.zip', 'application/zip'), ('publication.json', 'application/json')]:
            stub.add_response('put_object', {}, {'Bucket': 'published', 'Key': prefix + '/' + name, 'Body': ANY,
                'IfNoneMatch': '*', 'ServerSideEncryption': 'AES256', 'ChecksumSHA256': ANY, 'ContentType': mime, 'Metadata': ANY})
        result = publish_s3(client, tmp_path, {'publication_uri': 's3://published/snapshots/pokermind'}, {'path': 'publication.json'})
        assert result['snapshot_uri'].endswith('/snapshot.zip')
