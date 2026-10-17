"""Decision-only features and deterministic legal action-bucket mapping."""
import math

VERSION='behavior-visible-v1'
ACTIONS=('fold','check','call','half_pot','pot','double_pot','all_in')
KINDS=('fold','check','call','bet','raise','deal')
RANKS='23456789TJQKA'
MAX_PREFIX=64


def candidates(obs):
    state=obs['state'];legal=state['legal_actions'];result={}
    actor=next(s for s in state['seats'] if s['seat']==obs['acting_player'])
    for name in ('fold','check','call'):
        if name in legal:result[name]={'kind':name,'seat':obs['acting_player']}
    kind='raise' if 'raise' in legal else 'bet' if 'bet' in legal else None
    if kind:
        bounds=legal[kind];call=legal.get('call',{}).get('amount',0)
        base=actor['bet']+call;pot=state['pot']+call
        seen=set()
        for label,fraction in [('half_pot',.5),('pot',1),('double_pot',2),('all_in',None)]:
            to=bounds['max_to'] if fraction is None else min(bounds['max_to'],max(bounds['min_to'],base+round(pot*fraction)))
            if to not in seen:
                result[label]={'kind':kind,'seat':obs['acting_player'],'to':to};seen.add(to)
    return result


def target(obs,event):
    choices=candidates(obs)
    if event['kind'] in ('fold','check','call'):return ACTIONS.index(event['kind'])
    if event['kind'] not in ('bet','raise'):raise ValueError('Not an action target')
    return ACTIONS.index(min((k for k in choices if 'to' in choices[k]),key=lambda k:abs(choices[k]['to']-event['to'])))


def encode(obs):
    import numpy as np
    state=obs['state'];actor=obs['acting_player'];seats=state['seats']
    hero=next(s for s in seats if s['seat']==actor)
    if len(hero['cards'])!=2:raise ValueError('Acting-player cards unknown')
    if len(seats)!=2:raise ValueError('Behavior model supports heads-up simulation only')
    def counts(cards):return [sum(c[0]==r for c in cards)/4 for r in RANKS]
    suits=sorted([(sum(c[1]==s for c in hero['cards']),sum(c[1]==s for c in state['board'])) for s in 'cdhs'],reverse=True)
    features=counts(hero['cards'])+counts(state['board'])+[v/5 for pair in suits for v in pair]
    features += [float(state['street']==s) for s in ('preflop','flop','turn','river')]
    scale=max(20,state['pot']);other=next(s for s in seats if s['seat']!=actor)
    features += [math.log1p(state['pot']/20)/8, min(hero['stack']/scale,10)/10,min(other['stack']/scale,10)/10,
                 hero['bet']/scale,other['bet']/scale,state['legal_actions'].get('call',{}).get('amount',0)/scale,float(hero['button'])]
    prefix=obs['public_action_prefix']
    if len(prefix)>MAX_PREFIX:raise ValueError('Sequence exceeds model coverage')
    tokens=[]
    for e in prefix:
        tokens.append([float(e['kind']==k) for k in KINDS]+[float(e.get('seat')==actor),math.log1p((e.get('to') or 0)/20)/8,len(e.get('cards',[]))/3])
    if not tokens:tokens=[[0.]*9]
    mask=[a in candidates(obs) for a in ACTIONS]
    return np.array(features,dtype=np.float32),np.array(tokens,dtype=np.float32),np.array(mask,dtype=bool)
