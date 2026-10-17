import subprocess
import sys
import pytest
from scripts.run_local_research import execute


def test_local_timeout_stops_owned_job(tmp_path):
    pidfile=tmp_path/'pid'
    # A real subprocess exercises the termination path without cloud access.
    code='import os,pathlib,time; pathlib.Path('+repr(str(pidfile))+').write_text(str(os.getpid())); time.sleep(60)'
    with pytest.raises(subprocess.TimeoutExpired):execute([sys.executable,'-c',code],tmp_path/'job.log',.3)
    assert pidfile.exists()
    import os
    with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)
