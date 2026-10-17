"""Bounded two-player games. OpenSpiel supplies rules, never Hold'em abstractions."""
from dataclasses import dataclass, field
from functools import lru_cache
import numpy as np
import pyspiel

GAMES = {'kuhn': 'kuhn_poker', 'leduc': 'leduc_poker'}


def load_game(name):
    if name not in GAMES: raise ValueError('Supported research games: kuhn, leduc')
    return pyspiel.load_game(GAMES[name])


@dataclass
class Node:
    player: int
    info: int = -1
    children: dict = field(default_factory=dict)
    chance: dict = field(default_factory=dict)
    returns: tuple | None = None


class GameTree:
    def __init__(self, name):
        self.name = name
        self.game = load_game(name)
        self.keys, self.features, self.legal, self.owners, self.states = [], [], [], [], []
        self.lookup, self.histories = {}, []
        self.node_count = 0
        self.actions = self.game.num_distinct_actions()
        self.root = self._build(self.game.new_initial_state())
        self.features = np.asarray(self.features, dtype=np.float32)
        self.mask = np.zeros((len(self.keys), self.actions), dtype=bool)
        for i, actions in enumerate(self.legal): self.mask[i, actions] = True

    def _build(self, state):
        self.node_count += 1
        if self.node_count > 100_000: raise ValueError('Small-game node limit exceeded')
        if state.is_terminal(): return Node(-2, returns=tuple(state.returns()))
        if state.is_chance_node():
            probs = dict(state.chance_outcomes())
            return Node(-1, children={a:self._build(state.child(a)) for a in probs}, chance=probs)
        player = state.current_player()
        key = f'{player}|{state.information_state_string(player)}'
        if key not in self.lookup:
            self.lookup[key] = len(self.keys)
            self.keys.append(key)
            self.features.append(state.information_state_tensor(player))
            self.legal.append(state.legal_actions())
            self.owners.append(player)
            self.states.append(state.clone())
            self.histories.append([])
        info = self.lookup[key]
        if self.features[info] != state.information_state_tensor(player):
            raise ValueError('Information-state encoding is inconsistent')
        node = Node(player, info, {a:self._build(state.child(a)) for a in state.legal_actions()})
        self.histories[info].append(node)
        return node

    def validate_policy(self, probabilities):
        p = np.asarray(probabilities, dtype=float)
        if p.shape != self.mask.shape or not np.isfinite(p).all() or (p < 0).any():
            raise ValueError('Invalid policy dimensions or probabilities')
        if not np.allclose(p.sum(1), 1, atol=1e-7) or np.any(p[~self.mask] != 0):
            raise ValueError('Policy must normalize over legal actions only')
        return p

    def uniform(self): return self.mask / self.mask.sum(1, keepdims=True)

    def passive(self):
        p = np.zeros(self.mask.shape)
        for i, legal in enumerate(self.legal): p[i, 0 if self.name == 'kuhn' else 1] = 1
        return self.validate_policy(p)

    def expected_returns(self, probabilities):
        p = self.validate_policy(probabilities)
        def visit(n):
            if n.returns is not None: return np.array(n.returns)
            weights = n.chance if n.player == -1 else p[n.info]
            return sum(weights[a] * visit(c) for a, c in n.children.items())
        return visit(self.root)

    def best_response(self, probabilities, player):
        """Exact information-set BR, aggregating hidden histories before choosing."""
        p = self.validate_policy(probabilities)
        reach = {}
        def propagate(n, weight):
            reach[id(n)] = weight
            for a, c in n.children.items():
                prob = n.chance[a] if n.player == -1 else (p[n.info,a] if n.player != player else 1)
                propagate(c, weight * prob)
        propagate(self.root, 1.)
        choices = {}
        def value(n):
            if n.returns is not None: return n.returns[player]
            if n.player == player:
                if n.info not in choices:
                    values = {a:sum(reach[id(h)] * value(h.children[a]) for h in self.histories[n.info]) for a in n.children}
                    choices[n.info] = max(values, key=values.get)
                return value(n.children[choices[n.info]])
            weights = n.chance if n.player == -1 else p[n.info]
            return sum(weights[a] * value(c) for a,c in n.children.items())
        return float(value(self.root))

    def evaluate(self, probabilities):
        ev = self.expected_returns(probabilities)
        br = [self.best_response(probabilities,p) for p in range(2)]
        nash_conv = sum(br) - float(sum(ev))
        return {'expected_returns':ev.tolist(), 'best_response_values':br,
                'nash_conv':nash_conv, 'exploitability':nash_conv/2,
                'units':'OpenSpiel game chips per hand; exploitability = NashConv / 2'}

    def reference_metrics(self, probabilities):
        from open_spiel.python import policy
        from open_spiel.python.algorithms import exploitability
        p = self.validate_policy(probabilities)
        tab = policy.TabularPolicy(self.game)
        for i,key in enumerate(self.keys):
            tab.action_probability_array[tab.state_lookup[key.split('|',1)[1]]] = p[i]
        return {'nash_conv':float(exploitability.nash_conv(self.game,tab)),
                'exploitability':float(exploitability.exploitability(self.game,tab))}


@lru_cache(maxsize=2)
def game_tree(name): return GameTree(name)
