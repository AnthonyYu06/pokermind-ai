"""Deterministic NLHE reconstruction. No platform adapter or external actuation."""
from copy import deepcopy

from .cards import rank
from .contracts import Event, Hand, Summary, digest

STREETS = ("preflop", "flop", "turn", "river")


class RulesError(ValueError):
    pass


class Engine:
    def __init__(self, hand: Hand):
        if hand.variant != "NLHE_CASH" or hand.rules_version != "nlhe-v1":
            raise RulesError("Unsupported game variant or rules")
        self.hand = hand
        self.seats = sorted(p.seat for p in hand.players)
        self.stacks = {p.seat: p.stack for p in hand.players}
        self.cards = {p.seat: list(p.cards) for p in hand.players}
        self.contributions = {s: 0 for s in self.seats}
        self.bets = {s: 0 for s in self.seats}
        self.folded = set()
        self.board = []
        self.street = 0
        self.current = hand.big_blind
        self.min_raise = hand.big_blind
        self.acted_at = {}
        self.awards = {s: 0 for s in self.seats}
        self.refunds = {s: 0 for s in self.seats}
        self.settled = False
        self.pots = []
        sb = hand.button if len(self.seats) == 2 else self.next(hand.button)
        bb = self.next(sb)
        for s, amount in ((sb, hand.small_blind), (bb, hand.big_blind)):
            self.pay(s, min(amount, self.stacks[s]))
        self.pending = {s for s in self.seats if self.stacks[s] > 0}
        self.actor = self.next_pending(bb)
        self.normalize_pending()

    def next(self, seat):
        return self.seats[(self.seats.index(seat) + 1) % len(self.seats)]

    def next_pending(self, after):
        cursor = after
        for _ in self.seats:
            cursor = self.next(cursor)
            if cursor in self.pending: return cursor
        return None

    @property
    def active(self):
        return [s for s in self.seats if s not in self.folded]

    @property
    def pot(self):
        return sum(self.contributions.values())

    def pay(self, seat, amount):
        if amount < 0 or amount > self.stacks[seat]:
            raise RulesError("Invalid chip contribution")
        self.stacks[seat] -= amount
        self.bets[seat] += amount
        self.contributions[seat] += amount

    def normalize_pending(self):
        self.pending &= {s for s in self.active if self.stacks[s] > 0}
        if len(self.active) == 1:
            self.pending.clear()
        actionable = [s for s in self.active if self.stacks[s] > 0]
        if len(actionable) == 1 and self.bets[actionable[0]] >= max(self.bets.values()):
            self.pending.clear()
        if not self.pending:
            self.actor = None
            # Return unmatched chips; folded contributions still count as matches.
            ordered = sorted(self.bets.items(), key=lambda x: x[1], reverse=True)
            seat, high = ordered[0]
            excess = high - ordered[1][1]
            if excess:
                if seat in self.folded: raise RulesError("Unmatched contribution belongs to a folded seat")
                self.bets[seat] -= excess
                self.contributions[seat] -= excess
                self.stacks[seat] += excess
                self.refunds[seat] += excess

    def legal(self):
        if self.actor is None or self.settled: return {}
        s = self.actor
        call = min(max(0, self.current - self.bets[s]), self.stacks[s])
        result = {"fold": {}} if call else {"check": {}}
        if call: result["call"] = {"amount": call}
        ceiling = self.bets[s] + self.stacks[s]
        reopened = s not in self.acted_at or self.current - self.acted_at[s] >= self.min_raise
        if ceiling > self.current and reopened and any(self.stacks[o] > 0 for o in self.active if o != s):
            result["bet" if self.current == 0 else "raise"] = {
                "min_to": min(ceiling, self.current + self.min_raise), "max_to": ceiling,
            }
        return result

    def apply(self, event: Event):
        if self.settled: raise RulesError("Hand has already settled")
        if event.kind == "deal":
            if self.pending or self.street == 3 or len(self.active) == 1 or event.seat is not None or event.to is not None:
                raise RulesError("Board dealt before betting closed or after termination")
            if len(event.cards) != (3 if self.street == 0 else 1):
                raise RulesError("Incorrect number of board cards")
            seen = self.board + [c for cs in self.cards.values() for c in cs]
            if len(set(seen + event.cards)) != len(seen) + len(event.cards):
                raise RulesError("Duplicate cards")
            self.board.extend(event.cards)
            self.street += 1
            self.bets = {s: 0 for s in self.seats}
            self.current = 0
            self.min_raise = self.hand.big_blind
            self.acted_at = {}
            self.pending = {s for s in self.active if self.stacks[s] > 0}
            self.actor = self.next_pending(self.hand.button)
            self.normalize_pending()
            return
        if event.cards: raise RulesError("Cards may only appear on a deal event")
        legal = self.legal()
        s = event.seat
        if s != self.actor or event.kind not in legal:
            raise RulesError("Action is illegal or out of turn")
        if event.kind in ("bet", "raise"):
            bounds = legal[event.kind]
            if event.to is None or not bounds["min_to"] <= event.to <= bounds["max_to"]:
                raise RulesError("Raise-to is outside legal bounds")
            increase = event.to - self.current
            self.pay(s, event.to - self.bets[s])
            if increase >= self.min_raise: self.min_raise = increase
            self.current = event.to
            self.pending = {o for o in self.active if o != s and self.stacks[o] > 0}
        else:
            if event.to is not None: raise RulesError("Only bets/raises have a raise-to amount")
            if event.kind == "fold": self.folded.add(s)
            elif event.kind == "call": self.pay(s, legal["call"]["amount"])
            self.pending.discard(s)
        self.acted_at[s] = self.current
        self.actor = self.next_pending(s)
        self.normalize_pending()

    def finish(self) -> Summary:
        if self.settled: raise RulesError("Double settlement")
        if self.pending or (len(self.active) > 1 and self.street != 3):
            raise RulesError("Truncated hand: betting or board is incomplete")
        if self.hand.rake > self.pot: raise RulesError("Rake exceeds pot")
        remaining_rake = self.hand.rake
        levels = sorted(set(v for v in self.contributions.values() if v > 0))
        previous = 0
        for level in levels:
            contributors = [s for s in self.seats if self.contributions[s] >= level]
            eligible = [s for s in contributors if s not in self.folded]
            amount = (level - previous) * len(contributors)
            previous = level
            rake_here = min(remaining_rake, amount)
            remaining_rake -= rake_here
            amount -= rake_here
            if not eligible: raise RulesError("Pot has no eligible player")
            if len(eligible) == 1:
                winners = eligible
            else:
                if any(len(self.cards[s]) != 2 for s in eligible):
                    raise RulesError("Cannot verify showdown without revealed cards")
                values = {s: rank(self.cards[s] + self.board) for s in eligible}
                winners = [s for s in eligible if values[s] == min(values.values())]
            # Odd chips clockwise from the button, including heads-up.
            order = self.seats[self.seats.index(self.hand.button) + 1:] + self.seats[:self.seats.index(self.hand.button) + 1]
            winners.sort(key=order.index)
            for i, s in enumerate(winners):
                self.awards[s] += amount // len(winners) + int(i < amount % len(winners))
            self.pots.append({"amount": amount, "rake": rake_here, "eligible": eligible, "winners": winners})
        for s in self.seats: self.stacks[s] += self.awards[s]
        self.settled = True
        if sum(self.stacks.values()) + self.hand.rake != sum(p.stack for p in self.hand.players):
            raise RulesError("Chip conservation failed")
        return Summary(awards=self.awards, final_stacks=self.stacks, rake=self.hand.rake)

    def snapshot(self, viewer=None, retrospective=False):
        return deepcopy({
            "street": STREETS[self.street], "board": self.board, "actor": self.actor,
            "pot": self.pot, "settled": self.settled, "legal_actions": self.legal(),
            "seats": [{"seat": s, "stack": self.stacks[s], "bet": self.bets[s],
                       "contributed": self.contributions[s], "folded": s in self.folded,
                       "cards": self.cards[s] if retrospective or s == viewer else [],
                       "button": s == self.hand.button, "hero": s == viewer,
                       "refund": self.refunds[s], "award": self.awards[s]} for s in self.seats],
            "pots": self.pots,
        })


def reconstruct(hand: Hand, *, retrospective=False, viewer=None):
    engine = Engine(hand)
    snapshots = [engine.snapshot(viewer, retrospective)]
    observations = []
    prefix = []
    for i, event in enumerate(hand.events):
        if event.kind != "deal":
            state = engine.snapshot(engine.actor)
            obs = {"features_version": "observation-v1", "action_index": i,
                   "acting_player": engine.actor, "state": state, "public_action_prefix": deepcopy(prefix)}
            obs["observation_hash"] = digest(obs)
            observations.append(obs)
        engine.apply(event)
        prefix.append(event.model_dump())
        snapshots.append(engine.snapshot(viewer, retrospective))
    summary = engine.finish()
    if summary != hand.terminal_summary:
        raise RulesError("Terminal summary disagrees with reconstruction; source was not overwritten")
    snapshots.append(engine.snapshot(viewer, retrospective))
    return snapshots, observations
