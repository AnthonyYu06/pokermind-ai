from packages.pokermind_core.equity import equity
from packages.pokermind_core.metrics import summarize
from packages.pokermind_core.engine import reconstruct
from packages.simulation.generate import generate_hand


def test_exact_royal_board_split():
    r=equity(['2c','3d'],['As','Ks','Qs','Js','Ts'])
    assert r['equity']==.5 and r['standard_error']==0 and r['samples']==990


def test_seeded_monte_carlo_reproducible():
    a=equity(['As','Ah'],['2c','3d','7h'],samples=500,seed=42)
    assert a==equity(['As','Ah'],['2c','3d','7h'],samples=500,seed=42)
    assert .5<a['equity']<1 and a['ci95'][0]<=a['equity']<=a['ci95'][1]


def test_metrics_denominators_use_opportunities():
    hands=[generate_hand(i) for i in range(30)]
    records=[({'hero':1,'source_kind':'synthetic'},h) for h in hands]
    s=summarize(records)[0]
    assert s['vpip']['denominator']==30
    assert 0<s['three_bet']['denominator']<30
    assert s['three_bet']['numerator']<=s['three_bet']['denominator']
    assert s['net']==sum(h.terminal_summary.final_stacks[1]-h.players[0].stack for h in hands)
