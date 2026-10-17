from datetime import datetime,timedelta,timezone
import pytest
from packages.pokermind_core.contracts import TimeEvidence
from packages.pokermind_core.eligibility import assess

NOW=datetime(2026,10,15,12,tzinfo=timezone.utc)
def evidence(**changes):
    return TimeEvidence(**({'started_at':'2026-10-13T08:00:00+00:00','session_end':'2026-10-14T12:00:00+00:00','timezone':'UTC','precision':'second','evidence':'Explicit session-end marker'}|changes))

def test_exact_boundary():
    assert assess(evidence(),'real',now=NOW).eligible
    assert not assess(evidence(),'real',now=NOW-timedelta(microseconds=1)).eligible

@pytest.mark.parametrize('changes',[
    {'session_end':None},{'started_at':None},{'timezone':None},{'precision':'date'},
    {'session_end':'2026-10-16T12:00:00+00:00'},{'session_end':'2026-10-01T12:00:00+00:00'},
    {'session_end':'2026-10-13'},{'session_end':'2026-10-13T12:00:00'},{'evidence':None},
    {'timezone':'America/New_York'}, {'started_at':'2026-10-13T08:00+00:00'},
])
def test_fail_closed(changes):
    assert not assess(evidence(**changes),'real',now=NOW).eligible

def test_declaration_starts_24_hour_clock_and_cannot_fix_bad_dates():
    missing=evidence(session_end=None)
    assert not assess(missing,'real',declaration=NOW.isoformat(),now=NOW).eligible
    assert assess(missing,'real',declaration=NOW.isoformat(),now=NOW+timedelta(hours=24)).eligible
    assert not assess(evidence(session_end=None,precision='date'),'real',declaration=NOW.isoformat(),now=NOW+timedelta(days=2)).eligible

def test_unknown_provenance_denied():
    assert not assess(evidence(),'unknown',now=NOW).eligible
