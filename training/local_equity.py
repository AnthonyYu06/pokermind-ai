"""Frozen larger local equity comparison; test data never selects hyperparameters."""
import argparse
import copy
import hashlib
import json
import math
import platform
import random
import time
import uuid
from collections import defaultdict
from pathlib import Path
import numpy as np
import torch
from torch.utils.data import IterableDataset,DataLoader
from packages.models.card_set import CardSet,encode_cards,VERSION
from packages.models.equity import EquityMLP
from packages.pokermind_core.contracts import canonical,digest
from packages.pokermind_core.eligibility import utcnow
from training.equity import checked_manifest,baselines,loader
from training.artifacts import immutable_json
from training.limits import protocol,preflight


def freeze(root,manifest):
    import pyarrow.parquet as pq
    folder=root/'models'/('card-shards-'+manifest['dataset_id']);folder.mkdir(parents=True,exist_ok=True)
    pointer=folder/'manifest.json'
    if pointer.exists():
        data=json.loads(pointer.read_text())
        for file,sha in data['files'].items():
            if hashlib.sha256((folder/file).read_bytes()).hexdigest()!=sha:raise ValueError('Card shard checksum mismatch')
        return folder,data
    buffers=defaultdict(list);files={};counts=defaultdict(int)
    def flush(split):
        if not buffers[split]:return
        p=folder/f'{split}-{counts[split]:08d}.pt';torch.save(buffers[split],p);files[p.name]=hashlib.sha256(p.read_bytes()).hexdigest();buffers[split]=[]
    for file in manifest['files']:
        split=next(s for s in ('train','validation','test') if 'split='+s+'/' in file['path'])
        for batch in pq.ParquetFile(root/file['path']).iter_batches(batch_size=128):
            for row in batch.to_pylist():
                buffers[split].append({'x':torch.tensor(row['features'],dtype=torch.float32),'cards':torch.tensor(encode_cards(row['hero'],row['board'])),
                                       'y':float(row['equity']),'family':row['deal_family'],'hand':row['hand_id']})
                counts[split]+=1
                if len(buffers[split])>=128:flush(split)
    for split in buffers:flush(split)
    result={'dataset_id':manifest['dataset_id'],'encoder':VERSION,'files':files,'counts':dict(counts)}
    pointer.write_text(canonical(result));return folder,result


class Rows(IterableDataset):
    def __init__(self,folder,manifest,split,fraction=1.,seed=None):self.folder,self.manifest,self.split,self.fraction,self.seed=folder,manifest,split,fraction,seed
    def __iter__(self):
        files=sorted(f for f in self.manifest['files'] if f.startswith(self.split+'-'));rng=random.Random(self.seed)
        if self.seed is not None:rng.shuffle(files)
        for file in files:
            rows=torch.load(self.folder/file,weights_only=True)
            if self.seed is not None:rng.shuffle(rows)
            for row in rows:
                # Nested training subsets are fixed by deal family, independent of model seed.
                if self.split=='train' and int(row['family'][8:16],16)/2**32>=self.fraction:continue
                yield row


def batches(folder,manifest,split,fraction=1.,seed=None):
    return DataLoader(Rows(folder,manifest,split,fraction,seed),batch_size=128,num_workers=0)


def predict(model,row,name):return model(row['x']) if name=='mlp' else model(row['cards'])


def evaluate(model,data,name,baseline=None):
    predictions=[];labels=[];families=[]
    with torch.inference_mode():
        for row in data:
            p=baseline(row['x'].numpy()) if baseline else predict(model,row,name).numpy()
            predictions.extend(np.asarray(p).tolist());labels.extend(row['y'].tolist());families.extend(row['family'])
    p=np.array(predictions);y=np.array(labels);e=p-y
    if not len(y) or not np.isfinite(p).all() or (p<0).any() or (p>1).any():raise ValueError('Invalid evaluation outputs')
    grouped=defaultdict(list)
    for family,error in zip(families,np.abs(e)):grouped[family].append(error)
    sums=np.array([sum(v) for v in grouped.values()]);counts=np.array([len(v) for v in grouped.values()]);rng=np.random.default_rng(101)
    boot=[]
    for _ in range(500):
        ids=rng.integers(len(sums),size=len(sums));boot.append(sums[ids].sum()/counts[ids].sum())
    bins=[]
    for lo in np.arange(0,1,.2):
        mask=(p>=lo)&(p<(lo+.2) if lo<.8 else p<=1)
        if mask.any():bins.append({'lower':float(lo),'n':int(mask.sum()),'predicted':float(p[mask].mean()),'reference':float(y[mask].mean())})
    return {'n':len(y),'independent_families':len(grouped),'mae':float(abs(e).mean()),'rmse':float(np.sqrt((e**2).mean())),
            'bias':float(e.mean()),'mae_cluster_ci95':np.quantile(boot,[.025,.975]).tolist(),'calibration_bins':bins}


def fit(folder,manifest,name,width,fraction,seed,config,destination,resume=None):
    torch.manual_seed(seed);torch.set_num_threads(2);torch.use_deterministic_algorithms(True)
    model=EquityMLP() if name=='mlp' else CardSet(width);optimizer=torch.optim.Adam(model.parameters(),lr=.003)
    signature={'dataset':manifest['dataset_id'],'model':name,'width':width,'fraction':fraction,'seed':seed,'encoder':VERSION}
    start=0;best=float('inf');best_state=None;stale=0;curve=[]
    if resume:
        saved=torch.load(resume,map_location='cpu',weights_only=True)
        if saved['signature']!=signature:raise ValueError('Equity checkpoint configuration mismatch')
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer']);torch.set_rng_state(saved['rng'])
        start,best,best_state,stale,curve=saved['epoch'],saved['best'],saved['best_state'],saved['stale'],saved['curve']
    for epoch in range(start,config['epochs']):
        if stale>=config['early_stopping_patience']:break
        model.train();total=count=0;norm_max=0
        for row in batches(folder,manifest,'train',fraction,seed+epoch):
            loss=((predict(model,row,name)-row['y'])**2).mean()
            if not torch.isfinite(loss):raise FloatingPointError('Non-finite equity loss')
            optimizer.zero_grad();loss.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5,error_if_nonfinite=True);optimizer.step()
            total+=float(loss.detach())*len(row['y']);count+=len(row['y']);norm_max=max(norm_max,float(norm))
        model.eval()
        # Validation need not bootstrap each epoch; collect only absolute residual sum.
        with torch.inference_mode():
            absolute=n=0
            for row in batches(folder,manifest,'validation'):
                absolute+=float((predict(model,row,name)-row['y']).abs().sum());n+=len(row['y'])
        val=absolute/n;curve.append({'epoch':epoch+1,'train_mse':total/count,'validation_mae':val,'gradient_norm_max':norm_max})
        if val<best-1e-5:best=val;best_state=copy.deepcopy(model.state_dict());stale=0
        else:stale+=1
        temp=destination/'checkpoint.tmp';torch.save({'signature':signature,'epoch':epoch+1,'model':model.state_dict(),'optimizer':optimizer.state_dict(),
                  'rng':torch.get_rng_state(),'best':best,'best_state':best_state,'stale':stale,'curve':curve},temp);temp.replace(destination/'checkpoint.pt')
    model.load_state_dict(best_state);model.eval();return model,curve,best


def run(root):
    import mlflow
    from sklearn.ensemble import HistGradientBoostingRegressor
    root=root.resolve();preflight(root);cfg=protocol()['equity'];torch.set_num_threads(2)
    published=json.loads((root/'lake/published.json').read_text());manifest=checked_manifest(Path(published['manifest']),root)
    if sum(manifest['counts'].values())>50000:raise ValueError('This local comparison is bounded to 50,000 labeled decisions')
    folder,shards=freeze(root,manifest);output=root/'models'/('local-run-'+uuid.uuid4().hex[:12]);output.mkdir()
    train_mean,ridge=baselines(loader(manifest,root,'train'))
    # Classical estimator has a documented 50k-row bound in its own process, never a Spark driver collect.
    train=list(batches(folder,shards,'train'));x=torch.cat([r['x'] for r in train]).numpy();y=torch.cat([r['y'] for r in train]).numpy();del train
    boosted=HistGradientBoostingRegressor(max_iter=100,max_leaf_nodes=15,learning_rate=.1,l2_regularization=1.,early_stopping=False,random_state=11)
    boosted.fit(x,y);del x,y
    predictors={'constant_half':lambda x:np.full(len(x),.5),'train_mean':lambda x:np.full(len(x),train_mean),
                'ridge':lambda x:np.clip(np.c_[x,np.ones(len(x))]@ridge,0,1),'histogram_boosted':lambda x:np.clip(boosted.predict(x),0,1)}
    base={name:evaluate(None,batches(folder,shards,'test'),'mlp',fn) for name,fn in predictors.items()}
    report={'created_at':utcnow().isoformat(),'dataset_id':manifest['dataset_id'],'manifest_hash':digest(manifest),'protocol':protocol(),
            'domain':'Synthetic river states, two active players, exact uniform-opponent showdown equity; no action EV',
            'status':'research-only','counts':manifest['counts'],'baselines':base,'comparisons':[],'hardware':platform.platform(),'device':'cpu',
            'features_version':manifest['features_version'],'model':'MLP and canonical pooled card encoders','real_data_evaluation':'Unavailable: no verified real exports'}
    mlflow.set_tracking_uri('sqlite:///'+str(root/'mlflow.sqlite3'));mlflow.set_experiment('pokermind-equity-local-v2')
    for name,width in [('mlp',64),('card_set',64),('card_set_small',16)]:
        for fraction in cfg['data_fractions']:
            for seed in protocol()['seeds']:
                preflight(root);destination=output/f'{name}-{fraction}-{seed}';destination.mkdir();started=time.perf_counter()
                with mlflow.start_run(run_name=destination.name) as tracking:
                    mlflow.log_params({'model':name,'width':width,'fraction':fraction,'seed':seed,'dataset_id':manifest['dataset_id'],'device':'cpu','cost_usd':0})
                    model,curve,best=fit(folder,shards,name,width,fraction,seed,cfg,destination)
                    test=evaluate(model,batches(folder,shards,'test'),name)
                    weights=destination/'weights.pt';torch.save(model.state_dict(),weights);sha=hashlib.sha256(weights.read_bytes()).hexdigest();target=weights.with_name('weights-'+sha[:16]+'.pt');weights.rename(target)
                    again=EquityMLP() if name=='mlp' else CardSet(width);again.load_state_dict(torch.load(target,weights_only=True));again.eval()
                    if evaluate(again,batches(folder,shards,'test'),name)!=test:raise AssertionError('Equity reload mismatch')
                    sample=next(iter(batches(folder,shards,'test')));timings=[]
                    with torch.inference_mode():
                        for i in range(110):
                            t=time.perf_counter();predict(model,sample,name)
                            if i>=10:timings.append((time.perf_counter()-t)*1000)
                    result={'model':name,'width':width,'fraction':fraction,'seed':seed,'run_id':tracking.info.run_id,'test':test,'best_validation_mae':best,
                            'curve':curve,'seconds':time.perf_counter()-started,'model_path':str(target.relative_to(root)),'sha256':sha,'model_bytes':target.stat().st_size,
                            'batch_latency_ms':{'batch':len(sample['y']),'median':float(np.median(timings)),'p95':float(np.quantile(timings,.95))}}
                    report['comparisons'].append(result);mlflow.log_dict(manifest,'dataset.json');mlflow.log_dict(result,'evaluation.json');mlflow.log_artifact(str(target),'model');mlflow.log_artifact(str(destination/'checkpoint.pt'),'checkpoint')
                    mlflow.log_metrics({k:test[k] for k in ('mae','rmse','bias')});print(canonical({k:result[k] for k in ('model','fraction','seed','test','seconds')}),flush=True)
    report['runs']=[r for r in report['comparisons'] if r['model']=='mlp' and r['fraction']==1.]
    report['mean_test_mae']=float(np.mean([r['test']['mae'] for r in report['runs']]));report['std_test_mae']=float(np.std([r['test']['mae'] for r in report['runs']]))
    best_base=min(m['mae'] for m in base.values());gates={}
    for name in cfg['models']:
        runs=[r for r in report['comparisons'] if r['model']==name and r['fraction']==1.]
        gates[name]={'support':all(r['test']['independent_families']>=cfg['min_independent_test_deals'] for r in runs),
                     'quality':all(r['test']['mae']<=cfg['max_mae'] and abs(r['test']['bias'])<=cfg['max_absolute_bias'] for r in runs),
                     'baseline_gain':np.mean([r['test']['mae'] for r in runs])<=best_base*(1-cfg['relative_gain_vs_best_baseline'])}
    report['promotion_gates']={k:{n:bool(v) for n,v in g.items()} for k,g in gates.items()}
    report['promotion']='Research artifacts only; no automatic release. See per-model support, quality and baseline gates.'
    report['limitations']=['Synthetic river-only domain','Three CPU seeds; no GPU or multi-machine training','Card-order and suit invariance tested; no hidden/future features','Exact reference remains serving default']
    path,_=immutable_json(output/'report.json',report);temp=root/'research.tmp';temp.write_text(canonical({'report':str(path.relative_to(root))}));temp.replace(root/'research.json')
    print(canonical({'report':str(path),'promotion_gates':report['promotion_gates']}),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);a=p.parse_args();run(a.data)
