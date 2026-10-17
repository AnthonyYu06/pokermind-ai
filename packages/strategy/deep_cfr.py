"""Brown et al. (2019), Algorithms 1–2, bounded CPU implementation.

External sampling uses chance/other-player reach through sampling, without
multiplying it a second time. Only traverser actions are enumerated. Uniform
reservoir samples are weighted by iteration in BOTH regression objectives.
"""
import random
import numpy as np
import torch
from torch import nn
from .tabular import regret_matching

VERSION = 'deep-cfr-small-v1'


class Reservoir:
    def __init__(self, capacity, rng):
        if capacity < 1: raise ValueError('Positive reservoir capacity required')
        self.capacity, self.rng, self.seen, self.items = capacity, rng, 0, []

    def add(self, item):
        self.seen += 1
        if len(self.items) < self.capacity: self.items.append(item)
        else:
            j = self.rng.randrange(self.seen)
            if j < self.capacity: self.items[j] = item

    def state(self): return {'capacity':self.capacity,'seen':self.seen,'items':self.items}

    def restore(self,state):
        if state['capacity'] != self.capacity: raise ValueError('Reservoir config mismatch')
        self.seen,self.items = state['seen'],state['items']


class Network(nn.Module):
    def __init__(self, inputs, actions, width=64):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(inputs,width),nn.ReLU(),nn.Linear(width,width),nn.ReLU(),nn.Linear(width,actions))
    def forward(self,x):return self.net(x)


class DeepCFR:
    def __init__(self,tree,seed=11,capacity=30_000,width=64,linear=True,reset=True):
        self.tree,self.seed,self.width = tree,seed,width
        self.linear,self.reset = linear,reset
        self.rng = random.Random(seed)
        torch.manual_seed(seed); torch.set_num_threads(2); torch.use_deterministic_algorithms(True)
        self.features=torch.tensor(tree.features)
        self.mask=torch.tensor(tree.mask)
        self.advantages=[self.network() for _ in range(2)]
        self.strategy=self.network()
        self.memories=[Reservoir(capacity,self.rng) for _ in range(2)]
        self.strategy_memory=Reservoir(capacity,self.rng)
        self.iteration,self.traversals,self.curve=0,0,[]

    def network(self): return Network(self.features.shape[1],self.tree.actions,self.width)

    def _traverse(self,node,player,strategies):
        if node.returns is not None:return node.returns[player]
        if node.player == -1:
            a=self.rng.choices(list(node.chance),weights=list(node.chance.values()))[0]
            return self._traverse(node.children[a],player,strategies)
        i=node.info; strategy=strategies[node.player][i]
        if node.player == player:
            values=np.zeros(self.tree.actions)
            for a,child in node.children.items(): values[a]=self._traverse(child,player,strategies)
            expected=float(strategy@values)
            regret=np.where(self.tree.mask[i],values-expected,0)
            self.memories[player].add((i,self.iteration+1,regret.tolist()))
            return expected
        self.strategy_memory.add((i,self.iteration+1,strategy.tolist()))
        action=self.rng.choices(list(node.children),weights=[strategy[a] for a in node.children])[0]
        return self._traverse(node.children[action],player,strategies)

    def _fit(self,network,memory,steps,policy=False):
        if not memory.items:raise ValueError('No traversal samples')
        optimizer=torch.optim.Adam(network.parameters(),lr=.003)
        ids=torch.tensor([x[0] for x in memory.items],dtype=torch.long)
        weights=torch.tensor([x[1] if self.linear else 1 for x in memory.items],dtype=torch.float32)
        weights=weights/weights.max()
        targets=torch.tensor([x[2] for x in memory.items],dtype=torch.float32)
        for _ in range(steps):
            idx=torch.randint(len(ids),(min(256,len(ids)),))
            selected=ids[idx];mask=self.mask[selected]
            pred=network(self.features[selected])
            if policy:pred=pred.masked_fill(~mask,-1e9).softmax(-1)
            loss=(((pred-targets[idx])**2*mask).sum(-1)*weights[idx]).mean()
            if not torch.isfinite(loss):raise FloatingPointError('Non-finite CFR loss')
            optimizer.zero_grad();loss.backward()
            norm=nn.utils.clip_grad_norm_(network.parameters(),5,error_if_nonfinite=True)
            optimizer.step()
        return float(loss.detach()),float(norm)

    def step(self,traversals=200,train_steps=150):
        for player in range(2):
            with torch.no_grad():
                strategies=[regret_matching(net(self.features).numpy(),self.tree.mask,'argmax') for net in self.advantages]
            if self.iteration == 0:
                # Each untrained player's first policy is explicitly uniform.
                for p in range(player,2):strategies[p]=self.tree.uniform()
            for _ in range(traversals):self._traverse(self.tree.root,player,strategies)
            if self.reset:self.advantages[player]=self.network()
            self._fit(self.advantages[player],self.memories[player],train_steps)
            self.traversals+=traversals
        self.iteration+=1

    def average(self,steps=500):
        # Evaluation cannot perturb traversal/training random state.
        with torch.random.fork_rng():
            torch.manual_seed(self.seed+10_000+self.iteration)
            self.strategy=self.network()
            loss,norm=self._fit(self.strategy,self.strategy_memory,steps,policy=True)
        with torch.no_grad():p=self.strategy(self.features).masked_fill(~self.mask,-1e9).softmax(-1).numpy().astype(float)
        p/=p.sum(1,keepdims=True)
        return self.tree.validate_policy(p)

    def save(self,path):
        state={'version':VERSION,'game':self.tree.name,'seed':self.seed,'width':self.width,'linear':self.linear,'reset':self.reset,
               'iteration':self.iteration,'traversals':self.traversals,'curve':self.curve,'keys':self.tree.keys,
               'advantages':[n.state_dict() for n in self.advantages],'strategy':self.strategy.state_dict(),
               'memories':[m.state() for m in self.memories],'strategy_memory':self.strategy_memory.state(),
               'python_rng':self.rng.getstate(),'torch_rng':torch.get_rng_state()}
        temp=path.with_suffix('.tmp');torch.save(state,temp);temp.replace(path)

    def restore(self,path):
        state=torch.load(path,map_location='cpu',weights_only=True)
        for key,wanted in [('version',VERSION),('game',self.tree.name),('keys',self.tree.keys),('seed',self.seed),('width',self.width),('linear',self.linear),('reset',self.reset)]:
            if state[key]!=wanted:raise ValueError('Checkpoint configuration mismatch: '+key)
        self.iteration,self.traversals,self.curve=state['iteration'],state['traversals'],state['curve']
        for net,weights in zip(self.advantages,state['advantages']):net.load_state_dict(weights)
        self.strategy.load_state_dict(state['strategy'])
        for memory,items in zip(self.memories,state['memories']):memory.restore(items)
        self.strategy_memory.restore(state['strategy_memory'])
        self.rng.setstate(state['python_rng']);torch.set_rng_state(state['torch_rng'])
