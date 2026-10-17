"""Shared fail-closed policy; there is no clock or eligibility override in the API."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import TimeEvidence

POLICY_VERSION = "completed-session-24h-v1"
MIN_DELAY = timedelta(hours=24)


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    status: str
    reason: str
    eligible_at: str | None = None
    policy_version: str = POLICY_VERSION


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    # A date, timezone-free datetime, or reduced-precision string is not evidence.
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("An explicit second-resolution timestamp and UTC offset are required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assess(evidence: TimeEvidence, source: str, *, declaration: str | None = None,
           now: datetime | None = None) -> Eligibility:
    now = now or utcnow()
    if source == "synthetic":
        return Eligibility(True, "eligible", "Server-generated synthetic practice data")
    if source != "real":
        return Eligibility(False, "quarantined", "Unknown provenance")
    try:
        start = parse_time(evidence.started_at) if evidence.started_at else None
        end = parse_time(evidence.session_end) if evidence.session_end else None
        if any(t and t > now for t in (start, end)) or (start and end and end < start):
            raise ValueError("Future or inconsistent session timestamps")
        if evidence.precision != "second":
            raise ValueError("Timestamp precision is insufficient")
        if not start or not evidence.timezone:
            raise ValueError("Start time and source timezone are required")
        zone = ZoneInfo(evidence.timezone)
        if any(t and t.astimezone(zone).utcoffset() != t.utcoffset() for t in (start, end)):
            raise ValueError("Timestamp offset contradicts source timezone")
        if end and not evidence.evidence:
            raise ValueError("Documented session-end evidence is required")
        if end:
            eligible_at = end + MIN_DELAY
        elif declaration:
            declared = parse_time(declaration)
            if declared > now or (start and declared < start):
                raise ValueError("Invalid completed-session declaration")
            eligible_at = declared + MIN_DELAY
        else:
            raise ValueError("Missing session end; a completed-session declaration is required")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        return Eligibility(False, "quarantined", str(exc))
    eligible = now >= eligible_at
    return Eligibility(eligible, "eligible" if eligible else "waiting",
                       "Completed-session delay satisfied" if eligible else "24-hour study delay is active",
                       eligible_at.astimezone(timezone.utc).isoformat())
