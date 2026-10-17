import random
import pytest
from hypothesis import given, settings, strategies as st

from packages.pokermind_core.cards import DECK, rank, reference_rank
from packages.pokermind_core.contracts import Event, Hand, Player, Summary
from packages.pokermind_core.engine import Engine, RulesError, reconstruct
from packages.simulation.generate import generate_hand


def table(stacks, cards=None, button=None, rake=0):
    cards = cards or [['As','Ah'],['Ks','Kh'],['Qs','Qh'],['Js','Jh'],['Ts','Th'],['9s','9h']]
    return Hand.model_construct(source_hand_id='test', session_id='fixture', button=button or len(stacks),
        small_blind=10, big_blind=20, players=[Player(seat=i+1, stack=s, cards=cards[i]) for i,s in enumerate(stacks)],
        hero=1, events=[], rake=rake, terminal_summary=Summary(awards={},final_stacks={},rake=rake))


def act(engine, seat, kind, to=None):
    engine.apply(Event(kind=kind, seat=seat, to=to))


def runout(engine, board=('2c','3d','7c','8d','9c')):
    for cards in (board[:3],board[3:4],board[4:]):
        engine.apply(Event(kind='deal', cards=list(cards)))
        while engine.actor: act(engine,engine.actor,'check')


def test_heads_up_blinds_and_order():
    e=Engine(table([1000,1000],button=1))
    assert e.actor==1 and e.bets=={1:10,2:20}
    act(e,1,'call'); assert e.actor==2
    act(e,2,'check')
    e.apply(Event(kind='deal',cards=['2c','3d','7c']))
    assert e.actor==2


def test_six_max_fold_and_uncalled_refund():
    h=table([1000]*6);e=Engine(h)
    act(e,3,'fold');act(e,4,'fold');act(e,5,'raise',60);act(e,6,'fold');act(e,1,'fold');act(e,2,'call')
    assert e.pot==130
    e.apply(Event(kind='deal',cards=['2c','3d','7c']))
    act(e,2,'check');act(e,5,'bet',80);act(e,2,'fold')
    result=e.finish()
    assert e.refunds[5]==80
    assert result.awards=={1:0,2:0,3:0,4:0,5:130,6:0}
    assert result.final_stacks=={1:990,2:940,3:1000,4:1000,5:1070,6:1000}


def test_sidepots_and_short_raise_does_not_reopen():
    h=table([1000,150,100],cards=[['Qs','Qh'],['Ks','Kh'],['As','Ah']]);e=Engine(h)
    act(e,3,'raise',100);act(e,1,'call');act(e,2,'raise',150)
    assert e.actor==1 and 'raise' not in e.legal()
    act(e,1,'call');runout(e)
    assert e.finish().final_stacks=={1:850,2:100,3:300}
    assert [p['amount'] for p in e.pots]==[300,100]


def test_cumulative_short_raises_reopen():
    e=Engine(table([1000,1000,1000,150,200]))
    act(e,3,'raise',100);act(e,4,'raise',150);act(e,5,'raise',200);act(e,1,'call');act(e,2,'fold')
    assert e.actor==3 and e.legal()['raise']['min_to']==280


def test_allin_call_for_less_and_refund():
    e=Engine(table([1000,45],button=1))
    act(e,1,'raise',100);act(e,2,'call')
    assert e.contributions=={1:45,2:45} and e.refunds[1]==55
    runout(e)
    assert e.finish().final_stacks=={1:1045,2:0}


def test_folded_dead_money_odd_chip_and_rake():
    # Board plays. 60 pot minus one unit of rake splits 59: odd chip to seat 1.
    e=Engine(table([1000]*3,rake=1))
    act(e,3,'call');act(e,1,'call');act(e,2,'check')
    e.apply(Event(kind='deal',cards=['Tc','Jc','Qc']))
    act(e,1,'check');act(e,2,'check');act(e,3,'check')
    e.apply(Event(kind='deal',cards=['Kc']))
    act(e,1,'check');act(e,2,'check');act(e,3,'check')
    e.apply(Event(kind='deal',cards=['Ac']))
    act(e,1,'bet',20);act(e,2,'fold');act(e,3,'call')
    result=e.finish()
    assert result.awards=={1:50,2:0,3:49}
    assert sum(result.final_stacks.values())==2999


def test_illegal_action_and_premature_board():
    e=Engine(table([1000]*6))
    with pytest.raises(RulesError):act(e,1,'call')
    with pytest.raises(RulesError):act(e,3,'raise',21)
    with pytest.raises(RulesError):e.apply(Event(kind='deal',cards=['2c','3c','4c']))
    with pytest.raises(RulesError):e.finish()


def test_ace_low_and_board_tie():
    assert rank(['As','2c','3h','4d','5s','8c','9h']) < rank(['Ks','Kc','Qh','4d','5s','8c','9h'])
    board=['As','Ks','Qs','Js','Ts']
    assert rank(['2c','3c']+board)==rank(['4d','5d']+board)
    with pytest.raises(ValueError):rank(['As']*7)


@given(st.integers(min_value=0,max_value=100000))
@settings(max_examples=60,deadline=None)
def test_seeded_trajectories_conserve_and_replay(seed):
    hand=generate_hand(seed)
    snapshots,obs=reconstruct(hand,viewer=1)
    assert sum(hand.terminal_summary.final_stacks.values())+hand.rake==sum(p.stack for p in hand.players)
    assert snapshots[-1]['settled']
    assert all(s['stack']>=0 for state in snapshots for s in state['seats'])
    assert hand==generate_hand(seed)
    for o in obs:
        assert all(not s['cards'] for s in o['state']['seats'] if s['seat']!=o['acting_player'])


def test_evaluator_differential_and_suit_invariance():
    rng=random.Random(789)
    for _ in range(300):
        a=rng.sample(DECK,7);b=rng.sample(DECK,7)
        assert (rank(a)>rank(b))-(rank(a)<rank(b)) == -((reference_rank(a)>reference_rank(b))-(reference_rank(a)<reference_rank(b)))
        rename={'c':'d','d':'s','s':'h','h':'c'}
        assert rank(a)==rank([c[0]+rename[c[1]] for c in a])


def test_observation_independent_of_hidden_cards_and_future():
    h=generate_hand(2026)
    e=Engine(h)
    initial=e.snapshot(e.actor)
    hidden=next(s for s in e.seats if s!=e.actor)
    e.cards[hidden]=['2c','3c']
    assert e.snapshot(e.actor)==initial
    # A future event is not part of the initial snapshot or action prefix.
    h2=h.model_copy(deep=True)
    h2.events[-1]=Event(kind='check',seat=1)
    assert Engine(h2).snapshot(e.actor)==initial
