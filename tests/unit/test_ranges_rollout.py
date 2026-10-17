from copy import deepcopy
import math
import pytest
from packages.pokermind_core.ranges import legal_range,weighted_equity,preset
from packages.pokermind_core.equity import equity
from packages.pokermind_core.rollout import compare
from packages.pokermind_core.contracts import Event,Player
from packages.pokermind_core.engine import Engine,reconstruct
from packages.simulation.generate import generate_hand


def test_weighted_exact_reference_and_blocker_normalization():
    hero=['As','Ah'];board=['2c','3d','7h','9s','Tc']
    r=weighted_equity(hero,board,[(('Ks','Kh'),1.),(('Jd','8h'),3.),(('As','Kd'),2.)])
    assert r['equity']==pytest.approx(.25) and r['standard_error']==0
    assert r['range_support']['legal_combinations']==2
    assert r['range_support']['blocked_weight_fraction']==pytest.approx(1/3)
    assert weighted_equity(hero,board,preset('uniform'))['equity']==pytest.approx(equity(hero,board)['equity'])
    for weight in [-1,math.inf,math.nan,True]:
        with pytest.raises(ValueError):legal_range(hero,[(('Ks','Kh'),weight)])
    with pytest.raises(ValueError):legal_range(hero,[(('As','Kd'),1)])
    with pytest.raises(ValueError):legal_range(hero,[(('Ks','Kh'),1),(('Kh','Ks'),2)])


def test_weighted_monte_carlo_deterministic_and_matches_exact_turn():
    from packages.pokermind_core.cards import DECK,rank
    hero=['As','Ah'];board=['2c','3d','7h','9s'];opponent=['Ks','Kh']
    shares=[]
    for river in DECK:
        if river in hero+board+opponent:continue
        a=rank(hero+board+[river]);b=rank(opponent+board+[river]);shares.append(1 if a<b else .5 if a==b else 0)
    r=weighted_equity(hero,board,[(tuple(opponent),1)],samples=20000,seed=99)
    assert r==weighted_equity(hero,board,[(tuple(opponent),1)],samples=20000,seed=99)
    assert r['equity']==pytest.approx(sum(shares)/len(shares),abs=.015)


def tied_river():
    hand=generate_hand(19,table_size=2);hand.players=[Player(seat=1,stack=1000,cards=['2c','3d']),Player(seat=2,stack=1000,cards=['4c','5d'])]
    hand.events=[];engine=Engine(hand);board=['As','Ks','Qs','Js','Ts'];offset=0;bet=False
    while not (engine.street==3 and not engine.pending):
        if not engine.pending:
            n=3 if engine.street==0 else 1;event=Event(kind='deal',cards=board[offset:offset+n]);offset+=n
        elif engine.street==3 and not bet:
            event=Event(kind='bet',seat=engine.actor,to=100);bet=True
        else:event=Event(kind='check' if 'check' in engine.legal() else 'call',seat=engine.actor)
        hand.events.append(event);engine.apply(event)
    hand.terminal_summary=engine.finish();_,observations=reconstruct(hand)
    obs=next(o for o in observations if o['state']['street']=='river' and 'call' in o['state']['legal_actions'])
    return hand,obs


def test_future_ev_does_not_double_count_sunk_chips_or_use_reveals():
    hand,obs=tied_river();result=compare(hand,obs['action_index'],obs,samples=100,seed=9)
    values={r['label']:r for r in result['actions']}
    assert values['fold']['ev']['mean']==0
    for action,row in values.items():
        if action!='fold':assert row['ev']['mean']==20 and row['ev']['standard_error']==0
    assert values['fold']['difference_from_reference']['mean']==-20
    changed=deepcopy(hand)
    other=next(p for p in changed.players if p.seat!=obs['acting_player']);other.cards=['6h','7h']
    changed.events=changed.events[:obs['action_index']]+[Event(kind='fold',seat=other.seat)]
    changed.terminal_summary.final_stacks={1:123456,2:0}
    assert compare(changed,obs['action_index'],obs,samples=100,seed=9)==result
    changed.rake=1
    assert compare(changed,obs['action_index'],obs,samples=100)['status']=='unavailable'
