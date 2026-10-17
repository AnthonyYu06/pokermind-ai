import pytest
np=pytest.importorskip('numpy')
pytest.importorskip('pyspiel')
pytest.importorskip('torch')
from packages.strategy.games import game_tree
from packages.strategy.tabular import TabularCFR,regret_matching
from packages.strategy.deep_cfr import DeepCFR,Reservoir

@pytest.mark.parametrize('name',['kuhn','leduc'])
def test_exact_best_response_matches_independent_reference(name):
    tree=game_tree(name)
    rng=np.random.default_rng(83)
    random=rng.random(tree.mask.shape)*tree.mask;random/=random.sum(1,keepdims=True)
    for p in [tree.uniform(),tree.passive(),random]:
        own=tree.evaluate(p);ref=tree.reference_metrics(p)
        assert own['nash_conv']==pytest.approx(ref['nash_conv'],abs=1e-9)
        assert own['exploitability']==pytest.approx(ref['exploitability'],abs=1e-9)
        assert sum(own['expected_returns'])==pytest.approx(0)

@pytest.mark.parametrize('name',['kuhn','leduc'])
def test_tabular_matches_independent_cpp_cfr(name):
    import pyspiel
    tree=game_tree(name);ours=TabularCFR(tree);reference=pyspiel.CFRSolver(tree.game)
    ours.train(10)
    for _ in range(10):reference.evaluate_and_update_policy()
    ref=reference.average_policy();p=ours.average()
    for i,state in enumerate(tree.states):
        for action,prob in ref.action_probabilities(state).items():assert p[i,action]==pytest.approx(prob,abs=1e-9)


def test_kuhn_converges_to_known_game_value():
    tree=game_tree('kuhn');p=TabularCFR(tree).train(1000)
    metrics=tree.evaluate(p)
    assert metrics['expected_returns'][0]==pytest.approx(-1/18,abs=.001)
    assert metrics['exploitability']<.002


def test_regret_matching_legal_nonpositive_fallback():
    mask=np.array([[True,False,True],[True,True,False]])
    values=np.array([[-3,99,-2],[2,6,99]])
    p=regret_matching(values,mask,'argmax')
    assert p.tolist()==[[0,0,1],[.25,.75,0]]
    assert regret_matching(values,mask,'uniform')[0].tolist()==[.5,0,.5]


def test_reservoir_is_bounded_and_samples_full_history():
    import random
    memory=Reservoir(500,random.Random(77))
    for i in range(10000):memory.add(i)
    assert memory.seen==10000 and len(memory.items)==500
    assert 4300<np.mean(memory.items)<5700
    assert min(memory.items)<1000 and max(memory.items)>9000


def test_checkpoint_resume_matches_uninterrupted_and_masks(tmp_path):
    tree=game_tree('kuhn');a=DeepCFR(tree,seed=71,capacity=500)
    a.step(20,10);a.save(tmp_path/'checkpoint.pt');a.step(20,10)
    expected=a.average(20)
    b=DeepCFR(tree,seed=71,capacity=500);b.restore(tmp_path/'checkpoint.pt');b.step(20,10)
    assert np.array_equal(expected,b.average(20))
    assert b.traversals==a.traversals and b.memories[0].items==a.memories[0].items
    assert np.all(expected[~tree.mask]==0) and np.allclose(expected.sum(1),1)


def test_information_state_hides_opponent_card():
    tree=game_tree('kuhn');states=[]
    for opponent in [1,2]:
        s=tree.game.new_initial_state();s.apply_action(0);s.apply_action(opponent);states.append(s)
    assert states[0].information_state_tensor(0)==states[1].information_state_tensor(0)
    assert states[0].information_state_tensor(1)!=states[1].information_state_tensor(1)
