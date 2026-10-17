"""Offline job rendering; rendering never submits work or creates resources."""
import re
from urllib.parse import urlparse
from packages.pokermind_core.contracts import canonical

RUNTIME = '17.3.x-cpu-ml-scala2.13'


def validate_plan(plan):
    if plan['version'] != 'cloud-batch-v1' or plan['source_kind'] != 'synthetic':
        raise ValueError('Only the reviewed synthetic batch contract is supported')
    if plan['runtime'] != RUNTIME:
        raise ValueError('Runtime differs from the compatibility target')
    if plan['quote_status'] != 'reviewed' or not plan['cost_lines']:
        raise ValueError('A priced, reviewed cost worksheet is required')
    required = {'driver_compute', 'worker_compute', 'databricks_dbu', 'storage', 'requests', 'network', 'logs', 'retained_storage', 'budget_ledger'}
    if {r['category'] for r in plan['cost_lines']} != required:
        raise ValueError('Cost worksheet is missing required categories')
    if any(type(r['upper_cents']) is not int or r['upper_cents'] < 0 or not r.get('source_url', '').startswith('https://') or not r.get('checked_at') for r in plan['cost_lines']):
        raise ValueError('Each cost category needs a verified source, date, and upper bound')
    if sum(r['upper_cents'] for r in plan['cost_lines']) != plan['upper_cost_cents'] or plan['upper_cost_cents'] <= 0:
        raise ValueError('Cost total does not match upper bound')
    host = urlparse(plan['workspace_host'])
    if host.scheme != 'https' or not host.hostname or not host.hostname.endswith('.cloud.databricks.com') or host.username or host.path not in ('', '/'):
        raise ValueError('Expected an AWS Databricks workspace HTTPS hostname')
    lake = urlparse(plan['lake_uri'])
    if lake.scheme != 's3' or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', lake.netloc) or not lake.path.startswith('/pokermind/synthetic') or '..' in lake.path:
        raise ValueError('Expected a scoped synthetic S3 prefix')
    publication = urlparse(plan['publication_uri'])
    if publication.scheme != 's3' or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', publication.netloc) or not publication.path.startswith('/snapshots/') or '..' in publication.path:
        raise ValueError('Expected a scoped publication S3 prefix')
    for key in ['volume_root', 'wheel_uri']:
        if not re.fullmatch(r'/Volumes/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+', plan[key]) or '..' in plan[key]:
            raise ValueError('Expected a scoped Unity Catalog volume path')
    if not re.fullmatch(r'[0-9a-f]{64}', plan['wheel_sha256']):
        raise ValueError('Tested wheel SHA256 required')
    if not plan['instance_profile_arn'].startswith('arn:aws:iam::' + plan['account_id'] + ':instance-profile/'):
        raise ValueError('Instance profile must belong to the reviewed account')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', plan['cluster_policy_id']) or plan['cluster_policy_id'] == 'UNSELECTED':
        raise ValueError('Cost-enforcing Databricks cluster policy required')
    if not re.fullmatch(r'[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+', plan['registered_model']):
        raise ValueError('Unity Catalog model name required')
    if not 1 <= plan['partitions'] <= 512 or not 1 <= plan['count'] <= 100_000:
        raise ValueError('Bounded training profile accepts at most 100,000 work IDs')
    if plan.get('previous_dataset_manifest') and not plan['previous_dataset_manifest'].startswith(plan['volume_root'].rstrip('/') + '/'):
        raise ValueError('Prior snapshot must be in the approved volume prefix')
    if plan['research']['limits']['max_seconds'] >= plan['timeout_seconds']:
        raise ValueError('Training must leave time for ETL, scoring, and publication')


def job_definition(plan, policy, reservation):
    validate_plan(plan)
    envelope = canonical({'plan': plan, 'policy': policy, 'reservation': {k: reservation[k] for k in ('key', 'token')}})
    cluster = {'spark_version': plan['runtime'], 'node_type_id': plan['node_type_id'], 'num_workers': plan['workers'],
               'policy_id': plan['cluster_policy_id'], 'apply_policy_default_values': True, 'data_security_mode': 'SINGLE_USER',
               'aws_attributes': {'availability': 'ON_DEMAND', 'zone_id': 'auto', 'instance_profile_arn': plan['instance_profile_arn']},
               'spark_conf': {'spark.sql.shuffle.partitions': str(plan['partitions']), 'spark.sql.adaptive.enabled': 'true', 'spark.speculation': 'false',
                              'spark.eventLog.enabled': 'true', 'spark.eventLog.compress': 'false', 'spark.eventLog.dir': plan['lake_uri'] + '/spark-events'},
               'cluster_log_conf': {'s3': {'destination': plan['lake_uri'] + '/cluster-logs', 'region': plan['region']}},
               'custom_tags': {'Project': policy['project'], 'Owner': policy['owner'], 'ExpiresAt': plan['expires_at'], 'Reservation': reservation['token']}}
    tasks = []
    for i, stage in enumerate(['materialize', 'train', 'score', 'publish']):
        task = {'task_key': stage, 'job_cluster_key': 'bounded_batch', 'timeout_seconds': plan['timeout_seconds'],
                'max_retries': plan['max_retries'], 'retry_on_timeout': False,
                'libraries': [{'whl': plan['wheel_uri']}],
                'python_wheel_task': {'package_name': 'pokermind_batch', 'entry_point': 'batch', 'parameters': ['--stage', stage, '--envelope', envelope]}}
        if i: task['depends_on'] = [{'task_key': ['materialize', 'train', 'score', 'publish'][i - 1]}]
        tasks.append(task)
    from packages.pokermind_core.contracts import digest
    return {'name': 'pokermind-' + reservation['token'], 'timeout_seconds': plan['timeout_seconds'], 'max_concurrent_runs': 1, 'queue': {'enabled': False},
            'tags': {'Project': policy['project'], 'PlanHash': digest(plan), 'Reservation': reservation['token']},
            'job_clusters': [{'job_cluster_key': 'bounded_batch', 'new_cluster': cluster}], 'tasks': tasks}
