"""Seeded, bounded synthetic trajectories; all policies act only in this engine."""
import random
from datetime import datetime, timedelta, timezone

from packages.pokermind_core.cards import DECK
from packages.pokermind_core.contracts import Event, Hand, Player, Summary, TimeEvidence
from packages.pokermind_core.engine import Engine

GENERATOR = "legal-baselines-v1"


def generate_hand(seed: int, *, table_size=6, session="demo-2026") -> Hand:
    rng = random.Random(seed)
    deck = list(DECK)
    rng.shuffle(deck)
    players = [Player(seat=i + 1, stack=rng.choice([1000, 2000, 3000]), cards=[deck.pop(), deck.pop()]) for i in range(table_size)]
    start = datetime(2026, 1, 10, 18, tzinfo=timezone.utc) + timedelta(seconds=seed % 7200)
    hand = Hand.model_construct(
        source_hand_id=f"sim-{seed}", session_id=session, button=(seed % table_size) + 1,
        small_blind=10, big_blind=20, hero=1, players=players, events=[], rake=0,
        timestamps=TimeEvidence(started_at=start.isoformat(), session_end=(start + timedelta(hours=3)).isoformat(),
                                timezone="UTC", precision="second", evidence="Seeded simulator terminal record"),
        terminal_summary=Summary(awards={}, final_stacks={}, rake=0),
    )
    engine = Engine(hand)
    for _ in range(200):
        if len(engine.active) == 1 or (engine.street == 3 and not engine.pending): break
        if not engine.pending:
            event = Event(kind="deal", cards=[deck.pop() for _ in range(3 if engine.street == 0 else 1)])
        else:
            legal = engine.legal()
            draw = rng.random()
            if "fold" in legal and draw < .23:
                event = Event(kind="fold", seat=engine.actor)
            elif ("raise" in legal or "bet" in legal) and draw > .80:
                kind = "raise" if "raise" in legal else "bet"
                bounds = legal[kind]
                target = min(bounds["max_to"], max(bounds["min_to"], engine.current + max(20, engine.pot // 2)))
                event = Event(kind=kind, seat=engine.actor, to=target)
            else:
                event = Event(kind="check" if "check" in legal else "call", seat=engine.actor)
        engine.apply(event)
        hand.events.append(event)
    else:
        raise RuntimeError("Synthetic hand exceeded action budget")
    hand.terminal_summary = engine.finish()
    return Hand.model_validate(hand.model_dump())


def generate(count=48, seed=2026):
    if not 1 <= count <= 10_000: raise ValueError("Local generation accepts 1–10,000 hands")
    return [generate_hand(seed + i, session=f"synthetic-{seed}-{i // 24 + 1}") for i in range(count)]
