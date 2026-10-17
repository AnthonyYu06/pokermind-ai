"""Streaming Parquet -> PyTorch, train-only baselines, MLflow, immutable artifacts.

Tiny runs are research artifacts. Promotion needs 500 independent test deals plus
the predeclared quality gates; no automatic production model replacement occurs.
"""
import argparse
import hashlib
import json
import os
import platform
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, IterableDataset

from packages.features.equity import FEATURE_DIM, FEATURES_VERSION
from packages.models.equity import EquityMLP
from packages.pokermind_core.contracts import canonical,digest
from packages.pokermind_core.eligibility import utcnow

ROOT=Path(__file__).resolve().parents[1]


def checked_manifest(path,root):
    manifest=json.loads(path.read_text())
    if manifest['features_version']!=FEATURES_VERSION or manifest['source_kind']!='synthetic':raise ValueError('Incompatible dataset contract')
    for file in manifest['files']:
        resolved=(root/file['path']).resolve()
        if not resolved.is_relative_to(root.resolve()):raise ValueError('Shard path escapes dataset root')
        if hashlib.sha256(resolved.read_bytes()).hexdigest()!=file['sha256']:raise ValueError('Dataset checksum mismatch')
    return manifest


class ParquetRows(IterableDataset):
    def __init__(self,manifest,root,split):
        self.files=[root/f['path'] for f in manifest['files'] if f'split={split}/' in f['path']]

    def __iter__(self):
        import pyarrow.parquet as pq
        for path in self.files:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=128,columns=['features','equity']):
                for row in batch.to_pylist():
                    yield torch.tensor(row['features'],dtype=torch.float32),torch.tensor(row['equity'],dtype=torch.float32)


def loader(manifest,root,split):return DataLoader(ParquetRows(manifest,root,split),batch_size=64,num_workers=0)


def metrics(predict,data):
    count=0;absolute=squared=bias=0.;bins=[{'n':0,'predicted':0.,'reference':0.} for _ in range(5)]
    for x,y in data:
        pred=np.asarray(predict(x),dtype=float).reshape(-1);truth=y.numpy()
        if not np.isfinite(pred).all() or (pred<0).any() or (pred>1).any():raise ValueError('Invalid model output')
        residual=pred-truth
        count+=len(truth);absolute+=np.abs(residual).sum();squared+=(residual**2).sum();bias+=residual.sum()
        for a,b in zip(pred,truth):
            bucket=bins[min(4,int(a*5))];bucket['n']+=1;bucket['predicted']+=float(a);bucket['reference']+=float(b)
    if not count:raise ValueError('A held-out split is empty; generate more independent hands')
    return {'n':count,'mae':float(absolute/count),'rmse':float(np.sqrt(squared/count)),'bias':float(bias/count),
            'calibration_bins':[{'n':b['n'],'predicted':b['predicted']/b['n'],'reference':b['reference']/b['n']} for b in bins if b['n']]}


def baselines(data):
    xtx=np.eye(FEATURE_DIM+1)*1.0;xty=np.zeros(FEATURE_DIM+1);total=count=0
    xtx[-1,-1]=0
    for x,y in data:
        features=np.c_[x.numpy(),np.ones(len(x))];truth=y.numpy()
        xtx+=features.T@features;xty+=features.T@truth
        total+=float(truth.sum());count+=len(truth)
    weights=np.linalg.solve(xtx,xty)
    return total/count,weights


def load_model(path,expected_hash):
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected_hash:raise ValueError('Model checksum mismatch')
    model=EquityMLP()
    model.load_state_dict(torch.load(path,map_location='cpu',weights_only=True))
    model.eval()
    return model


def train(root,*,seeds=(11,23,47),epochs=100,resume=False):
    import mlflow
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    published=json.loads((root/'lake/published.json').read_text())
    path=Path(published['manifest']);manifest=checked_manifest(path,root)
    import uuid
    model_root=root/'models'/manifest['dataset_id'];model_root.mkdir(parents=True,exist_ok=True)
    checkpoint_pointer=model_root/'checkpoint-run.json'
    if resume:
        if not checkpoint_pointer.exists():raise ValueError('No matching resumable run exists')
        output=(model_root/json.loads(checkpoint_pointer.read_text())['directory']).resolve()
        if not output.is_relative_to(model_root.resolve()):raise ValueError('Invalid checkpoint path')
    else:
        output=model_root/f'run-{uuid.uuid4().hex[:12]}'
        output.mkdir()
        checkpoint_pointer.write_text(canonical({'directory':output.name}))
    mlflow.set_tracking_uri('sqlite:///'+str((root/'mlflow.sqlite3').resolve()))
    experiment=mlflow.get_experiment_by_name('PokerMind-river-equity')
    if experiment is None:
        experiment_id=mlflow.create_experiment('PokerMind-river-equity',artifact_location=(root/'mlflow-artifacts').resolve().as_uri())
    else:experiment_id=experiment.experiment_id
    train_mean,ridge=baselines(loader(manifest,root,'train'))
    base={'constant_half':metrics(lambda x:np.full(len(x),.5),loader(manifest,root,'test')),
          'train_mean':metrics(lambda x:np.full(len(x),train_mean),loader(manifest,root,'test')),
          'ridge':metrics(lambda x:np.clip(np.c_[x.numpy(),np.ones(len(x))]@ridge,0,1),loader(manifest,root,'test'))}
    runs=[]
    for seed in seeds:
        torch.manual_seed(seed)
        model=EquityMLP();optimizer=torch.optim.Adam(model.parameters(),lr=.003)
        checkpoint_path=output/f'checkpoint-{seed}.pt';start_epoch=0;best=float('inf');best_state=None;stale=0;curve=[]
        if resume and checkpoint_path.exists():
            state=torch.load(checkpoint_path,map_location='cpu',weights_only=True)
            if state['dataset_id']!=manifest['dataset_id']:raise ValueError('Resume dataset mismatch')
            model.load_state_dict(state['model']);optimizer.load_state_dict(state['optimizer']);torch.set_rng_state(state['rng'])
            start_epoch=state['epoch']+1;best=state['best'];best_state=state['best_state'];curve=state['curve'];stale=state['stale']
        started=time.perf_counter()
        with mlflow.start_run(experiment_id=experiment_id,run_name=f'river-mlp-seed-{seed}') as run:
            mlflow.log_params({'seed':seed,'epochs_budget':epochs,'features':FEATURES_VERSION,'dataset_id':manifest['dataset_id'],
                               'device':'cpu','torch':torch.__version__,'domain':'river / heads-up uniform range','optimizer':'Adam lr=0.003','batch_size':64})
            mlflow.log_artifact(str(path),'dataset')
            for epoch in range(start_epoch,epochs):
                model.train();loss_total=count=0.;gradient_max=0.
                for x,y in loader(manifest,root,'train'):
                    optimizer.zero_grad(set_to_none=True);loss=((model(x)-y)**2).mean()
                    if not torch.isfinite(loss):raise ValueError('Training produced NaN/Inf')
                    loss.backward()
                    norm=float(torch.nn.utils.clip_grad_norm_(model.parameters(),5,error_if_nonfinite=True))
                    gradient_max=max(gradient_max,norm);optimizer.step()
                    loss_total+=float(loss.detach())*len(y);count+=len(y)
                model.eval()
                with torch.no_grad():validation=metrics(lambda x:model(x).numpy(),loader(manifest,root,'validation'))
                curve.append({'epoch':epoch,'train_mse':loss_total/count,'validation_mae':validation['mae'],'gradient_norm_max':gradient_max})
                mlflow.log_metrics(curve[-1],step=epoch)
                if validation['mae']<best-1e-5:
                    best=validation['mae'];best_state={k:v.detach().clone() for k,v in model.state_dict().items()};stale=0
                else:stale+=1
                torch.save({'model':model.state_dict(),'optimizer':optimizer.state_dict(),'rng':torch.get_rng_state(),
                            'epoch':epoch,'dataset_id':manifest['dataset_id'],'best':best,'best_state':best_state,'curve':curve,'stale':stale},checkpoint_path)
                if stale>=20:break
            model.load_state_dict(best_state);model.eval()
            with torch.no_grad():result=metrics(lambda x:model(x).numpy(),loader(manifest,root,'test'))
            weights_temp=output/f'equity-{seed}.tmp';torch.save(model.state_dict(),weights_temp)
            checksum=hashlib.sha256(weights_temp.read_bytes()).hexdigest()
            weights_path=output/f'equity-{seed}-{checksum[:16]}.pt'
            os.replace(weights_temp,weights_path)
            reloaded=load_model(weights_path,checksum)
            x,_=next(iter(loader(manifest,root,'test')))
            with torch.no_grad():
                if not torch.equal(model(x),reloaded(x)):raise ValueError('Artifact load/score parity failure')
            row={'seed':seed,'run_id':run.info.run_id,'test':result,'best_validation_mae':best,'curve':curve,
                 'seconds':time.perf_counter()-started,'model_path':str(weights_path.relative_to(root)),'sha256':checksum,'model_bytes':weights_path.stat().st_size}
            runs.append(row)
            mlflow.log_metrics({f'test_{k}':result[k] for k in ('mae','rmse','bias')})
            mlflow.log_dict({'baselines':base,'report':row},'evaluation.json')
            mlflow.log_artifact(str(weights_path),'model');mlflow.log_artifact(str(checkpoint_path),'checkpoint')
    maes=[r['test']['mae'] for r in runs]
    report={'created_at':utcnow().isoformat(),'dataset_id':manifest['dataset_id'],'manifest_hash':digest(manifest),
            'domain':'Synthetic river states; two active players; uniform legal random opponent; no betting EV',
            'status':'research-only','promotion':'Not promoted: tiny data does not satisfy independent-test-deal support gate',
            'thresholds':{'max_mae':.05,'max_absolute_bias':.03,'relative_gain_vs_best_baseline':.1,'min_independent_test_deals':500},
            'counts':manifest['counts'],'baselines':base,'runs':runs,'mean_test_mae':float(np.mean(maes)),'std_test_mae':float(np.std(maes)),
            'hardware':platform.platform(),'device':'cpu','model':'MLP 55 → 64 → 32 → 1, sigmoid','features_version':FEATURES_VERSION,
            'limitations':['Exploratory three-seed tiny run, not final research evidence','No preflop, flop, turn or multiway model','No neural promotion','No GPU or distributed training','Synthetic policy distribution only']}
    report_path=output/f'report-{digest(report)[:16]}.json';report_path.write_text(json.dumps(report,indent=2))
    tmp=root/'research.tmp';tmp.write_text(canonical({'report':str(report_path.relative_to(root))}));os.replace(tmp,root/'research.json')
    print(json.dumps({k:v for k,v in report.items() if k!='runs'},indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=ROOT/'.data');p.add_argument('--epochs',type=int,default=100);p.add_argument('--resume',action='store_true');args=p.parse_args()
    try:train(args.data.resolve(),epochs=args.epochs,resume=args.resume)
    except (OSError,ValueError) as exc:raise SystemExit(f'PokerMind training: {exc}. Run make pipeline-local first.')
