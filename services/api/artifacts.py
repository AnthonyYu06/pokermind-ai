"""Read only pinned, checksummed local artifacts beneath the app data root."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path


def safe_path(root,relative):
    root=Path(root).resolve();path=(root/relative).resolve()
    if not path.is_relative_to(root):raise ValueError('Artifact path escapes local data root')
    return path


def report(root,name):
    pointer=Path(root)/(name+'.json')
    if not pointer.exists():raise ValueError(f'{name} research has not run; use the documented training command')
    from .releases import report_path
    return json.loads(safe_path(root,report_path(Path(root),name)).read_text())


def verified_bytes(root,path,sha):
    data=safe_path(root,path).read_bytes()
    if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('Artifact checksum verification failed')
    return data


def strategy_policy(root,game,opponent):
    research=report(root,'strategy-'+game)
    if opponent=='baseline':selection=research['serving']
    elif opponent=='research':
        # Fixed seed is predeclared; do not cherry-pick the best test exploitability.
        selection=next(r for r in research['runs'] if r['variant']=='main' and r['seed']==11)
    else:raise ValueError('Unknown opponent')
    data=json.loads(verified_bytes(root,selection['policy'],selection['sha256']))
    if data['game']!=game:raise ValueError('Policy game mismatch')
    from packages.strategy.games import game_tree
    tree=game_tree(game)
    if data['keys']!=tree.keys:raise ValueError('Policy information-state schema mismatch')
    tree.validate_policy(data['probabilities'])
    return selection,data
