from copy import deepcopy
import json
import pytest
from benchmarks.cloud import compare, attach_billing
from benchmarks.spark_events import collect


def test_event_metrics_include_failed_attempts_and_repeated_io(tmp_path):
    events = [{'Event': 'SparkListenerApplicationStart', 'App ID': 'fixture'}]
    for i in range(2):
        events.append({'Event': 'SparkListenerTaskEnd', 'Task Info': {'Finish Time': 20, 'Launch Time': 10,
                       'Host': 'host', 'Executor ID': '1', 'Failed': bool(i)},
                       'Task Metrics': {'Memory Bytes Spilled': 4, 'Shuffle Read Metrics': {'Remote Bytes Read': 6, 'Local Bytes Read': 2},
                                        'Input Metrics': {'Bytes Read': 10}}})
    path = tmp_path / 'events.json'; path.write_text('\n'.join(json.dumps(e) for e in events))
    result = collect(path)
    assert result['tasks'] == 2 and result['failed_or_killed_attempts'] == 1
    assert result['metrics']['shuffle_read_bytes'] == 16 and result['metrics']['task_input_bytes'] == 20
    with pytest.raises(ValueError, match='size limit'): collect(path, maximum_bytes=10)


def records():
    # Contract fixtures only: these are not measured cloud evidence.
    return [{'kind': 'measured', 'comparison_signature': 'same', 'cloud_execution_verified': True,
             'end_to_end_seconds': 100 / workers, 'run_id': workers*10+i, 'workers': workers, 'repetition': i}
            for workers in (1, 2) for i in (1, 2, 3)]


def test_comparison_requires_real_worker_evidence_compatible_runs_and_repetitions():
    rows = records(); result = compare(rows)
    assert result['status'] == 'measured' and result['comparisons'][1]['speedup'] == 2
    for mutate in [lambda r: r[0].update(cloud_execution_verified=False), lambda r: r[0].update(comparison_signature='other'),
                   lambda r: r[0].update(run_id=r[1]['run_id']), lambda r: r.pop(), lambda r: r[0].update(end_to_end_seconds=None)]:
        bad = deepcopy(rows); mutate(bad)
        assert compare(bad)['status'] == 'incomplete' and not compare(bad)['comparisons']
    assert compare([*rows, {**rows[0], 'kind': 'warmup'}])['warmups_excluded'] == 1


def test_billed_cost_is_separate_from_retained_storage_and_bound_to_receipt(tmp_path):
    from services.api.backup import checksum
    billing = tmp_path / 'billing.json'; receipt = tmp_path / 'receipt.json'
    billing.write_text(json.dumps({'run_id': 1, 'status': 'final', 'cost_cents': {k: 10 for k in ['compute', 'databricks', 'storage', 'requests', 'network', 'logs', 'ledger']}, 'retained_storage_reserve_cents': 30}))
    receipt.write_text(json.dumps({'status': 'closed', 'run_id': 1, 'plan_hash': 'plan', 'charged_cents': 100, 'billing_evidence': {'sha256': checksum(billing)}}))
    record = {'cloud_execution_verified': True, 'run_id': 1, 'plan_sha256': 'plan', 'unique_hands': 100, 'scored_rows': 200}
    result = attach_billing(record, receipt, billing)
    assert result['actual_cost_usd'] == .7 and result['retained_storage_reserve_usd'] == .3
    assert result['cost_per_million_hands_usd'] == 7000
    billing.write_text('{}')
    with pytest.raises(ValueError, match='match'): attach_billing(record, receipt, billing)
