"""Reference showdown pot share, heads-up versus a uniform legal random range."""
import random
from itertools import combinations
from math import sqrt
from statistics import mean, variance

from .cards import DECK, rank

MODEL_VERSION = "uniform-hu-equity-v1"


def equity(hero, board, *, samples=2000, seed=1):
    if len(hero) != 2 or len(board) not in (0, 3, 4, 5): raise ValueError("Unsupported card shape")
    known = hero + board
    if len(set(known)) != len(known) or any(c not in DECK for c in known): raise ValueError("Impossible cards")
    if not 100 <= samples <= 20_000: raise ValueError("Simulation budget must be 100–20,000")
    deck = [c for c in DECK if c not in known]
    shares = []
    exact = len(board) == 5
    rng = random.Random(seed)
    trials = combinations(deck, 2) if exact else (rng.sample(deck, 7 - len(board)) for _ in range(samples))
    fixed_rank = rank(hero + board) if exact else None
    for draw in trials:
        opponent = list(draw[:2])
        runout = board + list(draw[2:])
        a, b = fixed_rank if exact else rank(hero + runout), rank(opponent + runout)
        shares.append(1.0 if a < b else 0.5 if a == b else 0.0)
    value = mean(shares)
    se = 0.0 if exact else sqrt(variance(shares) / len(shares))
    return {"equity": value, "standard_error": se,
            "ci95": [max(0.0, value-1.96*se), min(1.0, value+1.96*se)],
            "samples": len(shares), "seed": seed, "method": "exact enumeration" if exact else "seeded Monte Carlo",
            "model_version": MODEL_VERSION, "range": "One opponent, uniform over legal card combinations",
            "assumptions": "Showdown pot share only; no folding, future betting, or rake. This is not action EV or a recommended action."}


def score_observation(obs, *, seed=1):
    state = obs["state"]
    active = [s for s in state["seats"] if not s["folded"]]
    hero = next(s for s in active if s["seat"] == obs["acting_player"])
    if len(active) != 2 or len(hero["cards"]) != 2:
        return {"status": "unavailable", "reason": "Reference currently supports two active players with known acting-player cards only", "model_version": MODEL_VERSION}
    return {"status": "available", **equity(hero["cards"], state["board"], seed=seed)}
