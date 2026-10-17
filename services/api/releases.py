"""Local serving-report snapshots, immutable validation and reversible activation.

These releases pin research reports and their baseline artifacts. Neural default
promotion remains fail-closed until all independent quality/domain gates pass.
"""
import hashlib
import json
import os
from pathlib import Path
from packages.pokermind_core.contracts import canonical,digest
from packages.pokermind_core.eligibility import utcnow

NAMES=('research','behavior','strategy-kuhn','strategy-leduc')


def safe(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or path.is_symlink():raise ValueError('Release path escapes local root')
    return path


def check_report(root,name,path,sha=None):
    data=safe(root,path).read_bytes()
    if sha and hashlib.sha256(data).hexdigest()!=sha:raise ValueError('Release report checksum mismatch')
    report=json.loads(data)
    if name=='research' and report.get('features_version')!='river-counts-v1':raise ValueError('Incompatible equity schema')
    if name=='behavior' and report.get('version')!='behavior-visible-v1':raise ValueError('Incompatible behavior schema')
    if name.startswith('strategy-') and (report.get('version')!='deep-cfr-small-v1' or report.get('game')!=name.split('-')[1]):raise ValueError('Incompatible strategy schema')
    # Validate every declared model/policy artifact, not just a mutable report alias.
    def visit(value):
        if isinstance(value,dict):
            if 'sha256' in value:
                artifact=next((value[k] for k in ('model_path','artifact','policy') if k in value),None)
                if artifact and hashlib.sha256(safe(root,artifact).read_bytes()).hexdigest()!=value['sha256']:raise ValueError('Release artifact checksum mismatch')
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(report)
    return hashlib.sha256(data).hexdigest(),report


def capture(root):
    root=Path(root).resolve();reports={}
    for name in NAMES:
        pointer=root/(name+'.json')
        if pointer.exists():
            path=json.loads(pointer.read_text())['report'];sha,_=check_report(root,name,path);reports[name]={'path':path,'sha256':sha}
    payload={'created_at':utcnow().isoformat(),'reports':reports,'policy':'Validated baseline defaults; neural artifacts remain explicit research opt-in'}
    release_id=digest(payload);payload['release_id']=release_id
    folder=root/'releases';folder.mkdir(exist_ok=True)
    file=folder/(release_id+'.json');file.write_text(canonical(payload))
    return payload


def load(root,release_id):
    if len(release_id)!=64 or any(c not in '0123456789abcdef' for c in release_id):raise ValueError('Invalid release ID')
    payload=json.loads((root/'releases'/(release_id+'.json')).read_text())
    if digest({k:v for k,v in payload.items() if k!='release_id'})!=release_id:raise ValueError('Release manifest checksum mismatch')
    for name,item in payload['reports'].items():
        if name not in NAMES:raise ValueError('Unknown report role')
        check_report(root,name,item['path'],item['sha256'])
    return payload


def activate(root,release_id):
    root=Path(root).resolve();payload=load(root,release_id)
    # One pointer switches all report roles coherently; existing games retain
    # their own immutable model/policy IDs and are unaffected.
    temp=root/'active-release.tmp';temp.write_text(canonical({'release_id':release_id}));os.replace(temp,root/'active-release.json')
    with (root/'release-audit.jsonl').open('a') as audit:audit.write(canonical({'at':utcnow().isoformat(),'activated':release_id})+'\n')
    return payload


def report_path(root,name):
    pointer=root/'active-release.json'
    if not pointer.exists():return json.loads((root/(name+'.json')).read_text())['report']
    release_id=json.loads(pointer.read_text())['release_id'];payload=load(root,release_id)
    if name not in payload['reports']:raise ValueError('This pinned release does not contain '+name)
    return payload['reports'][name]['path']


def inventory(root):
    root=Path(root).resolve();pointer=root/'active-release.json'
    active=json.loads(pointer.read_text())['release_id'] if pointer.exists() else None
    items=[]
    for path in sorted((root/'releases').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
        try:
            r=load(root,path.stem);items.append({'release_id':path.stem,'created_at':r['created_at'],'reports':list(r['reports']),'valid':True})
        except (ValueError,OSError,KeyError):items.append({'release_id':path.stem,'valid':False})
    return {'active':active,'items':items,'policy':'Snapshots pin validated baselines and research artifacts; failed neural gates cannot change defaults.'}


def promotion_check(root,name):
    if name not in NAMES:raise ValueError('Unknown model role')
    path=report_path(Path(root).resolve(),name);_,r=check_report(Path(root).resolve(),name,path)
    if name=='research':
        gates=r.get('promotion_gates',{}).get('mlp',{})
        passed=bool(gates) and all(gates.values())
    else:passed=r.get('promotion',{}).get('eligible') is True
    # This product has not validated cross-domain neural serving. A quality pass
    # can be recorded without enabling a broader/default inference pathway.
    return {'role':name,'quality_gates_passed':passed,'default_promotion_allowed':False,
            'reason':'Quality gates failed; baseline retained' if not passed else 'Quality passed; a separate domain/serving release review is still required'}
