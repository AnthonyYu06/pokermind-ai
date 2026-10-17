"""Explicit weighted heads-up ranges and card-removal-aware showdown pot share."""
import math
import random
from itertools import combinations
from statistics import mean,variance
from .cards import DECK,rank

VERSION='weighted-hu-equity-v1'
PRESETS=('uniform','pair_heavy','broadway_heavy')


def preset(name):
    if name not in PRESETS:raise ValueError('Unknown range assumption')
    result=[]
    for a,b in combinations(DECK,2):
        weight=1.
        if name=='pair_heavy':weight=4. if a[0]==b[0] else .5
        if name=='broadway_heavy':weight=4. if a[0] in 'TJQKA' and b[0] in 'TJQKA' else .5
        result.append(((a,b),weight))
    return result


def legal_range(known,weighted):
    if len(set(known))!=len(known) or any(c not in DECK for c in known):raise ValueError('Impossible known cards')
    seen=set();legal=[];weights=[];prior=blocked=0.
    for cards,weight in weighted:
        if len(cards)!=2 or cards[0]==cards[1] or any(c not in DECK for c in cards):raise ValueError('Invalid range combination')
        key=tuple(sorted(cards))
        if key in seen:raise ValueError('Duplicate range combination')
        seen.add(key)
        if isinstance(weight,bool) or not isinstance(weight,(int,float)) or not math.isfinite(weight) or weight<0:raise ValueError('Range weights must be finite and nonnegative')
        prior+=weight
        if set(cards)&set(known):blocked+=weight
        elif weight:legal.append(key);weights.append(weight)
    total=sum(weights)
    if not total or not math.isfinite(total) or not math.isfinite(prior):raise ValueError('Range has no finite positive legal mass')
    probabilities=[w/total for w in weights]
    return legal,probabilities,{'legal_combinations':len(legal),'blocked_weight_fraction':blocked/prior,'effective_combinations':1/sum(p*p for p in probabilities)}


def weighted_equity(hero,board,weighted,*,samples=2000,seed=1):
    if len(hero)!=2 or len(board) not in (0,3,4,5):raise ValueError('Unsupported card shape')
    if type(samples) is not int or not 100<=samples<=20000:raise ValueError('Samples must be 100–20,000')
    combos,probabilities,support=legal_range(hero+board,weighted);rng=random.Random(seed)
    exact=len(board)==5;shares=[]
    fixed=rank(hero+board) if exact else None
    selected=combos if exact else rng.choices(combos,weights=probabilities,k=samples)
    for opponent in selected:
        runout=board if exact else board+rng.sample([c for c in DECK if c not in hero+board+list(opponent)],5-len(board))
        a=fixed if exact else rank(hero+runout);b=rank(list(opponent)+runout)
        shares.append(1. if a<b else .5 if a==b else 0.)
    value=sum(p*s for p,s in zip(probabilities,shares)) if exact else mean(shares)
    se=0. if exact else math.sqrt(variance(shares)/samples)
    return {'status':'available','equity':value,'standard_error':se,'ci95':[max(0.,value-1.96*se),min(1.,value+1.96*se)],
            'samples':len(shares),'seed':seed,'method':'exact weighted enumeration' if exact else 'seeded weighted Monte Carlo',
            'model_version':VERSION,'range_support':support,'assumptions':'Heads-up showdown pot share; specified range renormalized after card removal. Sampling interval excludes uncertainty about the range. No future betting, folding or rake.'}
