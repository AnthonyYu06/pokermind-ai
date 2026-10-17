"""Offline readiness report; optional inventory uses metadata APIs only."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from packages.pokermind_core.contracts import digest
from pipelines.cloud.plan import validate_plan, job_definition


def check(plan, policy):
    problems = []
    try: validate_plan(plan)
    except (ValueError, KeyError) as exc: problems.append(str(exc))
    if not policy['cloud_enabled']: problems.append('Cloud spending is disabled ($0 authorized).')
    if policy.get('approved_plan_sha256') != digest(plan): problems.append('The exact plan has no spending approval.')
    return {'ready_to_submit': not problems, 'cloud_enabled': policy['cloud_enabled'], 'plan_sha256': digest(plan), 'blocking_gates': problems,
            'no_resources_created': True, 'target_runtime': plan['runtime']}


def inventory(plan):
    import boto3
    from databricks.sdk import WorkspaceClient
    session = boto3.Session(region_name=plan['region']); identity = session.client('sts').get_caller_identity()
    workspace = WorkspaceClient(host=plan['workspace_host'])
    return {'account_id': identity['Account'], 'region': plan['region'], 'workspace_host': plan['workspace_host'],
            'runtimes': workspace.api_client.do('GET', '/api/2.0/clusters/spark-versions'),
            'instance_profiles': workspace.api_client.do('GET', '/api/2.0/instance-profiles/list'),
            'cluster_policies': workspace.api_client.do('GET', '/api/2.0/policies/clusters/list'), 'metadata_only': True}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--plan', type=Path, default=ROOT / 'configs/cloud-run.example.json'); p.add_argument('--inventory', action='store_true')
    p.add_argument('--render', type=Path); a = p.parse_args(); plan = json.loads(a.plan.read_text()); policy = json.loads((ROOT / 'configs/cost-policy.json').read_text())
    result = check(plan, policy)
    if a.inventory: result['inventory'] = inventory(plan)
    if a.render:
        # Draft rendering still requires complete schema/pricing, but never calls
        # a workspace, creates a job, changes policy, or allocates compute.
        definition = job_definition(plan, policy, {'key': 'UNRESERVED', 'token': 'UNRESERVED'})
        a.render.write_text(json.dumps(definition, indent=2)); result['draft_job_file'] = str(a.render)
    print(json.dumps(result, indent=2))
