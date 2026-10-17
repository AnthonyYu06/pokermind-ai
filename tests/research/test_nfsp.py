from copy import deepcopy
import json
from pathlib import Path
import time

import pytest
torch = pytest.importorskip('torch')
pytest.importorskip('numpy')
pytest.importorskip('pyspiel')

from packages.features.behavior import ACTIONS, candidates
from packages.pokermind_core.contracts import Event
from packages.pokermind_core.engine import reconstruct
from packages.strategy.nfsp import Agent, train_episode
from packages.strategy.holdem_nfsp import DIM, Features, HoldemEnv, passive_template
from training.holdem_nfsp import fit, kuhn_metrics


def config():
    c = json.loads(Path('configs/holdem-nfsp-v1.json').read_text())
    c['agent'].update(width=16, minimum_buffer=4, batch_size=4, learn_every=1,
                      target_every=3, replay_capacity=40, reservoir_capacity=20, anticipatory=.5)
    c.update(kuhn_episodes=30, evaluation_interval=15)
    return c


def test_holdem_features_never_read_hidden_cards_or_chance_and_masks_are_legal(tmp_path):
    features = Features(tmp_path, None)
    streets = set()
    for seed in range(6):
        env = HoldemEnv(seed, features)
        while not env.terminal:
            obs = env.visible(); x, mask = features(obs); streets.add(obs['state']['street'])
            assert x.shape == (DIM,) and torch.isfinite(x).all()
            altered = deepcopy(obs)
            next(p for p in altered['state']['seats'] if p['seat'] != obs['acting_player'])['cards'] = ['As', 'Ah']
            altered['future_board'] = ['As', 'Kh', 'Qh', 'Th', 'Jh']
            assert torch.equal(x, features(altered)[0])
            for i in mask.nonzero().flatten().tolist():
                sim = deepcopy(env.engine); sim.apply(Event(**candidates(obs)[ACTIONS[i]]))
            env.step(ACTIONS.index('check' if 'check' in candidates(obs) else 'call'))
        assert sum(env.returns) == pytest.approx(0)
        assert all(-1 <= v <= 1 for v in env.returns)
        reconstruct(passive_template(seed))
    assert streets == {'preflop', 'flop', 'turn', 'river'}


def test_nfsp_resume_is_identical_and_illegal_actions_have_zero_probability(tmp_path):
    torch.set_num_threads(2)
    cfg = config()
    full, _ = fit('kuhn', 51, cfg, tmp_path/'full', None, 'same', time.monotonic()+60)
    early = {**cfg, 'kuhn_episodes': 15}
    fit('kuhn', 51, early, tmp_path/'part', None, 'same', time.monotonic()+60)
    resumed, _ = fit('kuhn', 51, cfg, tmp_path/'part', None, 'same', time.monotonic()+60, True)
    assert kuhn_metrics(full) == kuhn_metrics(resumed)
    for a, b in zip(full, resumed):
        assert a.seen == b.seen and a.updates == b.updates and a.rng.getstate() == b.rng.getstate()
        assert all(torch.equal(value, b.average.state_dict()[key]) for key, value in a.average.state_dict().items())
        assert len(a.replay) <= 40 and len(a.reservoir) <= 20
        p = a.probabilities(torch.zeros(a.inputs), torch.tensor([True, False]))
        assert p.tolist() == [1., 0.]
    with pytest.raises(ValueError, match='mismatch'):
        fit('kuhn', 51, cfg, tmp_path/'part', None, 'different', time.monotonic()+60, True)


def test_supervised_memory_only_records_response_episodes_and_terminal_rewards():
    cfg = config()['agent']; agent = Agent(3, 2, cfg, 12)
    x = torch.zeros(3); mask = torch.tensor([True, True])
    agent.best_response = False
    for _ in range(5): agent.act(x, mask)
    assert agent.seen == 0 and not agent.reservoir
    agent.best_response = True
    for _ in range(50): agent.act(x, mask)
    assert agent.seen == 50 and len(agent.reservoir) == 20
    # Terminal targets must never bootstrap, even with huge target-network outputs.
    with torch.no_grad():
        for value in agent.q.parameters(): value.zero_()
        for value in agent.target.parameters(): value.fill_(1000.)
    for _ in range(4): agent.replay.append((x, 0, 1., x, torch.tensor([False, False]), True))
    agent.learn()
    assert agent.last_losses['rl'] == pytest.approx(.5)


def test_episode_credit_follows_each_players_own_decisions():
    class Recorder:
        actions = 2
        def __init__(self): self.events=[]
        def begin_episode(self): pass
        def act(self,x,mask): return 0
        def observe(self,*args): self.events.append(args)
    class Env:
        terminal=False; player=0; counter=0
        def observation(self): return torch.tensor([float(self.counter)]), torch.tensor([True,True])
        def step(self,action):
            self.counter+=1;self.player=1-self.player
            if self.counter==3:self.terminal=True;self.returns=[-1,1]
    agents=[Recorder(),Recorder()]; train_episode(Env(),agents)
    assert agents[0].events[0][0].item()==0 and agents[0].events[0][3].item()==2
    assert agents[0].events[-1][2]==-1 and agents[0].events[-1][-1] is True
    assert agents[1].events[-1][2]==1 and agents[1].events[-1][-1] is True
