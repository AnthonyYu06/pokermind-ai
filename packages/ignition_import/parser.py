"""Bounded canonical parser. No real Ignition format has yet been verified."""
import json
import time
from dataclasses import dataclass
from io import BytesIO

from pydantic import ValidationError

from packages.pokermind_core.contracts import Hand, digest
from packages.pokermind_core.engine import RulesError, reconstruct

PARSER_VERSION = "canonical-jsonl-v1.0"
MAX_BYTES = 10 * 1024 * 1024
MAX_RECORD_BYTES = 128 * 1024
MAX_RECORDS = 10_000


@dataclass
class Parsed:
    line: int
    byte_start: int
    byte_end: int
    status: str
    reason: str
    hand: Hand | None = None
    content_hash: str | None = None


class ImportCancelled(Exception):
    pass


def parse(data: bytes, *, check=None, progress=None):
    if len(data) > MAX_BYTES: raise ValueError("File exceeds the 10 MiB import limit")
    if b"\x00" in data: raise ValueError("Binary input is not a supported hand-history file")
    try: data.decode("utf-8-sig")
    except UnicodeDecodeError as exc: raise ValueError("Only UTF-8 / UTF-8 BOM exports are supported") from exc
    begin = time.monotonic()
    offset = 0
    records = []
    for line, raw in enumerate(BytesIO(data), 1):
        if check: check()
        if progress: progress(offset,len(data))
        start = offset
        offset += len(raw)
        if not raw.strip(): continue
        if len(records) >= MAX_RECORDS: raise ValueError("File exceeds 10,000 records")
        if time.monotonic() - begin > 20: raise ValueError("Import exceeded the 20-second parser budget")
        if len(raw) > MAX_RECORD_BYTES:
            records.append(Parsed(line, start, offset, "failed", "Record exceeds 128 KiB"))
            continue
        try:
            record = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(record, dict) or record.get("format") != "pokermind-canonical-v1":
                records.append(Parsed(line, start, offset, "unsupported", "Unverified format. Ignition parser awaits sanitized real fixtures."))
                continue
            hand = Hand.model_validate(record)
            if hand.variant != "NLHE_CASH" or hand.rules_version != "nlhe-v1":
                records.append(Parsed(line, start, offset, "unsupported", "Unsupported variant or betting rules"))
                continue
            try:
                reconstruct(hand)
                records.append(Parsed(line, start, offset, "valid", "Reconciled integer chip accounting", hand, digest(hand)))
            except RulesError as exc:
                records.append(Parsed(line, start, offset, "quarantined", str(exc), hand, digest(hand)))
        except json.JSONDecodeError:
            records.append(Parsed(line, start, offset, "unsupported" if not raw.lstrip().startswith(b"{") else "failed", "Unrecognized format or truncated JSON record"))
        except (ValidationError, RecursionError):
            records.append(Parsed(line, start, offset, "failed", "Canonical schema violation: check amounts, cards, required fields and types"))
    if check: check()
    if progress: progress(len(data),len(data))
    if not records: raise ValueError("The selected file contains no records")
    return records
