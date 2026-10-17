"""Export measured synthetic-only reports and an experiment figure."""
import csv,json,os,shutil,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR',str(root/'.runtime/matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
out=root/'benchmarks/results';out.mkdir(exist_ok=True,parents=True)
pointer=json.loads((root/'.data/research.json').read_text())
report=json.loads((root/'.data'/pointer['report']).read_text())
shutil.copyfile(root/'.data'/pointer['report'],out/'equity-patched.json')
for source,name in [('lake/pipeline-report.json','local-pipeline.json'),('lake/scoring-report.json','local-scores.json')]:
    shutil.copyfile(root/'.data'/source,out/name)
with (out/'learning-curves.csv').open('w') as f:
    writer=csv.DictWriter(f,fieldnames=['seed','epoch','train_mse','validation_mae','gradient_norm_max']);writer.writeheader()
    for run in report['runs']:
        for point in run['curve']:writer.writerow({'seed':run['seed'],**point})
fig,(ax,bar)=plt.subplots(1,2,figsize=(10,4.2),layout='constrained')
for run in report['runs']:
    ax.plot([p['epoch']+1 for p in run['curve']],[p['validation_mae'] for p in run['curve']],label=f"seed {run['seed']}")
ax.set(xlabel='Training epoch',ylabel='Validation MAE (pot-share units)',title='Validation-selected checkpoints');ax.legend();ax.grid(alpha=.2)
names=['0.5','Train mean','Ridge','MLP (3 seeds)'];values=[report['baselines'][k]['mae'] for k in ['constant_half','train_mean','ridge']]+[report['mean_test_mae']]
bar.bar(names,values,color=['#a5aaa2']*3+['#3e7251'],yerr=[0,0,0,report['std_test_mae']],capsize=4)
bar.axhline(.05,color='#a65d32',linestyle='--',label='MAE gate = 0.05');bar.legend(fontsize=8)
bar.set(ylabel='Held-out MAE (pot-share units)',title='39 test decisions / 19 independent deals');bar.tick_params(axis='x',labelsize=8)
fig.suptitle('Synthetic river equity • CPU • research only, not promoted',fontsize=13)
fig.savefig(out/'equity-report.png',dpi=170)
hashes={}
for folder in ['packages','services','training','pipelines','evaluation','scripts','tests']:
    for p in sorted((root/folder).rglob('*.py')):
        hashes[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
(out/'source-manifest.json').write_text(json.dumps({'git_commit':None,'state':'uncommitted local implementation','python_source_hashes':hashes},indent=2))
print('Saved reports, raw curves, figure, and source checksums to benchmarks/results.')
