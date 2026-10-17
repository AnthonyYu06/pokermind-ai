"""Verify and publish the larger synthetic-only evidence without changing serving defaults."""
import argparse
import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from packages.pokermind_core.contracts import canonical
from services.api.releases import check_report


def run(data,publish):
    import numpy as np
    import torch
    import pyarrow.parquet as pq
    out=ROOT/'benchmarks/results';out.mkdir(exist_ok=True)
    reports={};sources={}
    for name,folder in [('research','equity'),('behavior','behavior'),('strategy-kuhn','strategy'),('strategy-leduc','strategy')]:
        base=data/folder;relative=json.loads((base/(name+'.json')).read_text())['report']
        checksum,r=check_report(base,name,relative);reports[name]=r
        sources[name]={'report':str((base/relative).relative_to(ROOT)),'sha256':checksum}
        (out/('local-'+name+'.json')).write_text(json.dumps(r,indent=2)+'\n')
    audits={}
    for name in ['research','behavior']:
        r=reports[name];families=defaultdict(set);features=defaultdict(set);counts=defaultdict(int);hands=defaultdict(set)
        if name=='behavior':
            manifest=r['dataset'];folder=data/'behavior'/'behavior/datasets'/manifest['dataset_id']
            for file,sha in manifest['files'].items():
                assert hashlib.sha256((folder/file).read_bytes()).hexdigest()==sha
                split=file.split('-')[0]
                for row in torch.load(folder/file,weights_only=True):
                    families[row['family']].add(split);hands[split].add(row['hand']);counts[split]+=1
                    feature=hashlib.sha256(row['x'].numpy().tobytes()+row['seq'].numpy().tobytes()+row['mask'].numpy().tobytes()).hexdigest()
                    features[feature].add(split)
        else:
            from training.equity import checked_manifest
            base=data/'equity';manifest=checked_manifest(base/'lake/datasets'/f'{r["dataset_id"]}.json',base)
            for file in manifest['files']:
                split=next(s for s in ['train','validation','test'] if 'split='+s+'/' in file['path'])
                for batch in pq.ParquetFile(base/file['path']).iter_batches(batch_size=128):
                    for row in batch.to_pylist():
                        families[row['deal_family']].add(split);hands[split].add(row['hand_id']);counts[split]+=1
                        # Audit both the canonical card identity and the features actually fed to the MLP.
                        from packages.models.card_set import encode_cards
                        for label,values in [('mlp',np.asarray(row['features'],dtype=np.float32)),('cards',encode_cards(row['hero'],row['board']))]:
                            features[label+hashlib.sha256(values.tobytes()).hexdigest()].add(split)
        overlap=sum(len(s)>1 for s in families.values());feature_overlap=sum(len(s)>1 for s in features.values())
        audits[name]={'dataset_id':manifest['dataset_id'],'decision_counts':dict(counts),'hands':{k:len(v) for k,v in hands.items()},
                      'independent_families':{s:sum(s in v for v in families.values()) for s in counts},
                      'cross_split_family_overlap':overlap,'cross_split_exact_feature_overlap':feature_overlap}
        if overlap or feature_overlap:raise ValueError(f'{name} split audit failed: {audits[name]}')
    (out/'local-split-audit.json').write_text(json.dumps(audits,indent=2)+'\n')
    def stats(values):return {'mean':float(np.mean(values)),'std':float(np.std(values)),'n':len(values)}
    equity=reports['research'];groups=[]
    for name in equity['protocol']['equity']['models']:
        for fraction in equity['protocol']['equity']['data_fractions']:
            runs=[r for r in equity['comparisons'] if r['model']==name and r['fraction']==fraction]
            groups.append({'model':name,'fraction':fraction,'mae':stats([r['test']['mae'] for r in runs]),
                           'bias':stats([r['test']['bias'] for r in runs]),'seconds':stats([r['seconds'] for r in runs]),
                           'model_bytes':runs[0]['model_bytes'],'batch_latency_ms_p95':stats([r['batch_latency_ms']['p95'] for r in runs])})
    behavior=reports['behavior'];behavior_groups=[]
    audited_behavior_gates={**behavior['promotion_gates'],'independent_support':audits['behavior']['independent_families']['test']>=behavior['dataset']['config']['minimum_independent_test_families']}
    (out/'local-gate-audit.json').write_text(json.dumps({'behavior_source':sources['behavior'],
        'reported_generated_test_families':behavior['independent_test_families'],'retained_test_families':audits['behavior']['independent_families']['test'],
        'audited_gates':audited_behavior_gates,'promotion_eligible':all(audited_behavior_gates.values()),
        'correction':'Count only families with retained examples; initial report counted generated families with zero retained observations. Raw report is preserved unchanged. No predictions, thresholds or split membership changed.'},indent=2)+'\n')
    for name in ['logistic','gru','gru_small']:
        runs=[r for r in behavior['runs'] if r['model']==name]
        behavior_groups.append({'model':name,**{split:stats([r['metrics'][split]['nll'] for r in runs]) for split in ['test','ood']},
                                'test_ece':stats([r['metrics']['test']['ece'] for r in runs]),'model_bytes':runs[0]['model_bytes']})
    strategies=[]
    for game in ['kuhn','leduc']:
        r=reports['strategy-'+game]
        strategies.append({'game':game,'main':stats([x['metrics']['exploitability'] for x in r['runs'] if x['variant']=='main']),
                           'variants':{v:stats([x['metrics']['exploitability'] for x in r['runs'] if x['variant']==v]) for v in ['no_iteration_weighting','no_network_reset']},
                           'tabular':r['baselines']['tabular']['exploitability'],'promotion':r['promotion']})
    summary={'version':'local-research-v2','status':'measured','cloud_enabled':False,'cost_usd':0,'source_kind':'synthetic','sources':sources,'audits':audits,
             'equity':{'hands':6000,'groups':groups,'baselines':{k:v['mae'] for k,v in equity['baselines'].items()},'gates':equity['promotion_gates']},
             'behavior':{'generated_hands':8000,'groups':behavior_groups,'frequency':{s:behavior['baselines']['frequency'][s]['nll'] for s in ['test','ood']},'gates':audited_behavior_gates},
             'strategy':strategies,'interpretation':'All neural models remain unpromoted. Sampling intervals and seed variation do not measure synthetic-to-real domain error.'}
    benchmark=out/'local-pipeline-comparison.json'
    if benchmark.exists():
        measured=json.loads(benchmark.read_text());summary['pipeline']={'output_equivalence':measured['output_equivalence'],'groups':[],'limitations':measured['limitations']}
        for engine,partitions in [('streaming',1),('spark',2),('spark',4)]:
            runs=[r for r in measured['records'] if r['engine']==engine and r['partitions']==partitions and r['warmup_or_measured']=='measured']
            summary['pipeline']['groups'].append({'engine':engine,'partitions':partitions,'seconds':stats([r['total_seconds'] for r in runs])})
        if any(g['seconds']['n']!=3 for g in summary['pipeline']['groups']):raise ValueError('All benchmark repetitions must finish before publication')
    app=out/'local-app-latency.json'
    if app.exists():summary['app_latency']=json.loads(app.read_text())
    scoring=data/'equity/lake/scoring-report.json'
    if scoring.exists():
        summary['scoring']=json.loads(scoring.read_text())
        (out/'local-large-scores.json').write_text(json.dumps(summary['scoring'],indent=2)+'\n')
    for key,filename in [('inference_diagnostics','local-inference-slices.json'),('device_compatibility','device-compatibility.json')]:
        if (out/filename).exists():summary[key]={'artifact':filename,'sha256':hashlib.sha256((out/filename).read_bytes()).hexdigest()}
    for family,runs in [('equity',equity['comparisons']),('behavior',behavior['runs']),('strategy',[dict(x,game=g) for g in ['kuhn','leduc'] for x in reports['strategy-'+g]['runs']])]:
        rows=[]
        for run in runs:
            for point in run['curve']:rows.append({**{k:run[k] for k in ['model','fraction','seed','variant','game'] if k in run},**point})
        with (out/('local-'+family+'-curves.csv')).open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));writer.writeheader();writer.writerows(rows)
    os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.runtime/matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(12,8),layout='constrained')
    names=['mlp','card_set','card_set_small'];positions=np.arange(3)
    for fraction,offset,color in [(1.,-.18,'#315b48'),(.25,.18,'#b77840')]:
        selected=[g for g in groups if g['fraction']==fraction]
        axes[0,0].bar(positions+offset,[g['mae']['mean'] for g in selected],yerr=[g['mae']['std'] for g in selected],width=.34,label=f'{fraction:.0%} training families',color=color,capsize=3)
    axes[0,0].axhline(equity['baselines']['histogram_boosted']['mae'],ls='--',color='#555',label='Full-data boosted baseline')
    axes[0,0].set(xticks=positions,xticklabels=names,ylabel='Held-out mean absolute error',title='River equity · 358 independent test deals');axes[0,0].legend(fontsize=7)
    for split,offset,color in [('test',-.18,'#315b48'),('ood',.18,'#b77840')]:
        values=[summary['behavior']['frequency'][split]]+[g[split]['mean'] for g in behavior_groups]
        errors=[0]+[g[split]['std'] for g in behavior_groups]
        axes[0,1].bar(np.arange(4)+offset,values,yerr=errors,width=.34,label=split,color=color,capsize=3)
    axes[0,1].set(xticks=np.arange(4),xticklabels=['frequency']+[g['model'] for g in behavior_groups],ylabel='NLL (nats)',title='Behavior · held-out and unseen generator');axes[0,1].legend(fontsize=8)
    for col,game in enumerate(['kuhn','leduc']):
        r=reports['strategy-'+game]
        for variant in ['main','no_iteration_weighting','no_network_reset']:
            runs=[x for x in r['runs'] if x['variant']==variant];values=np.array([[p['exploitability'] for p in x['curve']] for x in runs]);xs=[p['traversals'] for p in runs[0]['curve']]
            axes[1,col].plot(xs,values.mean(0),label=variant.replace('_',' '));axes[1,col].fill_between(xs,values.mean(0)-values.std(0),values.mean(0)+values.std(0),alpha=.12)
        axes[1,col].axhline(r['baselines']['tabular']['exploitability'],color='#444',ls='--',label='tabular')
        axes[1,col].set(xlabel='Traversals (both players)',ylabel='Exploitability (game chips / hand)',title=game.title()+' · independent exact best response');axes[1,col].legend(fontsize=7)
    for ax in axes.flat:ax.grid(axis='y',alpha=.15)
    fig.suptitle('Larger local research · three seeds · synthetic data · neural gates failed\nError bars / bands: across-seed standard deviation',fontsize=13)
    fig.savefig(out/'local-research-report.png',dpi=160);plt.close(fig)
    payload=canonical(summary);checksum=hashlib.sha256(payload.encode()).hexdigest()
    (out/'local-research-summary.json').write_text(payload+'\n')
    publish.mkdir(parents=True,exist_ok=True);folder=publish/'evidence';folder.mkdir(exist_ok=True)
    name='local-research-'+checksum[:16]+'.json';(folder/name).write_text(payload)
    pointer=publish/'local-research.tmp';pointer.write_text(canonical({'path':'evidence/'+name,'sha256':checksum}));pointer.replace(publish/'local-research.json')
    print(canonical({'published':str(publish/'local-research.json'),'audits':audits,'neural_promotions':0}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=ROOT/'.data/research-local-v2');parser.add_argument('--publish',type=Path,default=ROOT/'.data')
    args=parser.parse_args();run(args.data.resolve(),args.publish.resolve())
