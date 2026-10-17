"""Bounded single-machine materialized-pipeline comparison with raw Spark metrics."""
import argparse
import hashlib
import json
import os
import platform
import threading
import time
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path
from statistics import median
from types import SimpleNamespace
import resource
from packages.pokermind_core.contracts import canonical
from packages.pokermind_core.equity import equity
from services.api.store import Store
from pipelines.spark.local import run as spark_run,reconstruct_partition,seed_training
from training.limits import protocol,preflight


@lru_cache(maxsize=2048)
def label(hero,board):return equity(list(hero),list(board))['equity']


def streaming(root,count,seed):
    label.cache_clear();started=time.perf_counter();store=Store(root);seed_training(store,count,seed)
    output=root/'streaming'/uuid.uuid4().hex;output.mkdir(parents=True)
    observed=hashlib.sha256();labeled=hashlib.sha256();gold=Counter();decisions=labels=hands=0
    with store.connect() as conn, (output/'bronze.jsonl').open('w') as bronze, (output/'silver.jsonl').open('w') as silver, (output/'labels.jsonl').open('w') as target:
        for row in conn.execute("SELECT * FROM hands WHERE source_kind='synthetic' AND quality='valid' ORDER BY hand_id"):
            item=SimpleNamespace(**dict(row));hands+=1;bronze.write(canonical(dict(row))+'\n');local=[]
            for obs in reconstruct_partition([item]):
                decisions+=1;observed.update((obs[0]+':'+obs[7]+'\n').encode());silver.write(canonical(obs)+'\n')
                gold[item.source_kind+':'+obs[4]+':'+obs[8]]+=1
                if obs[11]:
                    value=label(tuple(sorted(obs[9])),tuple(sorted(obs[10])));local.append((obs[0],value));labels+=1
                    target.write(canonical({'decision_id':obs[0],'features':obs[11],'family':obs[12],'split':obs[13],'equity':value})+'\n')
            for did,value in sorted(local):labeled.update(f'{did}:{value:.12f}\n'.encode())
    (output/'gold.json').write_text(canonical(gold))
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    (output/'manifest.json').write_text(canonical(manifest));pointer=root/'streaming'/'published.tmp';pointer.write_text(canonical({'directory':output.name,'files':manifest}));os.replace(pointer,pointer.with_suffix('.json'))
    return {'unique_hands':hands,'decisions':decisions,'label_rows':labels,'output_hash':observed.hexdigest(),'label_hash':labeled.hexdigest(),'gold_counts':dict(gold),
            'total_seconds':time.perf_counter()-started,'output_bytes':sum(p.stat().st_size for p in output.iterdir()),'startup_seconds':0.,'worker_count':1,'executor_count':1}


def spark_metrics(root,app):
    totals=Counter();durations=[];identities=set()
    for file in (root/'lake/spark-events'/('eventlog_v2_'+app)).glob('events_*'):
        for line in file.open():
            event=json.loads(line)
            if event.get('Event')!='SparkListenerTaskEnd':continue
            info=event['Task Info'];metrics=event.get('Task Metrics',{});identities.add((info.get('Executor ID'),info.get('Host')))
            durations.append(info['Finish Time']-info['Launch Time'])
            for key in ('Executor Run Time','Executor CPU Time','Memory Bytes Spilled','Disk Bytes Spilled'):totals[key]+=metrics.get(key,0)
            read=metrics.get('Shuffle Read Metrics',{});totals['shuffle_read_bytes']+=read.get('Remote Bytes Read',0)+read.get('Local Bytes Read',0)
            totals['shuffle_write_bytes']+=metrics.get('Shuffle Write Metrics',{}).get('Shuffle Bytes Written',0)
            totals['peak_task_execution_memory_bytes']=max(totals['peak_task_execution_memory_bytes'],metrics.get('Peak Execution Memory',0))
    return {**dict(totals),'tasks':len(durations),'task_duration_median_ms':median(durations),'task_duration_max_ms':max(durations),
            'max_to_median_task_ratio':max(durations)/max(1,median(durations)),'executor_identities':[list(i) for i in identities]}


def measured(fn):
    result=fn()
    # Python process high-water mark, not a claim about the whole Spark JVM.
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {**result,'python_process_highwater_rss_bytes':rss if platform.system()=='Darwin' else rss*1024}


def run(root):
    cfg=protocol();count=cfg['equity']['hands'];seed=cfg['equity']['generation_seed'];records=[];expected=None
    out=Path('benchmarks/results/local-pipeline-comparison.json')
    for engine,partitions in [('streaming',1),('spark',2),('spark',4)]:
        for repetition in range(cfg['benchmark']['warmups']+cfg['benchmark']['repetitions']):
            preflight(root)
            if engine=='streaming':result=measured(lambda:streaming(root,count,seed))
            else:
                def job():
                    spark_run(root,count=count,seed=seed,partitions=partitions)
                    r=json.loads((root/'lake/pipeline-report.json').read_text());r['spark_metrics']=spark_metrics(root,r['spark_application_id']);return r
                result=measured(job)
            identity={k:result[k] for k in ('unique_hands','decisions','label_rows','output_hash','label_hash','gold_counts')}
            if expected is None:expected=identity
            if identity!=expected:raise AssertionError('Materialized streaming/Spark outputs differ')
            row={'engine':engine,'partitions':partitions,'repetition':repetition,'warmup_or_measured':'warmup' if repetition==0 else 'measured',
                 'hardware':platform.platform(),'cost_usd':0,'cost_status':'local CPU; no cloud','throughput_hands_per_second':count/result['total_seconds'],**result}
            records.append(row)
            out.write_text(json.dumps({'protocol':cfg,'execution':'one Mac; local Spark threads are not worker machines','records':records,'output_equivalence':True,
                                       'limitations':['Warm filesystem/JVM state; generation and idempotent import included','Python writes JSONL snapshots; Spark writes transactional Delta and frozen Parquet; includes format overhead','Other bounded local research may contend for CPU','No multi-machine speedup or cloud cost crossover claim']},indent=2)+'\n')
            print(canonical({'engine':engine,'partitions':partitions,'repetition':repetition,'seconds':result['total_seconds']}),flush=True)
    return records


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);a=p.parse_args();run(a.data.resolve())
