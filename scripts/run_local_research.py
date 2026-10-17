"""Bounded sequential local suite; interrupts also terminate owned JVM workers."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from training.limits import preflight,protocol


def execute(command,log,remaining):
    with log.open('w') as output:
        process=subprocess.Popen(command,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            code=process.wait(timeout=remaining)
            if code:raise RuntimeError(f'Local job failed ({code}); inspect {log}')
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()


def run(data,publish):
    cfg=protocol();started=time.monotonic();deadline=started+cfg['limits']['max_hours_per_suite']*3600
    preflight(data);logs=data/'logs';logs.mkdir(exist_ok=True);py=sys.executable
    tasks=[('pipeline',[py,'-m','pipelines.spark.local','--data',str(data/'equity'),'--count',str(cfg['equity']['hands']),'--seed',str(cfg['equity']['generation_seed'])]),
           ('equity',[py,'-m','training.local_equity','--data',str(data/'equity')]),
           ('behavior',[py,'-m','training.behavior','--data',str(data/'behavior'),'--profile','local']),
           *[(game,[py,'-m','training.strategy','--data',str(data/'strategy'),'--profile','local','--game',game,'--ablations']) for game in ['kuhn','leduc']],
           ('benchmark',[py,'-m','benchmarks.local','--data',str(data/'equity')]),
           ('app-latency',[py,'scripts/benchmark_app.py','--data',str(data/'equity')]),
           ('scoring',[py,'-m','pipelines.spark.score','--data',str(data/'equity')]),
           ('inference-slices',[py,'scripts/evaluate_local_slices.py','--data',str(data/'equity')]),
           ('publish',[py,'scripts/export_local_research.py','--data',str(data),'--publish',str(publish)])]
    records=[]
    try:
        for name,command in tasks:
            preflight(data);remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Two-hour local suite limit reached')
            print(f'{name}: local CPU only; log {logs/(name+".log")}',flush=True)
            t=time.monotonic();execute(command,logs/(name+'.log'),remaining);records.append({'job':name,'seconds':time.monotonic()-t})
    finally:
        (logs/'suite-status.json').write_text(json.dumps({'completed':records,'seconds':time.monotonic()-started,'cloud_enabled':False,'cost_usd':0},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=ROOT/'.data/research-local-v2');parser.add_argument('--publish',type=Path,default=ROOT/'.data')
    a=parser.parse_args();run(a.data.resolve(),a.publish.resolve())
