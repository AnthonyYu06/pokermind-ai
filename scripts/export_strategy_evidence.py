"""Export measured synthetic experiments, curves, split audit and a comparison plot."""
import csv,json,os,shutil
from collections import defaultdict
from pathlib import Path
import numpy as np
root=Path(__file__).resolve().parents[1];data=root/'.data';out=root/'benchmarks/results';out.mkdir(exist_ok=True)
os.environ.setdefault('MPLCONFIGDIR',str(root/'.runtime/matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

reports={}
for name in ['strategy-kuhn','strategy-leduc','behavior']:
 pointer=json.loads((data/(name+'.json')).read_text());path=data/pointer['report'];reports[name]=json.loads(path.read_text());shutil.copyfile(path,out/(name+'.json'))
with (out/'strategy-curves.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=['game','variant','seed','iteration','traversals','samples','seconds','exploitability']);w.writeheader()
 for game in ['kuhn','leduc']:
  for run in reports['strategy-'+game]['runs']:
   for point in run['curve']:w.writerow({'game':game,'variant':run['variant'],'seed':run['seed'],**{k:point[k] for k in ['iteration','traversals','samples','seconds','exploitability']}})
with (out/'behavior-curves.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=['model','seed','epoch','train_nll','validation_nll','gradient_norm']);w.writeheader()
 for run in reports['behavior']['runs']:
  for point in run['curve']:w.writerow({'model':run['model'],'seed':run['seed'],**point})
fig,axes=plt.subplots(2,3,figsize=(14,8),layout='constrained')
colors={'main':'#315b48','no_iteration_weighting':'#b77840','no_network_reset':'#6c7293'}
for col,game in enumerate(['kuhn','leduc']):
 r=reports['strategy-'+game]
 for variant,color in colors.items():
  runs=[x for x in r['runs'] if x['variant']==variant]
  values=np.array([[p['exploitability'] for p in x['curve']] for x in runs]);xs=[p['traversals'] for p in runs[0]['curve']]
  axes[0,col].plot(xs,values.mean(0),label=variant.replace('_',' '),color=color);axes[0,col].fill_between(xs,values.mean(0)-values.std(0),values.mean(0)+values.std(0),color=color,alpha=.13)
 axes[0,col].axhline(r['baselines']['tabular']['exploitability'],color='#444',ls='--',label='tabular baseline')
 axes[0,col].set(title=game.title()+' exact best response',xlabel='Traversals (both players)',ylabel='Exploitability (game chips / hand)');axes[0,col].legend(fontsize=7)
 for run in r['runs']:
  if run['variant']=='main':axes[1,col].plot([p['seconds'] for p in run['curve']],[p['exploitability'] for p in run['curve']],label=f"seed {run['seed']}")
 axes[1,col].set(title=game.title()+' wall time',xlabel='Elapsed CPU run time (seconds)',ylabel='Exploitability (NashConv / 2)');axes[1,col].legend(fontsize=8)
r=reports['behavior'];names=['frequency','logistic','gru','gru_small'];positions=np.arange(4)
for offset,split,color in [(-.18,'test','#315b48'),(.18,'ood','#b77840')]:
 values=[];std=[]
 for name in names:
  points=[r['baselines']['frequency'][split]['nll']] if name=='frequency' else [x['metrics'][split]['nll'] for x in r['runs'] if x['model']==name]
  values.append(np.mean(points));std.append(np.std(points))
 axes[0,2].bar(positions+offset,values,width=.34,label=split,yerr=std,capsize=3,color=color)
axes[0,2].set(xticks=positions,xticklabels=names,title='Behavior: test and unseen-policy loss',ylabel='Negative log-likelihood (nats)');axes[0,2].legend();axes[0,2].tick_params(axis='x',labelsize=8)
for name,color in [('logistic','#7f857c'),('gru','#315b48'),('gru_small','#b77840')]:
 for run in r['runs']:
  if run['model']==name:axes[1,2].plot([p['epoch'] for p in run['curve']],[p['validation_nll'] for p in run['curve']],color=color,alpha=.6,label=name if run['seed']==11 else None)
axes[1,2].set(title='Behavior validation curves',xlabel='Training epoch',ylabel='NLL (nats)');axes[1,2].legend(fontsize=8)
for ax in axes.flat:ax.grid(alpha=.15)
fig.suptitle('PokerMind synthetic research • CPU • three seeds • all neural models unpromoted',fontsize=14)
fig.savefig(out/'strategy-behavior-report.png',dpi=160)
# Audit all frozen shards independently of the dataset builder.
import torch
manifest=r['dataset'];folder=data/'behavior'/'datasets'/manifest['dataset_id'];families=defaultdict(set);observations=defaultdict(set);counts=defaultdict(int)
import hashlib
for filename in manifest['files']:
 split=filename.split('-')[0]
 for row in torch.load(folder/filename,weights_only=True):
  families[row['family']].add(split);key=hashlib.sha256(row['x'].numpy().tobytes()+row['seq'].numpy().tobytes()+row['mask'].numpy().tobytes()).hexdigest();observations[key].add(split);counts[split]+=1
assert all(len(s)==1 for s in families.values()) and all(len(s)==1 for s in observations.values())
(out/'behavior-split-audit.json').write_text(json.dumps({'dataset_id':manifest['dataset_id'],'decision_counts':counts,'distinct_families':len(families),'distinct_observations':len(observations),'cross_split_family_overlap':0,'cross_split_exact_feature_overlap':0},indent=2))
summary={}
for game in ['kuhn','leduc']:
 r=reports['strategy-'+game];summary[game]={k:r[k] for k in ['mean_exploitability','std_exploitability','promotion']};summary[game]['tabular']=r['baselines']['tabular']['exploitability']
r=reports['behavior'];summary['behavior']={'decisions':r['dataset']['decisions'],'hands':r['dataset']['hands'],'frequency_test_nll':r['baselines']['frequency']['test']['nll'],'promotion':r['promotion']}
for name in ['logistic','gru','gru_small']:
 summary['behavior'][name]={split:float(np.mean([x['metrics'][split]['nll'] for x in r['runs'] if x['model']==name])) for split in ['test','ood']}
print(json.dumps(summary,indent=2))
