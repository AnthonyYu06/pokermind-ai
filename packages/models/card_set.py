"""Permutation-invariant pooled card encoder (Deep Sets style), river domain only."""
from itertools import permutations
import numpy as np
import torch
from torch import nn

VERSION='canonical-card-set-v1'


def encode_cards(hero,board):
    from packages.pokermind_core.cards import DECK
    if len(hero)!=2 or len(board)!=5 or len(set(hero+board))!=7 or any(c not in DECK for c in hero+board):
        raise ValueError('Card set requires two private and five public distinct legal cards')
    # Canonicalize jointly, retaining the rank/suit association lost by count summaries.
    signatures=[]
    for suits in permutations('cdhs'):
        rename=dict(zip('cdhs',suits))
        signatures.append((tuple(sorted(c[0]+rename[c[1]] for c in hero)),tuple(sorted(c[0]+rename[c[1]] for c in board))))
    own,public=min(signatures);tokens=np.zeros((7,18),dtype=np.float32)
    for i,card in enumerate(own+public):
        tokens[i,'23456789TJQKA'.index(card[0])]=1
        tokens[i,13+'cdhs'.index(card[1])]=1;tokens[i,17]=i<2
    return tokens


class CardSet(nn.Module):
    def __init__(self,width=64):
        super().__init__();self.width=width
        self.card=nn.Sequential(nn.Linear(18,width),nn.ReLU(),nn.Linear(width,width),nn.ReLU())
        self.head=nn.Sequential(nn.Linear(width*4,width),nn.ReLU(),nn.Linear(width,1),nn.Sigmoid())
    def forward(self,tokens):
        x=self.card(tokens);own=x[:,:2];board=x[:,2:]
        pooled=torch.cat([own.mean(1),own.amax(1),board.mean(1),board.amax(1)],dim=1)
        return self.head(pooled).squeeze(-1)
