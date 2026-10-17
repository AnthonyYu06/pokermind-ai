import pytest
pytest.importorskip('torch',reason='Optional research dependencies are not installed')
from types import SimpleNamespace
from packages.simulation.generate import generate_hand
from packages.pokermind_core.contracts import canonical
from packages.pokermind_core.eligibility import utcnow
from pipelines.spark.local import reconstruct_partition
from datetime import timedelta

def test_worker_rechecks_eligibility():
    h=generate_hand(10)
    h.timestamps.started_at=(utcnow()-timedelta(hours=2)).isoformat()
    h.timestamps.session_end=(utcnow()-timedelta(hours=1)).isoformat()
    row=SimpleNamespace(hand_id='hand',canonical=canonical(h),source_kind='real',declaration=None)
    with pytest.raises(ValueError,match='eligibility'):list(reconstruct_partition([row]))

def test_worker_decision_prefix_and_grouped_split():
    h=generate_hand(46001,table_size=2)
    row=SimpleNamespace(hand_id='hand',canonical=canonical(h),source_kind='synthetic',declaration=None)
    rows=list(reconstruct_partition([row]))
    assert all(r[2]==r[5] for r in rows)
    assert len(set(r[-1] for r in rows))==1


def test_concurrent_pipeline_writer_is_rejected_before_spark(tmp_path):
    import fcntl
    from pipelines.spark.local import run
    with (tmp_path/'.pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='Another pipeline'):run(tmp_path,count=1)
