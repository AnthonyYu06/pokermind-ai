"""Independent full-tree alternating vanilla CFR with own-reach averaging."""
import numpy as np


def regret_matching(values, mask, fallback='uniform'):
    values = np.asarray(values, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    positive = np.where(mask, np.maximum(values,0),0)
    total = positive.sum(-1,keepdims=True)
    result = positive / np.maximum(total,1e-30)
    missing = total[:,0] <= 0
    if fallback == 'uniform': result[missing] = mask[missing]/mask[missing].sum(-1,keepdims=True)
    elif fallback == 'argmax':
        ids = np.flatnonzero(missing)
        result[ids,np.argmax(np.where(mask[ids],values[ids],-np.inf),axis=1)] = 1
    else: raise ValueError('Unknown regret fallback')
    return result


class TabularCFR:
    def __init__(self, tree):
        self.tree = tree
        self.regrets = np.zeros(tree.mask.shape)
        self.strategy_sum = np.zeros(tree.mask.shape)
        self.iteration = 0

    def step(self):
        tree = self.tree
        for updating in range(2):
            strategy = regret_matching(self.regrets, tree.mask)
            delta = np.zeros_like(self.regrets)
            def visit(node, reach, chance):
                if node.returns is not None: return node.returns[updating]
                if node.player == -1:
                    return sum(prob*visit(node.children[a],reach,chance*prob) for a,prob in node.chance.items())
                player,i = node.player,node.info
                values = np.zeros(tree.actions)
                for a,child in node.children.items():
                    next_reach = reach.copy(); next_reach[player] *= strategy[i,a]
                    values[a] = visit(child,next_reach,chance)
                expected = float(strategy[i]@values)
                if player == updating:
                    delta[i] += chance*reach[1-player]*np.where(tree.mask[i],values-expected,0)
                    # Histories within an infoset have identical own reach; repeated
                    # additions multiply its average by a constant, which cancels.
                    self.strategy_sum[i] += reach[player]*strategy[i]
                return expected
            visit(tree.root,[1.,1.],1.)
            self.regrets += delta
        self.iteration += 1

    def train(self, iterations):
        for _ in range(iterations): self.step()
        return self.average()

    def average(self):
        return regret_matching(self.strategy_sum,self.tree.mask)
