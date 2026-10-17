"""Canonical v1 contracts. Currency values are integer minor units throughout."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Chips = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
Seat = Annotated[int, Field(strict=True, ge=1, le=9)]
Card = Annotated[str, Field(pattern=r"^[2-9TJQKA][cdhs]$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeEvidence(Contract):
    started_at: str | None = None
    session_end: str | None = None
    timezone: str | None = None
    precision: Literal["second", "minute", "date", "unknown"] = "unknown"
    evidence: str | None = Field(default=None, max_length=256)


class Player(Contract):
    seat: Seat
    stack: Chips
    cards: list[Card] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_cards(self):
        if len(self.cards) not in (0, 2) or self.stack == 0:
            raise ValueError("A seat needs a positive starting stack and zero or two known cards")
        return self


class Event(Contract):
    kind: Literal["fold", "check", "call", "bet", "raise", "deal"]
    seat: Seat | None = None
    to: Chips | None = None
    cards: list[Card] = Field(default_factory=list, max_length=3)


class Summary(Contract):
    awards: dict[int, Chips]
    final_stacks: dict[int, Chips]
    rake: Chips


class Hand(Contract):
    schema_version: Literal[1] = 1
    format: Literal["pokermind-canonical-v1"] = "pokermind-canonical-v1"
    source_hand_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=100)
    variant: str = "NLHE_CASH"
    rules_version: str = "nlhe-v1"
    timestamps: TimeEvidence = Field(default_factory=TimeEvidence)
    currency: Literal["USD"] = "USD"
    scale: Literal[100] = 100
    button: Seat
    small_blind: Chips
    big_blind: Chips
    hero: Seat | None = None
    players: list[Player] = Field(min_length=2, max_length=9)
    events: list[Event] = Field(min_length=1, max_length=500)
    rake: Chips = 0
    terminal_summary: Summary

    @model_validator(mode="after")
    def structure(self):
        seats = [p.seat for p in self.players]
        if len(set(seats)) != len(seats) or self.button not in seats:
            raise ValueError("Seats must be unique and include the button")
        if self.hero is not None and self.hero not in seats:
            raise ValueError("Hero must be a seated player; never inferred")
        if not 0 < self.small_blind < self.big_blind:
            raise ValueError("Invalid blinds")
        cards = [c for p in self.players for c in p.cards]
        cards += [c for e in self.events for c in e.cards]
        if len(cards) != len(set(cards)):
            raise ValueError("Duplicate cards")
        return self


def canonical(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
