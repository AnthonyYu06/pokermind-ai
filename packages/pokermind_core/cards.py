"""Treys (MIT), low rank wins. Independent tuple reference: high tuple wins."""
from collections import Counter
from itertools import combinations
from treys import Card, Evaluator

DECK = tuple(r + s for r in "23456789TJQKA" for s in "cdhs")
_EVALUATOR = Evaluator()


def rank(cards: list[str]) -> int:
    if len(cards) != 7 or len(set(cards)) != 7 or any(c not in DECK for c in cards):
        raise ValueError("Evaluator requires seven distinct valid cards")
    encoded = [Card.new(c) for c in cards]
    return _EVALUATOR.evaluate(encoded[:2], encoded[2:])


def reference_five(cards):
    counts = Counter("23456789TJQKA".index(c[0]) + 2 for c in cards)
    ranks = sorted(counts, reverse=True)
    flush = len({c[1] for c in cards}) == 1
    straight = (5 if ranks == [14, 5, 4, 3, 2] else ranks[0]) if len(ranks) == 5 and (ranks[0] - ranks[4] == 4 or ranks == [14, 5, 4, 3, 2]) else 0
    groups = sorted(((n, r) for r, n in counts.items()), reverse=True)
    if flush and straight: return (8, straight)
    if groups[0][0] == 4: return (7, groups[0][1], groups[1][1])
    if [g[0] for g in groups] == [3, 2]: return (6, groups[0][1], groups[1][1])
    if flush: return (5, *ranks)
    if straight: return (4, straight)
    if groups[0][0] == 3: return (3, groups[0][1], *sorted((r for n, r in groups[1:]), reverse=True))
    if [g[0] for g in groups[:2]] == [2, 2]: return (2, *sorted((g[1] for g in groups[:2]), reverse=True), groups[2][1])
    if groups[0][0] == 2: return (1, groups[0][1], *sorted((r for n, r in groups[1:]), reverse=True))
    return (0, *ranks)


def reference_rank(cards):
    return max(reference_five(c) for c in combinations(cards, 5))
