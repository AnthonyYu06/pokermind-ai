"""Varied synthetic data policies using visible state only; no optimal labels."""
import random
from packages.pokermind_core.engine import Engine
from packages.pokermind_core.contracts import Event,Hand
from packages.pokermind_core.cards import DECK
from packages.simulation.generate import generate_hand
from packages.features.behavior import ACTIONS,candidates

POLICIES=('cautious','pressure','responsive','unseen_mixed')
VERSION='visible-policy-families-v1'


def probabilities(obs,policy):
    if policy not in POLICIES:raise ValueError('Unknown synthetic policy')
    legal=candidates(obs)
    weights={'fold':.18,'check':1.,'call':.5,'half_pot':.18,'pot':.08,'double_pot':.04,'all_in':.01}
    if policy=='cautious':weights.update(fold=.32,call=.7,half_pot=.08,pot=.03,double_pot=.01)
    if policy=='pressure':weights.update(fold=.07,call=.25,check=.4,half_pot=.4,pot=.3,double_pot=.15)
    if policy=='responsive':
        recent=obs['public_action_prefix'][-3:]
        pressured=any(e['kind'] in ('bet','raise') for e in recent)
        weights.update(fold=.35 if pressured else .08,call=.25 if pressured else .7,half_pot=.1 if pressured else .5,check=.4)
    if policy=='unseen_mixed':weights.update(fold=.03,call=.4,check=.5,all_in=.18,pot=.3)
    total=sum(weights[a] for a in legal)
    return [weights[a]/total if a in legal else 0. for a in ACTIONS]


def generate_behavior(seed,policy):
    hand=generate_hand(seed,table_size=2,session=f'behavior-{seed//24}')
    deck=[c for c in DECK if c not in [c for p in hand.players for c in p.cards]]
    rng=random.Random(seed+700001);rng.shuffle(deck)
    hand.events=[];engine=Engine(hand);prefix=[]
    for _ in range(200):
        if len(engine.active)==1 or (engine.street==3 and not engine.pending):break
        if not engine.pending:event=Event(kind='deal',cards=[deck.pop() for _ in range(3 if engine.street==0 else 1)])
        else:
            obs={'acting_player':engine.actor,'state':engine.snapshot(engine.actor),'public_action_prefix':prefix}
            label=rng.choices(ACTIONS,weights=probabilities(obs,policy))[0]
            event=Event(**candidates(obs)[label])
        engine.apply(event);hand.events.append(event);prefix.append(event.model_dump())
    else:raise RuntimeError('Behavior trajectory exceeded budget')
    hand.terminal_summary=engine.finish()
    return Hand.model_validate(hand.model_dump())
