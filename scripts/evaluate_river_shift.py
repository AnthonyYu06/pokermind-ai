import json,time,sys
from pathlib import Path
import numpy as np,torch,pyarrow.parquet as pq
sys.path.insert(0,str(Path.cwd()))
from training.river import load_model,measure
from packages.models.river import encode_structure
from packages.pokermind_core.equity import equity
root=Path('.data/research-river-v3');report=json.loads((root/'models/report.json').read_text());spec=report['selected'];model=load_model(root/'models'/spec['model_path'],spec);torch.set_num_threads(2)
prior=Path('.data/research-local-v2/equity');old_report=json.loads((prior/json.loads((prior/'research.json').read_text())['report']).read_text());manifest=json.loads((prior/'lake/datasets'/(old_report['dataset_id']+'.json')).read_text())
def rows():
 for f in manifest['files']:
  if 'split=test/' not in f['path']: continue
  for batch in pq.ParquetFile(prior/f['path']).iter_batches(batch_size=128,columns=['hero','board','equity','deal_family']):
   data=batch.to_pylist();yield torch.tensor([encode_structure(r['hero'],r['board']) for r in data]),torch.tensor([r['equity'] for r in data]),[r['deal_family'] for r in data]
shift=measure(lambda x:model(x).detach().numpy(),rows())
fresh=json.loads((root/'dataset/dataset.json').read_text());sample=[]
for f in fresh['files']:
 if f['split']!='test':continue
 for batch in pq.ParquetFile(root/'dataset'/f['path']).iter_batches(batch_size=40,columns=['hero','board','equity']):
  sample.extend(batch.to_pylist())
  if len(sample)>=40:break
 if len(sample)>=40:break
sample=sample[:40];times={'network_with_encoding':[],'exact':[]};errors=[]
for repeat in range(4):
 for row in sample:
  started=time.perf_counter()
  with torch.inference_mode():prediction=float(model(torch.tensor([encode_structure(row['hero'],row['board'])]))[0])
  elapsed=time.perf_counter()-started
  started=time.perf_counter();reference=equity(row['hero'],row['board'])['equity'];exact=time.perf_counter()-started
  assert abs(reference-row['equity'])<1e-12
  if repeat:times['network_with_encoding'].append(elapsed*1000);times['exact'].append(exact*1000)
  if repeat==0:errors.append(abs(prediction-reference))
result={'protocol':'Post-training diagnostics only; no tuning or promotion. Fixed first 40 test states, one warmup and three timed repetitions. Shift audit uses prior simulator-policy test states excluded from v3 fitting.', 'selected_on':'validation only','selected_model':spec['sha256'],'synthetic_policy_shift':shift,'inference':{k:{'median_ms':float(np.median(v)),'p95_ms':float(np.quantile(v,.95))} for k,v in times.items()},'diagnostic_states':40,'diagnostic_mae':float(np.mean(errors)),'cost_usd':0}
Path('benchmarks/results/river-v3-diagnostics.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
