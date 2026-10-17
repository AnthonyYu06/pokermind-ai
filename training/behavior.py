"""Bounded shard training for frequency/logistic/GRU next-action predictions."""
import argparse
import copy
import hashlib
import json
import math
import random
import time
import uuid
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
import torch
from torch.utils.data import IterableDataset,DataLoader
from torch.nn.utils.rnn import pad_sequence
from packages.features.behavior import encode,target,ACTIONS,VERSION
from packages.models.behavior import BehaviorModel
from packages.simulation.behavior_data import generate_behavior,POLICIES
from packages.pokermind_core.engine import reconstruct
from packages.pokermind_core.contracts import canonical,digest
from training.artifacts import immutable_json

ROOT=Path(__file__).resolve().parents[1]


def freeze(root,config):
    dataset_id=digest({'config':config,'features':VERSION,'generator':'visible-policy-families-v1'})
    folder=root/'behavior'/'datasets'/dataset_id;manifest_path=folder/'manifest.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text())
        for rel,sha in manifest['files'].items():
            if hashlib.sha256((folder/rel).read_bytes()).hexdigest()!=sha:raise ValueError('Behavior shard checksum mismatch')
        return folder,manifest
    folder.mkdir(parents=True,exist_ok=True)
    buffers=defaultdict(list);counts=Counter();hands=Counter();files={};families={};seen_observations={}
    def flush(split):
        if not buffers[split]:return
        path=folder/f'{split}-{counts[split]:07d}.pt';torch.save(buffers[split],path)
        files[path.name]=hashlib.sha256(path.read_bytes()).hexdigest();buffers[split]=[]
    for family,policy in enumerate(POLICIES):
        for n in range(config['hands_per_policy']):
            seed=config['generation_seed']+family*100000+n
            hand=generate_behavior(seed,policy)
            # Entire initial private deal family (all suit permutations, ignoring
            # player order) is assigned together, independent of generated future.
            from itertools import permutations
            cards=[p.cards for p in hand.players]
            deal=min(tuple(sorted(tuple(sorted(c[0]+dict(zip('cdhs',perm))[c[1]] for c in pair)) for pair in cards)) for perm in permutations('cdhs'))
            key=digest(deal);bucket=int(key[:8],16)%10
            split='ood' if policy==config['unseen_policy'] else 'train' if bucket<7 else 'validation' if bucket<9 else 'test'
            # Cross-domain duplicates are excluded so OOD remains independent.
            if key in families and families[key]!=split:continue
            families[key]=split;hands[split]+=1
            _,observations=reconstruct(hand)
            for obs in observations:
                x,seq,mask=encode(obs);y=target(obs,hand.events[obs['action_index']].model_dump())
                # Identical observable states can recur in poker; only exact full
                # feature+prefix duplicates are removed across split boundaries.
                fingerprint=hashlib.sha256(x.tobytes()+seq.tobytes()+mask.tobytes()).hexdigest()
                if fingerprint in seen_observations and seen_observations[fingerprint]!=split:continue
                seen_observations[fingerprint]=split
                buffers[split].append({'x':torch.tensor(x),'seq':torch.tensor(seq),'mask':torch.tensor(mask),'y':y,'policy':policy,
                                       'hand':seed,'street':obs['state']['street'],'family':key})
                counts[split]+=1
                if len(buffers[split])>=128:flush(split)
    for split in buffers:flush(split)
    manifest={'dataset_id':dataset_id,'features':VERSION,'source':'synthetic','files':files,'decisions':dict(counts),'hands':dict(hands),
              'split':'canonical initial two-player deal family hash 70/20/10; unseen generator held out; cross-split exact observation duplicates dropped',
              'config':config,'distinct_deal_families':len(families),'independent_families':dict(Counter(families.values()))}
    manifest_path.write_text(canonical(manifest));return folder,manifest


class Shards(IterableDataset):
    def __init__(self,folder,manifest,split,seed=0):self.folder,self.manifest,self.split,self.seed=folder,manifest,split,seed
    def __iter__(self):
        files=sorted(f for f in self.manifest['files'] if f.startswith(self.split+'-'))
        rng=random.Random(self.seed)
        if self.split=='train':rng.shuffle(files)
        for name in files:
            rows=torch.load(self.folder/name,weights_only=True)
            if self.split=='train':rng.shuffle(rows)
            yield from rows


def retained_test_families(folder,manifest):
    return len({row['family'] for row in Shards(folder,manifest,'test')})


def collate(rows):
    return (torch.stack([r['x'] for r in rows]),pad_sequence([r['seq'] for r in rows],batch_first=True),
            torch.tensor([len(r['seq']) for r in rows]),torch.stack([r['mask'] for r in rows]),torch.tensor([r['y'] for r in rows]),rows)


def frequencies(folder,manifest):
    counts=defaultdict(lambda:np.ones(7));global_count=np.ones(7)
    for row in Shards(folder,manifest,'train'):
        key=row['street']+'|'+''.join('1' if v else '0' for v in row['mask'])
        counts[key][row['y']]+=1;global_count[row['y']]+=1
    return {k:v.tolist() for k,v in counts.items()},global_count.tolist()


def frequency_predict(rows,table,global_count):
    result=[]
    for row in rows:
        key=row['street']+'|'+''.join('1' if v else '0' for v in row['mask'])
        p=np.array(table.get(key,global_count))*row['mask'].numpy();result.append(p/p.sum())
    return np.array(result)


def metrics(probabilities,labels,lengths,policies):
    p=np.asarray(probabilities);y=np.asarray(labels);n=len(y)
    nll=-np.log(np.clip(p[np.arange(n),y],1e-12,1)).mean()
    brier=((p-np.eye(7)[y])**2).sum(1).mean()
    confidence=p.max(1);correct=p.argmax(1)==y;bins=[];ece=0.
    for lo in np.arange(0,1,.1):
        mask=(confidence>=lo)&(confidence<(lo+.1) if lo<.9 else confidence<=1)
        if mask.any():
            accuracy=float(correct[mask].mean());conf=float(confidence[mask].mean());ece+=abs(accuracy-conf)*mask.mean()
            bins.append({'lower':float(lo),'count':int(mask.sum()),'confidence':conf,'accuracy':accuracy})
    strata={}
    for name,mask in [('short_0_4',np.array(lengths)<=4),('medium_5_12',(np.array(lengths)>4)&(np.array(lengths)<=12)),('long_13_plus',np.array(lengths)>12)]+[(policy,np.array(policies)==policy) for policy in sorted(set(policies))]:
        if mask.any():strata[name]={'count':int(mask.sum()),'nll':float(-np.log(np.clip(p[mask,y[mask]],1e-12,1)).mean())}
    return {'count':n,'nll':float(nll),'brier':float(brier),'ece':float(ece),'reliability':bins,'support':dict(Counter(ACTIONS[i] for i in y)),'strata':strata}


def evaluate(model,loader,temperature=1.,frequency=None):
    ps=[];ys=[];lengths=[];policies=[]
    with torch.inference_mode():
        for x,seq,lens,mask,y,rows in loader:
            p=frequency_predict(rows,*frequency) if frequency else (model(x,seq,lens,mask)/temperature).softmax(-1).numpy()
            ps.extend(p.tolist());ys.extend(y.tolist());lengths.extend(lens.tolist());policies.extend(r['policy'] for r in rows)
    return metrics(ps,ys,lengths,policies)


def fit(folder,manifest,seed,context,width,config,output,resume=None):
    torch.manual_seed(seed);torch.set_num_threads(2);torch.use_deterministic_algorithms(True)
    model=BehaviorModel(width=width,context=context);optimizer=torch.optim.Adam(model.parameters(),lr=.003)
    validation=DataLoader(Shards(folder,manifest,'validation'),batch_size=128,collate_fn=collate)
    best=float('inf');best_weights=None;stale=0;curve=[]
    start=0
    if resume is not None:
        saved=torch.load(resume,map_location='cpu',weights_only=True)
        for key,wanted in [('features',VERSION),('dataset_id',manifest['dataset_id']),('seed',seed),('context',context),('width',width)]:
            if saved[key]!=wanted:raise ValueError('Behavior checkpoint mismatch: '+key)
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer']);torch.set_rng_state(saved['torch_rng'])
        best,best_weights,stale,curve,start=saved['best'],saved['best_model'],saved['stale'],saved['curve'],saved['epoch']
    for epoch in range(start,config['epochs']):
        if stale>=config['early_stopping_patience']:break
        loader=DataLoader(Shards(folder,manifest,'train',seed+epoch),batch_size=128,collate_fn=collate)
        total=count=0;model.train()
        for x,seq,lens,mask,y,_ in loader:
            loss=torch.nn.functional.cross_entropy(model(x,seq,lens,mask),y)
            if not torch.isfinite(loss):raise FloatingPointError('Non-finite behavior loss')
            optimizer.zero_grad();loss.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5,error_if_nonfinite=True);optimizer.step()
            total+=float(loss.detach())*len(y);count+=len(y)
        model.eval();val=evaluate(model,validation)['nll'];curve.append({'epoch':epoch+1,'train_nll':total/count,'validation_nll':val,'gradient_norm':float(norm)})
        if val<best-1e-5:best=val;best_weights=copy.deepcopy(model.state_dict());stale=0
        else:stale+=1
        torch.save({'features':VERSION,'dataset_id':manifest['dataset_id'],'seed':seed,'context':context,'width':width,'epoch':epoch+1,
                    'model':model.state_dict(),'best_model':best_weights,'optimizer':optimizer.state_dict(),'torch_rng':torch.get_rng_state(),'best':best,'stale':stale,'curve':curve},output/'checkpoint.pt')
        if stale>=config['early_stopping_patience']:break
    model.load_state_dict(best_weights);model.eval()
    # Temperature is selected on validation only; the final test is never used.
    options=[.75,1.,1.25,1.5,2.]
    calibration={t:evaluate(model,validation,t)['nll'] for t in options}
    temperature=min(calibration,key=calibration.get)
    return model,temperature,curve


def run(root,profile='tiny'):
    import mlflow
    root=root.resolve();config=json.loads((ROOT/'configs/behavior-evaluation.json').read_text())
    if profile=='local':
        from training.limits import protocol,preflight
        preflight(root);config={**config,**protocol()['behavior'],'profile':'local-research-v2'}
    folder,manifest=freeze(root,config)
    output=root/'behavior'/('run-'+uuid.uuid4().hex[:12]);output.mkdir(parents=True)
    frequency=frequencies(folder,manifest)
    loaders={s:DataLoader(Shards(folder,manifest,s),batch_size=128,collate_fn=collate) for s in ['validation','test','ood']}
    report={'version':VERSION,'dataset':manifest,'status':'research-only','domain':config['scope'],'real_data_evaluation':'Unavailable; no verified real exports',
            'baselines':{'frequency':{s:evaluate(None,loader,frequency=frequency) for s,loader in loaders.items()}},'runs':[]}
    mlflow.set_tracking_uri('sqlite:///'+str(root/'mlflow.sqlite3'));mlflow.set_experiment('pokermind-behavior-'+profile)
    for name,context,width in [('logistic',False,32),('gru',True,32),('gru_small',True,8)]:
        for seed in config['seeds']:
            destination=output/f'{name}-{seed}';destination.mkdir();started=time.perf_counter()
            with mlflow.start_run(run_name=f'{name}-{seed}') as tracking:
                mlflow.log_params({'model':name,'seed':seed,'dataset_id':manifest['dataset_id'],'width':width,'context':context,'device':'cpu','cost_usd':0})
                model,temperature,curve=fit(folder,manifest,seed,context,width,config,destination)
                scores={s:evaluate(model,loader,temperature) for s,loader in loaders.items()}
                weights=destination/'weights.pt';torch.save({'features':VERSION,'state_size':45,'context':context,'width':width,'temperature':temperature,'model':model.state_dict()},weights)
                sha=hashlib.sha256(weights.read_bytes()).hexdigest();new=weights.with_name(f'weights-{sha[:16]}.pt');weights.rename(new)
                loaded=torch.load(new,weights_only=True);again=BehaviorModel(width=width,context=context);again.load_state_dict(loaded['model']);again.eval()
                if evaluate(again,loaders['test'],temperature)!=scores['test']:raise AssertionError('Behavior checkpoint parity failed')
                result={'model':name,'seed':seed,'temperature':temperature,'metrics':scores,'curve':curve,'artifact':str(new.relative_to(root)),'sha256':sha,
                        'run_id':tracking.info.run_id,'seconds':time.perf_counter()-started,'model_bytes':new.stat().st_size}
                report['runs'].append(result)
                mlflow.log_dict(manifest,'dataset-manifest.json');mlflow.log_dict(result,'evaluation.json');mlflow.log_artifact(str(new),'model');mlflow.log_artifact(str(destination/'checkpoint.pt'),'checkpoint')
                for split,value in scores.items():mlflow.log_metrics({split+'_nll':value['nll'],split+'_brier':value['brier'],split+'_ece':value['ece']})
                print(canonical({k:result[k] for k in ['model','seed','metrics','seconds']}),flush=True)
    gru=[r for r in report['runs'] if r['model']=='gru'];logistic=[r for r in report['runs'] if r['model']=='logistic']
    best_base=min(report['baselines']['frequency']['test']['nll'],np.mean([r['metrics']['test']['nll'] for r in logistic]))
    # Count families actually represented in retained examples. Generated hands
    # can contribute zero examples after action/visibility/deduplication filters.
    independent=retained_test_families(folder,manifest)
    best_ood=min(report['baselines']['frequency']['ood']['nll'],np.mean([r['metrics']['ood']['nll'] for r in logistic]))
    gates={'independent_support':independent>=config.get('minimum_independent_test_families',500),
           'class_support':all(report['baselines']['frequency']['test']['support'].get(a,0)>=config.get('min_test_class_support',10) for a in ACTIONS),
           'quality_and_calibration':all(r['metrics']['test']['nll']<=best_base*(1-config['promotion']['relative_nll_improvement']) and r['metrics']['test']['ece']<=config['promotion']['maximum_ece'] for r in gru),
           'unseen_policy':all(r['metrics']['ood']['nll']<=best_ood*(1+config.get('max_ood_relative_regression',.02)) for r in gru)}
    passed=all(gates.values())
    report['promotion_gates']=gates;report['independent_test_families']=independent
    report['promotion']={'eligible':bool(passed),'reason':'Research gate passed; manual release required' if passed else 'Quality/support gate not met; frequency baseline retained'}
    # Choose by validation exclusively; available as explicitly labeled research opponent.
    selected=min(gru,key=lambda r:r['metrics']['validation']['nll']);report['selected']=selected
    report['frequency']={'table':frequency[0],'global':frequency[1]}
    path,_=immutable_json(output/'report.json',report);pointer=root/'behavior.json';temp=pointer.with_suffix('.tmp');temp.write_text(canonical({'report':str(path.relative_to(root))}));temp.replace(pointer)
    print(canonical({'report':str(path),'promotion':report['promotion']}),flush=True);return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=ROOT/'.data');p.add_argument('--profile',choices=['tiny','local'],default='tiny');p.add_argument('--resume',type=Path);p.add_argument('--epochs',type=int,default=35);a=p.parse_args()
    if a.resume:
        if not 1<=a.epochs<=100:raise SystemExit('Resume epochs must be between 1 and 100')
        saved=torch.load(a.resume,map_location='cpu',weights_only=True)
        folder=a.data/'behavior'/'datasets'/saved['dataset_id'];manifest=json.loads((folder/'manifest.json').read_text())
        for name,sha in manifest['files'].items():
            if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=sha:raise SystemExit('Dataset checksum mismatch')
        config={**manifest['config'],'epochs':a.epochs};destination=a.data/'behavior'/('resume-'+uuid.uuid4().hex[:12]);destination.mkdir()
        model,temp,curve=fit(folder,manifest,saved['seed'],saved['context'],saved['width'],config,destination,resume=a.resume)
        print(canonical({'checkpoint':str(destination/'checkpoint.pt'),'epochs':len(curve),'temperature':temp,'status':'resumed-research; no promotion'}))
    else:run(a.data,a.profile)
