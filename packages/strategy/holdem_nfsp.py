"""Server-generated, 50-BB heads-up simulation and visible-only policy features."""
from copy import deepcopy
from functools import lru_cache
import random

import torch
from packages.features.behavior import ACTIONS, MAX_PREFIX, candidates, encode
from packages.models.river_inference import predict
from packages.pokermind_core.cards import DECK
from packages.pokermind_core.contracts import Hand, Player, Event, Summary
from packages.pokermind_core.engine import Engine

FEATURE_VERSION = 'holdem-nfsp-visible-v1'
DIM = 45 + MAX_PREFIX * 9 + len(ACTIONS) + 2
STACK = 1000


class Features:
    def __init__(self, root, river_pointer):
        self.root, self.pointer = root, river_pointer
        self._equity = lru_cache(maxsize=2048)(self._predict)

    def _predict(self, hero, board):
        value = predict(self.root, self.pointer, list(hero), list(board))
        if value['status'] != 'available': raise ValueError('Frozen river feature unavailable: ' + value['reason'])
        return value['equity']

    def __call__(self, observation):
        x, sequence, mask = encode(observation)
        padded = torch.zeros(MAX_PREFIX, 9); padded[:len(sequence)] = torch.from_numpy(sequence)
        state = observation['state']; actor = observation['acting_player']
        own = next(s['cards'] for s in state['seats'] if s['seat'] == actor)
        river = len(state['board']) == 5 and self.pointer is not None
        probability = self._equity(tuple(sorted(own)), tuple(sorted(state['board']))) if river else .5
        features = torch.cat([torch.from_numpy(x), padded.flatten(), torch.from_numpy(mask).float(),
                              torch.tensor([probability, float(river)])])
        return features, torch.from_numpy(mask)


class HoldemEnv:
    def __init__(self, seed, features, button=None):
        self.features = features; rng = random.Random(seed)
        self.deck = list(DECK); rng.shuffle(self.deck)
        self.hand = Hand.model_construct(source_hand_id='nfsp-simulation', session_id='internal-self-play',
            button=button or (seed % 2 + 1), hero=1, small_blind=10, big_blind=20, rake=0,
            players=[Player(seat=s, stack=STACK, cards=[self.deck.pop(), self.deck.pop()]) for s in (1, 2)],
            events=[], terminal_summary=Summary(awards={}, final_stacks={}, rake=0))
        self.engine = Engine(self.hand); self.events = []; self.terminal = False; self.returns = None
        self._advance()

    @property
    def player(self): return self.engine.actor - 1

    def visible(self):
        return {'acting_player': self.engine.actor, 'state': self.engine.snapshot(self.engine.actor),
                'public_action_prefix': [e.model_dump() for e in self.events]}

    def observation(self): return self.features(self.visible())

    def step(self, action):
        if self.terminal: raise ValueError('Episode already complete')
        choice = candidates(self.visible()).get(ACTIONS[action])
        if choice is None: raise ValueError('Illegal policy action')
        event = Event(**choice); self.engine.apply(event); self.events.append(event); self._advance()

    def _advance(self):
        while not self.engine.pending:
            if len(self.engine.active) == 1 or self.engine.street == 3:
                summary = self.engine.finish(); self.terminal = True
                self.returns = [(summary.final_stacks[p] - STACK) / STACK for p in (1, 2)]
                return
            event = Event(kind='deal', cards=[self.deck.pop() for _ in range(3 if self.engine.street == 0 else 1)])
            self.engine.apply(event); self.events.append(event)


def passive_template(seed):
    """A valid full hand used only to initialize the existing server game store."""
    env = HoldemEnv(seed, None)
    while not env.terminal:
        labels = candidates(env.visible())
        env.step(ACTIONS.index('check' if 'check' in labels else 'call'))
    hand = deepcopy(env.hand); hand.events = env.events
    hand.terminal_summary = Summary(awards=env.engine.awards, final_stacks=env.engine.stacks, rake=0)
    return Hand.model_validate(hand.model_dump())
