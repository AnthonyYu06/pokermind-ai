"""Frozen test diagnostics and end-to-end inference timing, without retraining."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import random
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import pyarrow.parquet as pq
import torch
from packages.features.equity import encode
from packages.models.card_set import CardSet,encode_cards
from packages.models.equity import EquityMLP
from packages.pokermind_core.cards import DECK,rank,reference_rank
from packages.pokermind_core.contracts import digest
from packages.pokermind_core.equity import equity
from services.api.releases import check_report
from training.equity import checked_manifest


def run(root):
    torch.set_num_threads(2);cfg=json.loads((ROOT/'configs/local-inference-evaluation.json').read_text())
    relative=json.loads((root/'research.json').read_text())['report'];sha,report=check_report(root,'research',relative)
    manifest=checked_manifest(root/'lake/datasets'/f'{report["dataset_id"]}.json',root)
    rows=[]
    for file in manifest['files']:
        if 'split=test/' not in file['path']:continue
        for batch in pq.ParquetFile(root/file['path']).iter_batches(batch_size=128):rows.extend(batch.to_pylist())
    if len(rows)>50000:raise ValueError('Diagnostic profile exceeds local row bound')
    groups=defaultdict(list)
    for i,row in enumerate(rows):
        board=row['board'];paired=max(Counter(c[0] for c in board).values())>1;suits=max(Counter(c[1] for c in board).values())
        groups['river_all'].append(i);groups['paired_board' if paired else 'unpaired_board'].append(i)
        if suits>=3:groups['three_plus_same_suit_board'].append(i)
        if suits>=4:groups['four_plus_same_suit_board'].append(i)
        if reference_rank(row['hero']+board)[0]>=4:groups['hero_straight_or_better'].append(i)
    models={};slices=[]
    for run in report['comparisons']:
        if run['fraction']!=1.:continue
        model=EquityMLP() if run['model']=='mlp' else CardSet(run['width'])
        model.load_state_dict(torch.load(root/run['model_path'],weights_only=True,map_location='cpu'));model.eval()
        def predict(row,model=model,name=run['model']):
            features=encode(row['hero'],row['board']) if name=='mlp' else encode_cards(row['hero'],row['board'])
            with torch.inference_mode():return float(model(torch.tensor(np.asarray(features),dtype=torch.float32).unsqueeze(0))[0])
        predictions=[predict(row) for row in rows]
        for name,indices in groups.items():
            error=np.array([predictions[i]-rows[i]['equity'] for i in indices])
            slices.append({'model':run['model'],'seed':run['seed'],'slice':name,'decisions':len(indices),
                'independent_families':len({rows[i]['deal_family'] for i in indices}),'mae':float(np.abs(error).mean()),'rmse':float(np.sqrt((error**2).mean())),'bias':float(error.mean())})
        if run['seed']==cfg['model_seed']:models[run['model']]=predict
    visible={digest([sorted(r['hero']),sorted(r['board'])]):r for r in rows}
    states=[visible[k] for k in sorted(visible)[:cfg['timing_states']]]
    def monte_carlo(row,budget):
        rng=random.Random(cfg['simulation_seed']+int(digest([row['hero'],row['board']])[:8],16));known=row['hero']+row['board']
        deck=[c for c in DECK if c not in known];own=rank(known);values=[]
        for _ in range(budget):
            other=rank(rng.sample(deck,2)+row['board']);values.append(1. if own<other else .5 if own==other else 0.)
        return sum(values)/budget
    methods={'exact_reference':lambda r:equity(r['hero'],r['board'])['equity'],
             **{f'monte_carlo_{n}':lambda r,n=n:monte_carlo(r,n) for n in cfg['monte_carlo_samples']},**models}
    records=[]
    for name,method in methods.items():
        for repetition in range(cfg['warmups']+cfg['repetitions']):
            timings=[];errors=[]
            for row in states:
                started=time.perf_counter();value=method(row);timings.append(1000*(time.perf_counter()-started));errors.append(value-row['equity'])
            if name=='exact_reference' and max(abs(x) for x in errors)>1e-12:raise ValueError('Exact reference disagrees with frozen labels')
            records.append({'method':name,'repetition':repetition,'warmup':repetition<cfg['warmups'],'states':len(states),'milliseconds':timings,
                            'median_ms':float(np.median(timings)),'p95_ms':float(np.quantile(timings,.95)),'mae':float(np.abs(errors).mean())})
    result={'protocol':cfg,'source_report_sha256':sha,'dataset_id':report['dataset_id'],'slices':slices,'timings':records,
            'interpretation':'Post-training descriptive slices; no threshold/model selection changes. Timing includes encoding and one-state inference, excludes artifact loading. Seed 11 is preselected. Small slices are not quality certificates; river-only contract.',
            'timing_state_hash':digest([[r['hero'],r['board']] for r in states]),'cost_usd':0}
    path=ROOT/'benchmarks/results/local-inference-slices.json';path.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'report':str(path),'slice_counts':{k:len(v) for k,v in groups.items()},'records':len(records)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=ROOT/'.data/research-local-v2/equity')
    run(parser.parse_args().data.resolve())
