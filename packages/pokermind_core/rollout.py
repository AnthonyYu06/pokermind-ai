"""Bounded historical action comparison under an explicit passive continuation.

EV is final stack minus stack immediately before this decision. Prior contributions
are sunk; folding normally has zero future EV. This is not an equilibrium solver.
"""
from copy import deepcopy
from math import sqrt
import random
from statistics import mean,variance
from .cards import DECK
from .contracts import Event,Summary
from .engine import Engine
from .ranges import legal_range,preset
from packages.features.behavior import candidates

VERSION='hu-passive-rollout-v1'


def compare(hand,index,obs,*,range_name='uniform',samples=300,seed=1):
    if type(samples) is not int or not 100<=samples<=1000:raise ValueError('Rollout budget must be 100–1,000')
    if len(hand.players)!=2 or hand.rake or hand.variant!='NLHE_CASH':
        return {'status':'unavailable','reason':'Rollout supports two-seat, zero-rake NLHE only; other rules abstain','model_version':VERSION}
    actor=obs['acting_player'];state=obs['state'];own=next(p['cards'] for p in state['seats'] if p['seat']==actor)
    if len(own)!=2:return {'status':'unavailable','reason':'Known acting-player cards required','model_version':VERSION}
    other=next(p.seat for p in hand.players if p.seat!=actor)
    combos,weights,support=legal_range(own+state['board'],preset(range_name));rng=random.Random(seed)
    actions=candidates(obs);returns={a:[] for a in actions}
    for _ in range(samples):
        opponent=list(rng.choices(combos,weights=weights)[0]);copy=deepcopy(hand)
        # Replace all retrospective cards and discard every future event/summary.
        for p in copy.players:p.cards=own[:] if p.seat==actor else opponent
        copy.events=deepcopy(hand.events[:index]);copy.rake=0;copy.terminal_summary=Summary(awards={},final_stacks={},rake=0)
        engine=Engine(copy)
        for event in copy.events:engine.apply(event)
        if engine.actor!=actor or engine.snapshot(actor)!=state:raise ValueError('Stored observation disagrees with reconstructed prefix')
        before=engine.stacks[actor]
        deck=[c for c in DECK if c not in own+opponent+state['board']];rng.shuffle(deck)
        for label,action in actions.items():
            sim=deepcopy(engine);chance=deck[:];sim.apply(Event(**action))
            for _step in range(200):
                if len(sim.active)==1 or (sim.street==3 and not sim.pending):break
                if not sim.pending:event=Event(kind='deal',cards=[chance.pop() for _ in range(3 if sim.street==0 else 1)])
                else:event=Event(kind='check' if 'check' in sim.legal() else 'call',seat=sim.actor)
                sim.apply(event)
            else:raise RuntimeError('Rollout exceeded action bound')
            final=sim.finish();returns[label].append(final.final_stacks[actor]-before)
    reference='check' if 'check' in actions else 'call' if 'call' in actions else 'fold'
    def estimate(values):
        m=mean(values);se=sqrt(variance(values)/len(values));return {'mean':m,'standard_error':se,'ci95':[m-1.96*se,m+1.96*se]}
    return {'status':'available','model_version':VERSION,'samples_per_action':samples,'seed':seed,'range_preset':range_name,'range_support':support,
            'reference_action':reference,'unit':'integer currency units (USD cents)','big_blind':hand.big_blind,'rake':0,
            'continuation':'Both players check when possible and call otherwise; no later bets or raises.',
            'ev_convention':'Expected final stack minus remaining stack immediately before this decision; previously committed chips are sunk.',
            'uncertainty':'Paired seeded rollouts use common cards. Intervals measure sampling error only, not error in the range or continuation model.',
            'actions':[{'label':a,'action':actions[a],'ev':estimate(values),'difference_from_reference':estimate([v-r for v,r in zip(values,returns[reference])])} for a,values in returns.items()],
            'warning':'Estimated EV differences under this model; not proven mistakes or optimal-action labels.'}
