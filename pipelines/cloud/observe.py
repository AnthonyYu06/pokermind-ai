"""Read run/worker/stop evidence and reconcile only after actual billing is known."""
import hashlib
import json
from pathlib import Path


def observe(workspace, run_id):
    run = workspace.api_client.do('GET', '/api/2.1/jobs/runs/get', query={'run_id': run_id})
    cluster_ids = sorted({t['cluster_instance']['cluster_id'] for t in run.get('tasks', []) if t.get('cluster_instance', {}).get('cluster_id')})
    clusters = [workspace.api_client.do('GET', '/api/2.0/clusters/get', query={'cluster_id': cid}) for cid in cluster_ids]
    terminal = run.get('state', {}).get('life_cycle_state') in ('TERMINATED', 'SKIPPED', 'INTERNAL_ERROR')
    stopped = terminal and bool(clusters) and all(c.get('state') == 'TERMINATED' for c in clusters)
    return {'run_id': run_id, 'job_id': run.get('job_id'), 'state': run.get('state'), 'start_time': run.get('start_time'), 'end_time': run.get('end_time'),
            'setup_duration_ms': run.get('setup_duration'), 'execution_duration_ms': run.get('execution_duration'),
            'cleanup_duration_ms': run.get('cleanup_duration'), 'run_page_url': run.get('run_page_url'), 'compute_stopped': stopped,
            'clusters': [{'cluster_id': c['cluster_id'], 'state': c.get('state'), 'spark_version': c.get('spark_version'),
                          'driver': c.get('driver'), 'executors': c.get('executors', []), 'num_workers': c.get('num_workers'),
                          'node_type_id': c.get('node_type_id')} for c in clusters],
            'retained_storage': 'S3 versions, artifacts, logs, ECR and budget ledger may continue to incur costs.'}


def reconcile(ledger, reservation, observation, billing_path):
    billing_path = Path(billing_path); payload = billing_path.read_bytes(); billing = json.loads(payload)
    if not observation['compute_stopped'] or observation['run_id'] != reservation.get('run_id'):
        raise ValueError('Run and all compute must be verified stopped')
    if billing.get('run_id') != observation['run_id'] or billing.get('status') != 'final' or not billing.get('source'):
        raise ValueError('Final run-specific billing evidence required')
    required = {'compute', 'databricks', 'storage', 'requests', 'network', 'logs', 'ledger'}
    if set(billing.get('cost_cents', {})) != required or any(type(v) is not int or v < 0 for v in billing['cost_cents'].values()):
        raise ValueError('Incomplete final cost accounting')
    # Retained storage reserve stays charged unless explicitly included in a
    # final storage invoice; no automatic refund from a cloud timeout or TTL.
    reserve = billing.get('retained_storage_reserve_cents')
    if type(reserve) is not int or reserve < 0:
        raise ValueError('Retained storage reserve required')
    actual = sum(billing['cost_cents'].values()) + reserve
    return ledger.update(reservation['key'], reservation['token'], status='closed', actual_cents=actual,
                         stop_evidence=observation, billing_evidence={'sha256': hashlib.sha256(payload).hexdigest(), 'source': billing['source']})


def distributed_gate(manifest, observation, requested_workers):
    hosts = {r['host'] for r in manifest['worker_evidence'] if r['rows'] > 0}
    executor_ids = {e.get('node_id') or e.get('instance_id') for c in observation['clusters'] for e in c['executors']}
    executor_ids.discard(None)
    executor_ids.update(observation.get('observed_executor_ids', []))
    return {'passed': manifest['scope'] == 'cluster' and requested_workers >= 2 and len(hosts) >= 2 and len(executor_ids) >= 2,
            'observed_worker_hosts': sorted(hosts), 'observed_executor_ids': sorted(executor_ids), 'requested_workers': requested_workers,
            'interpretation': 'Local threads and configured worker counts alone cannot establish multiple-machine execution.'}


def monitor(workspace, run_id, deadline, evidence_path, interval=15, plan_hash=None):
    """Bounded run monitoring; loss of tracking requests cancellation, no refund."""
    import time
    evidence_path = Path(evidence_path); evidence_path.parent.mkdir(parents=True, exist_ok=True)
    executor_ids = set(); observations = []; cancelled = False
    while True:
        try:
            result = observe(workspace, run_id)
        except Exception:
            workspace.api_client.do('POST', '/api/2.1/jobs/runs/cancel', body={'run_id': run_id})
            raise RuntimeError('Tracking failed; cancellation requested, reservation retained')
        for cluster in result['clusters']:
            executor_ids.update(e.get('node_id') or e.get('instance_id') for e in cluster['executors'])
        executor_ids.discard(None)
        result['observed_executor_ids'] = sorted(executor_ids)
        observations.append(result)
        # Bound metadata memory as well as compute time.
        observations = observations[-512:]
        temporary = evidence_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'latest': result, 'observations': observations, 'cancel_requested': cancelled, 'plan_hash': plan_hash}, indent=2)); temporary.replace(evidence_path)
        if result['compute_stopped']: return result
        if time.time() >= deadline and not cancelled:
            workspace.api_client.do('POST', '/api/2.1/jobs/runs/cancel', body={'run_id': run_id}); cancelled = True
        if time.time() > deadline + 120:
            raise TimeoutError('Compute termination not confirmed; reservation retained and manual inspection required')
        time.sleep(interval)
