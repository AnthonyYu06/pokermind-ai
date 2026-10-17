"""Bounded small-game research with independent tabular/evaluation references."""
import argparse
import hashlib
import json
import platform
import time
import uuid
from importlib.metadata import version
from pathlib import Path
import numpy as np
import pyspiel
import torch
from packages.pokermind_core.contracts import canonical
from packages.strategy.games import game_tree
from packages.strategy.tabular import TabularCFR
from packages.strategy.deep_cfr import DeepCFR, VERSION
from training.artifacts import immutable_json

ROOT=Path(__file__).resolve().parents[1]


def run(root,game='kuhn',ablations=False,resume=None,profile='tiny'):
    import mlflow
    root=root.resolve();root.mkdir(exist_ok=True,parents=True)
    protocol=json.loads((ROOT/'configs/strategy-evaluation.json').read_text())
    cfg=protocol['tiny']
    if profile=='local':
        from training.limits import protocol as local_protocol,preflight
        preflight(root);cfg=local_protocol()['strategy'];protocol={**protocol,'local':cfg,'active_profile':'local-research-v2'}
    tree=game_tree(game)
    output=root/'strategy'/game/('run-'+uuid.uuid4().hex[:12]);output.mkdir(parents=True)
    started=time.perf_counter()
    baseline=TabularCFR(tree);baseline.train(protocol['tabular_iterations'][game])
    reference=pyspiel.CFRSolver(tree.game)
    for _ in range(baseline.iteration):reference.evaluate_and_update_policy()
    ref_policy=reference.average_policy();ref_array=np.zeros(tree.mask.shape)
    for i,state in enumerate(tree.states):
        for a,p in ref_policy.action_probabilities(state).items():ref_array[i,a]=p
    parity=float(abs(baseline.average()-ref_array).max())
    if parity>1e-9:raise AssertionError(f'Independent tabular CFR mismatch: {parity}')
    baseline_metrics=tree.evaluate(baseline.average())
    ref_metrics=tree.reference_metrics(baseline.average())
    if abs(baseline_metrics['exploitability']-ref_metrics['exploitability'])>1e-9:raise AssertionError('Best response mismatch')
    baseline_file,baseline_sha=immutable_json(output/'tabular.json',{'game':game,'version':'tabular-cfr-v1','iterations':baseline.iteration,'keys':tree.keys,'probabilities':baseline.average().tolist(),'metrics':baseline_metrics})
    report={'version':VERSION,'game':game,'status':'research-only','protocol':protocol,'nodes':tree.node_count,'information_sets':len(tree.keys),
            'runtime':{'python':platform.python_version(),'torch':torch.__version__,'open_spiel':version('open-spiel'),'hardware':platform.platform()},
            'baselines':{'uniform':tree.evaluate(tree.uniform()),'check_call':tree.evaluate(tree.passive()),'tabular':baseline_metrics},
            'independent_reference':{'name':'OpenSpiel C++ CFR and Python best response','max_policy_difference':parity,'metrics':ref_metrics},'runs':[]}
    mlflow.set_tracking_uri('sqlite:///'+str(root/'mlflow.sqlite3'));mlflow.set_experiment('pokermind-small-game-cfr')
    variants=[('main',True,True)] + ([('no_iteration_weighting',False,True),('no_network_reset',True,False)] if ablations else [])
    for variant,linear,reset in variants:
        for seed in protocol['seeds']:
            timer=time.perf_counter();solver=DeepCFR(tree,seed,cfg['capacity'],cfg['width'],linear,reset)
            checkpoint=output/f'{variant}-{seed}-checkpoint.pt'
            if resume:
                if variant!='main' or seed!=protocol['seeds'][0]:continue
                solver.restore(resume)
            with mlflow.start_run(run_name=f'{game}-{variant}-{seed}') as tracking:
                mlflow.log_params({'game':game,'seed':seed,'variant':variant,**cfg,'algorithm':VERSION,'source':'synthetic-small-game','cost_usd':0})
                mlflow.log_dict(protocol,'evaluation-protocol.json')
                for _ in range(solver.iteration,cfg['iterations']):
                    solver.step(cfg['traversals_per_player'],cfg['advantage_steps'])
                    if solver.iteration%5==0 or solver.iteration==1:
                        p=solver.average(cfg['strategy_steps']);metrics=tree.evaluate(p)
                        metrics.update(iteration=solver.iteration,traversals=solver.traversals,seconds=time.perf_counter()-timer,
                                       samples=sum(m.seen for m in solver.memories),stored_samples=sum(len(m.items) for m in solver.memories))
                        solver.curve.append(metrics);mlflow.log_metric('exploitability',metrics['exploitability'],step=solver.iteration)
                        print(canonical({'game':game,'variant':variant,'seed':seed,**metrics}),flush=True)
                    solver.save(checkpoint)
                p=solver.average(cfg['strategy_steps']);metrics=tree.evaluate(p)
                independent=tree.reference_metrics(p)
                if abs(metrics['exploitability']-independent['exploitability'])>1e-8:raise AssertionError('Neural best-response reference mismatch')
                policy_file,sha=immutable_json(output/f'{variant}-{seed}-policy.json',{'game':game,'version':VERSION,'keys':tree.keys,'probabilities':p.tolist(),'metrics':metrics,'seed':seed})
                solver.save(checkpoint)
                mlflow.log_artifact(str(checkpoint),'checkpoint');mlflow.log_artifact(str(policy_file),'policy');mlflow.log_dict({'curve':solver.curve},'learning-curve.json')
                report['runs'].append({'variant':variant,'seed':seed,'metrics':metrics,'curve':solver.curve,'run_id':tracking.info.run_id,
                                       'policy':str(policy_file.relative_to(root)),'sha256':sha,'checkpoint':str(checkpoint.relative_to(root)),
                                       'seconds':time.perf_counter()-timer,'model_bytes':sum(x.numel()*x.element_size() for x in solver.strategy.parameters())})
    main=[r for r in report['runs'] if r['variant']=='main'];values=[r['metrics']['exploitability'] for r in main]
    report.update(mean_exploitability=float(np.mean(values)),std_exploitability=float(np.std(values)),elapsed_seconds=time.perf_counter()-started,cost_usd=0)
    passed=len(main)==3 and all(v<=protocol['promotion']['max_exploitability'][game] and v<=baseline_metrics['exploitability'] for v in values)
    report['promotion']={'eligible':passed,'reason':'All predeclared gates passed; manual release required' if passed else 'Neural quality gate failed; validated tabular policy retained'}
    report['serving']={'kind':'tabular-cfr','policy':str(baseline_file.relative_to(root)),'sha256':baseline_sha,'metrics':baseline_metrics}
    report_file,_=immutable_json(output/'report.json',report)
    pointer=root/f'strategy-{game}.json';temp=pointer.with_suffix('.tmp');temp.write_text(canonical({'report':str(report_file.relative_to(root))}));temp.replace(pointer)
    print(canonical({'report':str(report_file),'mean_exploitability':report['mean_exploitability'],'promotion':report['promotion']}),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=ROOT/'.data');p.add_argument('--game',choices=['kuhn','leduc'],default='kuhn');p.add_argument('--ablations',action='store_true');p.add_argument('--resume',type=Path);p.add_argument('--profile',choices=['tiny','local'],default='tiny')
    a=p.parse_args();run(a.data,a.game,a.ablations,a.resume,a.profile)
