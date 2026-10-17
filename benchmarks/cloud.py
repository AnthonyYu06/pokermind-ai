"""Collect immutable batch evidence and compare compatible measured cloud runs."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
from packages.pokermind_core.contracts import digest
from services.api.backup import checksum
from .spark_events import collect as event_metrics


def collect(root, events, *, observation=None, repetition=1, kind='measured'):
    root = Path(root).resolve(); plan = json.loads((root / 'plan.json').read_text())
    stages = {}; payloads = {}; hashes = {}
    for stage in ('materialize', 'train', 'score', 'publish'):
        pointer = json.loads((root / (stage + '.json')).read_text()); artifact = (root / pointer['path']).resolve()
        if not artifact.is_relative_to(root) or pointer['plan_hash'] != digest(plan) or checksum(artifact) != pointer['sha256']:
            raise ValueError('Benchmark artifact lineage mismatch')
        if not math.isfinite(pointer['seconds']) or pointer['seconds'] <= 0: raise ValueError('Invalid stage duration')
        stages[stage] = pointer['seconds']; payloads[stage] = json.loads(artifact.read_text()); hashes[stage] = pointer['sha256']
    data, model, scores, publication = [payloads[s] for s in ('materialize', 'train', 'score', 'publish')]
    if scores['manifest_hash'] != digest(data) or model['manifest_hash'] != digest(data) or publication['dataset_id'] != data['dataset_id']:
        raise ValueError('Benchmark stages describe different datasets')
    required_apps = {data['spark_application_id'], scores.get('spark_application_id', data['spark_application_id'])}
    metrics = event_metrics(events, application_ids=required_apps)
    if data['spark_application_id'] not in metrics['applications']:
        raise ValueError('Event log belongs to another Spark application')
    observation = observation or {}; observed = observation.get('latest', {}); clusters = observed.get('clusters', [])
    ids = set(observed.get('observed_executor_ids', [])) | {e.get('node_id') or e.get('instance_id') for c in clusters for e in c.get('executors', [])}
    ids.discard(None)
    hosts = {w['host'] for w in data['worker_evidence'] if w['rows'] > 0}
    cloud_evidence = (data['scope'] == 'cluster' and observation.get('plan_hash') == digest(plan)
                      and observed.get('compute_stopped') is True and observed.get('state', {}).get('result_state') == 'SUCCESS'
                      and len(ids) >= plan['workers'] and len(hosts) >= plan['workers'] and bool(clusters) and metrics['complete']
                      and scores.get('spark_application_id') and required_apps <= set(metrics['applications'])
                      and all(c.get('num_workers') == plan['workers'] and c.get('node_type_id') == plan['node_type_id'] for c in clusters))
    startup = observed.get('setup_duration_ms'); execution = observed.get('execution_duration_ms'); cleanup = observed.get('cleanup_duration_ms')
    wall = (observed['end_time'] - observed['start_time']) / 1000 if observed.get('end_time') and observed.get('start_time') else None
    signature = {'dataset_id': data['dataset_id'], 'code': data['code_sha256'], 'spark': data['spark_version'],
                 'runtime': model['runtime'], 'protocol': model['protocol'], 'instance': plan['node_type_id'], 'partitions': plan['partitions']}
    return {'schema_version': 1, 'recorded_at': datetime.now(timezone.utc).isoformat(), 'scope': data['scope'],
            'cloud_execution_verified': bool(cloud_evidence), 'run_id': observed.get('run_id'), 'plan_sha256': digest(plan),
            'comparison_signature': digest(signature), 'comparison_basis': signature, 'dataset_id': data['dataset_id'], 'artifact_hashes': hashes,
            'unique_hands': data['unique_hands'], 'decisions': data['decisions'], 'scored_rows': scores['rows'],
            'training_shard_bytes': sum(f['bytes'] for f in data['files']), 'dataset_output_identity': data['output_identity'],
            'prediction_output_identity': scores['output_identity'], 'workers': plan['workers'], 'total_nodes': plan['workers'] + 1,
            'observed_worker_hosts': sorted(hosts), 'observed_executor_ids': sorted(ids), 'repetition': repetition, 'kind': kind,
            'stage_seconds': stages, 'task_body_seconds': sum(stages.values()), 'end_to_end_seconds': wall,
            'startup_seconds': startup / 1000 if startup is not None else None,
            'execution_seconds': execution / 1000 if execution is not None else None,
            'cleanup_seconds': cleanup / 1000 if cleanup is not None else None,
            'hands_per_second': data['unique_hands'] / stages['materialize'],
            'scored_decisions_per_second': scores['rows'] / stages['score'], 'spark_events': metrics,
            'quality_metrics': [{'model': r['model'], 'seed': r['seed'], 'test': r['test']} for r in model['runs']],
            'estimated_upper_cost_usd': plan['upper_cost_cents'] / 100 if plan['upper_cost_cents'] else None,
            'actual_cost_usd': None, 'cost_status': 'unreconciled' if data['scope'] == 'cluster' else 'local-no-cloud-bill',
            'limits': 'Task-body timings exclude cluster startup. Repeated logs include retries. Model quality is synthetic only; no cloud claim without observed worker and stop evidence.'}


def attach_billing(record, receipt_path, billing_path):
    receipt = json.loads(Path(receipt_path).read_text()); billing_path = Path(billing_path); billing = json.loads(billing_path.read_text())
    if (not record['cloud_execution_verified'] or receipt.get('status') != 'closed'
            or receipt.get('run_id') != record['run_id'] or receipt.get('plan_hash') != record['plan_sha256']
            or receipt.get('billing_evidence', {}).get('sha256') != checksum(billing_path)
            or billing.get('run_id') != record['run_id'] or billing.get('status') != 'final'):
        raise ValueError('Billing must match a closed reservation and verified run')
    categories = {'compute', 'databricks', 'storage', 'requests', 'network', 'logs', 'ledger'}
    costs = billing.get('cost_cents', {}); reserve = billing.get('retained_storage_reserve_cents')
    if set(costs) != categories or any(type(v) is not int or v < 0 for v in costs.values()) or type(reserve) is not int or reserve < 0:
        raise ValueError('Incomplete billing categories/storage reserve')
    actual = sum(costs.values())
    if receipt['charged_cents'] != actual + reserve: raise ValueError('Ledger charge and billed costs differ')
    return {**record, 'actual_cost_usd': actual / 100, 'retained_storage_reserve_usd': reserve / 100, 'cost_status': 'reconciled',
            'cost_per_million_hands_usd': actual / 100 * 1_000_000 / record['unique_hands'],
            'cost_per_million_scored_decisions_usd': actual / 100 * 1_000_000 / record['scored_rows'],
            'cost_interpretation': 'Entire four-stage run billed cost divided by output count; retained storage reserve shown separately, not claimed as billed spend.'}


def compare(records):
    measured = [r for r in records if r['kind'] == 'measured']; reasons = []
    if not measured: return {'status': 'incomplete', 'reasons': ['No measured runs'], 'comparisons': []}
    if len({r['comparison_signature'] for r in measured}) != 1: reasons.append('Dataset, code, runtime, model protocol, partitions or instance types differ')
    if not all(r['cloud_execution_verified'] for r in measured): reasons.append('Actual cloud worker/stop evidence is incomplete')
    if any(not isinstance(r.get('end_to_end_seconds'), (int, float)) or not math.isfinite(r['end_to_end_seconds']) or r['end_to_end_seconds'] <= 0 for r in measured): reasons.append('End-to-end timing evidence is incomplete')
    ids = [r.get('run_id') for r in measured]
    if len(set(ids)) != len(ids): reasons.append('Duplicate or missing run IDs cannot count as repetitions')
    groups = {n: [r for r in measured if r['workers'] == n] for n in sorted({r['workers'] for r in measured})}
    if 1 not in groups or not any(n >= 2 for n in groups): reasons.append('A one-worker and multiple-worker comparison is required')
    if any(len(rows) < 3 or len({r['repetition'] for r in rows}) != len(rows) for rows in groups.values()): reasons.append('At least three distinct measured repetitions per configuration are required')
    if reasons: return {'status': 'incomplete', 'reasons': reasons, 'comparisons': [], 'warmups_excluded': len(records)-len(measured)}
    base = statistics.median(r['end_to_end_seconds'] for r in groups[1]); comparisons = []
    for workers, rows in groups.items():
        seconds = [r['end_to_end_seconds'] for r in rows]; median = statistics.median(seconds); speedup = base / median
        comparisons.append({'workers': workers, 'total_nodes': workers + 1, 'repetitions': len(rows), 'seconds': seconds,
                            'median_seconds': median, 'run_sd_seconds': statistics.pstdev(seconds), 'speedup': speedup,
                            'worker_efficiency': speedup / workers, 'total_node_efficiency': speedup / ((workers + 1) / 2)})
    return {'status': 'measured', 'comparisons': comparisons, 'warmups_excluded': len(records)-len(measured),
            'cost_status': 'Separate final billing reconciliation required; estimates are not actual cost',
            'interpretation': 'Strong scaling of the full four-stage job with identical inputs/configuration; both worker-only and driver-inclusive resource ratios are shown.'}


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('collect'); c.add_argument('--root', type=Path, required=True); c.add_argument('--events', type=Path, required=True)
    c.add_argument('--observation', type=Path); c.add_argument('--repetition', type=int, default=1); c.add_argument('--kind', choices=['warmup', 'measured'], default='measured')
    c.add_argument('--receipt', type=Path); c.add_argument('--billing', type=Path)
    c.add_argument('--output', type=Path, required=True)
    c = sub.add_parser('compare'); c.add_argument('records', type=Path, nargs='+'); c.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try:
        result = (collect(a.root, a.events, observation=json.loads(a.observation.read_text()) if a.observation else None,
                          repetition=a.repetition, kind=a.kind) if a.command == 'collect' else compare([json.loads(p.read_text()) for p in a.records]))
        if a.command == 'collect' and (a.receipt or a.billing):
            if not a.receipt or not a.billing: raise ValueError('Supply both --receipt and --billing')
            result = attach_billing(result, a.receipt, a.billing)
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with a.output.open('x') as f: json.dump(result, f, indent=2)
        print(json.dumps({'status': result.get('status', 'collected'), 'output': str(a.output), 'cloud_execution_verified': result.get('cloud_execution_verified')}, indent=2))
        return 2 if result.get('status') == 'incomplete' else 0
    except (ValueError, KeyError, OSError) as exc: p.exit(2, f'PokerMind benchmark: {exc}\n')


if __name__ == '__main__': raise SystemExit(main())
