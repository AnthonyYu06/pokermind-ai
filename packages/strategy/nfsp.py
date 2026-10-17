"""Bounded NFSP research: Double DQN response learning and reservoir averaging.

Based on Heinrich & Silver (2016), https://arxiv.org/abs/1603.01121.
This implementation has no convergence or general poker optimality claim.
"""
from collections import deque
from copy import deepcopy
import random

import torch
from torch import nn

VERSION = 'nfsp-research-v1'


class PolicyNet(nn.Module):
    def __init__(self, inputs, actions, width):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(inputs, width), nn.ReLU(),
                                 nn.Linear(width, width), nn.ReLU(), nn.Linear(width, actions))

    def forward(self, x): return self.net(x)


class Agent:
    def __init__(self, inputs, actions, config, seed):
        self.inputs, self.actions, self.config = inputs, actions, dict(config)
        self.rng = random.Random(seed)
        # Isolate initialization from callers and from the other player's RNG.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            self.q = PolicyNet(inputs, actions, config['width'])
            self.average = PolicyNet(inputs, actions, config['width'])
        self.target = deepcopy(self.q).eval()
        self.q_optimizer = torch.optim.Adam(self.q.parameters(), lr=config['rl_learning_rate'])
        self.sl_optimizer = torch.optim.Adam(self.average.parameters(), lr=config['sl_learning_rate'])
        self.replay = deque(maxlen=config['replay_capacity'])
        self.reservoir, self.seen = [], 0
        self.decisions, self.updates = 0, 0
        self.best_response = False
        self.last_losses = {}

    def begin_episode(self):
        self.best_response = self.rng.random() < self.config['anticipatory']

    @torch.inference_mode()
    def probabilities(self, x, mask, average=True):
        mask = torch.as_tensor(mask, dtype=torch.bool)
        if not mask.any(): raise ValueError('A policy needs a legal action')
        values = (self.average if average else self.q)(torch.as_tensor(x).float().unsqueeze(0))[0]
        if not torch.isfinite(values).all(): raise ValueError('Non-finite policy output')
        if average:
            return values.masked_fill(~mask, -1e9).softmax(-1)
        epsilon = max(self.config['epsilon_end'], self.config['epsilon_start'] -
                      (self.config['epsilon_start'] - self.config['epsilon_end']) * self.decisions / self.config['epsilon_decay_steps'])
        legal = mask.nonzero().flatten().tolist()
        maximum = values[mask].max()
        greedy = (mask & (values >= maximum - 1e-8)).nonzero().flatten().tolist()
        p = mask.float() * (epsilon / len(legal))
        p[greedy] += (1 - epsilon) / len(greedy)
        return p

    def act(self, x, mask):
        p = self.probabilities(x, mask, average=not self.best_response)
        action = self.rng.choices(range(self.actions), weights=p.tolist())[0]
        if self.best_response:
            self.seen += 1
            item = (x.clone(), mask.clone(), action)
            capacity = self.config['reservoir_capacity']
            if len(self.reservoir) < capacity: self.reservoir.append(item)
            else:
                index = self.rng.randrange(self.seen)
                if index < capacity: self.reservoir[index] = item
        self.decisions += 1
        return action

    def observe(self, x, action, reward, next_x, next_mask, terminal):
        self.replay.append((x.clone(), action, float(reward), next_x.clone(), next_mask.clone(), bool(terminal)))
        if len(self.replay) >= self.config['minimum_buffer'] and self.decisions % self.config['learn_every'] == 0:
            self.learn()

    def learn(self):
        batch = self.rng.sample(list(self.replay), min(self.config['batch_size'], len(self.replay)))
        x = torch.stack([b[0] for b in batch]); actions = torch.tensor([b[1] for b in batch])
        rewards = torch.tensor([b[2] for b in batch]); next_x = torch.stack([b[3] for b in batch])
        masks = torch.stack([b[4] for b in batch]); terminal = torch.tensor([b[5] for b in batch])
        with torch.no_grad():
            selected = self.q(next_x).masked_fill(~masks, -1e9).argmax(-1)
            continuation = self.target(next_x).gather(1, selected[:, None]).squeeze(1)
            target = rewards + torch.where(terminal, 0., continuation)
        loss = nn.functional.smooth_l1_loss(self.q(x).gather(1, actions[:, None]).squeeze(1), target)
        self._update(loss, self.q, self.q_optimizer)
        self.last_losses['rl'] = float(loss.detach())
        if len(self.reservoir) >= self.config['minimum_buffer']:
            batch = self.rng.sample(self.reservoir, min(self.config['batch_size'], len(self.reservoir)))
            x = torch.stack([b[0] for b in batch]); masks = torch.stack([b[1] for b in batch])
            labels = torch.tensor([b[2] for b in batch])
            loss = nn.functional.cross_entropy(self.average(x).masked_fill(~masks, -1e9), labels)
            self._update(loss, self.average, self.sl_optimizer)
            self.last_losses['sl'] = float(loss.detach())
        self.updates += 1
        if self.updates % self.config['target_every'] == 0:
            self.target.load_state_dict(self.q.state_dict())

    @staticmethod
    def _update(loss, model, optimizer):
        if not torch.isfinite(loss): raise ValueError('Non-finite NFSP loss')
        optimizer.zero_grad(); loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5., error_if_nonfinite=True)
        optimizer.step()

    def state(self):
        return {'inputs': self.inputs, 'actions': self.actions, 'config': self.config,
                'q': self.q.state_dict(), 'target': self.target.state_dict(), 'average': self.average.state_dict(),
                'q_optimizer': self.q_optimizer.state_dict(), 'sl_optimizer': self.sl_optimizer.state_dict(),
                'replay': list(self.replay), 'reservoir': self.reservoir, 'seen': self.seen,
                'decisions': self.decisions, 'updates': self.updates, 'rng': self.rng.getstate(),
                'best_response': self.best_response, 'last_losses': self.last_losses}

    def restore(self, saved):
        if (saved['inputs'], saved['actions'], saved['config']) != (self.inputs, self.actions, self.config):
            raise ValueError('NFSP checkpoint contract mismatch')
        for name in ['q', 'target', 'average', 'q_optimizer', 'sl_optimizer']:
            getattr(self, name).load_state_dict(saved[name])
        self.replay = deque(saved['replay'], maxlen=self.config['replay_capacity'])
        for name in ['reservoir', 'seen', 'decisions', 'updates', 'best_response', 'last_losses']:
            setattr(self, name, saved[name])
        self.rng.setstate(saved['rng'])


def train_episode(env, agents):
    """Store transitions between the SAME player's decisions, including folds."""
    previous = [None, None]
    for agent in agents: agent.begin_episode()
    count = 0
    while not env.terminal:
        player = env.player; agent = agents[player]
        x, mask = env.observation()
        if previous[player] is not None:
            old_x, action = previous[player]
            agent.observe(old_x, action, 0., x, mask, False)
        action = agent.act(x, mask); previous[player] = (x, action)
        env.step(action); count += 1
        if count > 200: raise ValueError('Episode action bound exceeded')
    for player, item in enumerate(previous):
        if item is not None:
            x, action = item
            agents[player].observe(x, action, env.returns[player], torch.zeros_like(x),
                                   torch.zeros(agents[player].actions, dtype=torch.bool), True)
    return count
