"""Stream Spark JSON event logs; task totals include failed/retried attempts."""
import gzip
import hashlib
import json
from pathlib import Path
import statistics


def collect(path, *, maximum_bytes=512 << 20, application_ids=None):
    path = Path(path)
    files = sorted(p for p in path.rglob('events_*') if p.is_file()) if path.is_dir() else [path]
    if not files: raise ValueError('No uncompressed or gzip Spark event logs found')
    total = 0; tasks = []; applications = set(); hosts = set(); executors = set(); sources = []
    metrics = {k: 0 for k in ('executor_run_ms', 'executor_cpu_ns', 'memory_spill_bytes', 'disk_spill_bytes',
                             'shuffle_read_bytes', 'shuffle_write_bytes', 'task_input_bytes', 'task_output_bytes')}
    failed = 0; peak = 0; app = None; ended = set(); seen_sources = set(); stage_tasks = {}
    for file in files:
        digest = hashlib.sha256()
        with (gzip.open(file, 'rb') if file.suffix == '.gz' else file.open('rb')) as stream:
            while line := stream.readline((16 << 20) + 1):
                total += len(line); digest.update(line)
                if total > maximum_bytes or len(line) > 16 << 20: raise ValueError('Spark event log size limit exceeded')
                event = json.loads(line)
                if event.get('Event') == 'SparkListenerApplicationStart':
                    app = event.get('App ID'); applications.add(app)
                if application_ids is not None and app not in application_ids: continue
                if event.get('Event') == 'SparkListenerApplicationEnd': ended.add(app)
                if event.get('Event') != 'SparkListenerTaskEnd': continue
                info, m = event['Task Info'], event.get('Task Metrics', {})
                if len(tasks) >= 1_000_000: raise ValueError('Spark task-count limit exceeded')
                tasks.append(max(0, info['Finish Time'] - info['Launch Time']))
                stage_key = f"{app}:{event.get('Stage ID', 'unknown')}:{event.get('Stage Attempt ID', 'unknown')}"
                stage_tasks.setdefault(stage_key, []).append(tasks[-1])
                hosts.add(info['Host']); executors.add(str(info['Executor ID']))
                failed += int(info.get('Failed', False) or info.get('Killed', False))
                for target, source in [('executor_run_ms', 'Executor Run Time'), ('executor_cpu_ns', 'Executor CPU Time'),
                                       ('memory_spill_bytes', 'Memory Bytes Spilled'), ('disk_spill_bytes', 'Disk Bytes Spilled')]:
                    metrics[target] += m.get(source, 0)
                read = m.get('Shuffle Read Metrics', {})
                metrics['shuffle_read_bytes'] += read.get('Remote Bytes Read', 0) + read.get('Local Bytes Read', 0)
                metrics['shuffle_write_bytes'] += m.get('Shuffle Write Metrics', {}).get('Shuffle Bytes Written', 0)
                metrics['task_input_bytes'] += m.get('Input Metrics', {}).get('Bytes Read', 0)
                metrics['task_output_bytes'] += m.get('Output Metrics', {}).get('Bytes Written', 0)
                peak = max(peak, m.get('Peak Execution Memory', 0))
        if digest.hexdigest() in seen_sources: raise ValueError('Duplicate Spark event log')
        seen_sources.add(digest.hexdigest()); sources.append({'name': file.name, 'decompressed_sha256': digest.hexdigest()})
    ordered = sorted(tasks)
    if not tasks or not applications or None in applications: raise ValueError('Incomplete Spark application/task evidence')
    selected = applications if application_ids is None else applications & set(application_ids)
    stages = {}
    for key, durations in stage_tasks.items():
        median = statistics.median(durations)
        stages[key] = {'tasks': len(durations), 'median_ms': median, 'max_ms': max(durations),
                       'max_to_median_ratio': max(durations) / median if median else None}
    return {'applications': sorted(selected), 'completed_applications': sorted(ended), 'complete': bool(selected) and selected <= ended,
            'sources': sources, 'tasks': len(tasks), 'failed_or_killed_attempts': failed,
            'worker_hosts': sorted(hosts), 'executor_ids': sorted(executors), 'metrics': metrics,
            'task_duration_ms': {'median': statistics.median(tasks), 'p95': ordered[min(len(tasks)-1, int(len(tasks)*.95))], 'max': max(tasks)},
            'stage_task_duration_ms': stages, 'largest_task_execution_memory_bytes': peak,
            'interpretation': 'Includes every task attempt and repeated reads; peak is largest task execution memory, not cluster RAM. Event logs do not prove cloud execution.'}
