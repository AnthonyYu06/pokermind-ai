"""Suit-invariant, order-invariant rank/suit set summaries for river equity only.

No revealed opponent cards, action labels, or outcome fields enter this encoder.
"""
from itertools import permutations
from packages.pokermind_core.contracts import digest

FEATURES_VERSION = "river-counts-v1"
FEATURE_DIM = 55


def encode(hero, board):
    if len(hero) != 2 or len(board) != 5 or len(set(hero+board)) != 7:
        raise ValueError("River encoder requires two hero and five distinct board cards")
    ranks = "23456789TJQKA"
    # Ranks are a set representation; sorting the suit-group summaries removes
    # dependence on which arbitrary suit is called clubs/hearts/etc.
    own = [sum(c[0] == r for c in hero) / 2 for r in ranks]
    public = [sum(c[0] == r for c in board) / 4 for r in ranks]
    suits = sorted([(sum(c[1] == s for c in board)/5, sum(c[1] == s for c in hero)/2,
                     max([ranks.index(c[0])+2 for c in hero if c[1]==s],default=0)/14,
                     max([ranks.index(c[0])+2 for c in board if c[1]==s],default=0)/14) for s in 'cdhs'],reverse=True)
    interactions = [own[i]*public[i] for i in range(13)]
    return own+public+[v for group in suits for v in group]+interactions


def deal_family(hero, board):
    # Same deal up to suit permutation, card order, and repeated decisions gets
    # one identity. A parent full-deal family is additionally used for splitting.
    signatures=[]
    for perm in permutations('cdhs'):
        rename=dict(zip('cdhs',perm))
        signatures.append(('|'.join(sorted(c[0]+rename[c[1]] for c in hero)), '|'.join(sorted(c[0]+rename[c[1]] for c in board))))
    return digest(min(signatures))


def full_deal_family(players, board):
    signatures=[]
    for perm in permutations('cdhs'):
        rename=dict(zip('cdhs',perm))
        holes=sorted(tuple(sorted(c[0]+rename[c[1]] for c in p.cards)) for p in players)
        signatures.append((holes,sorted(c[0]+rename[c[1]] for c in board)))
    return digest(min(signatures))
