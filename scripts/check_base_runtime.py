"""Exercise the base app/tests with optional research imports deliberately absent.

This checks dependency separation, not installation on a second operating system.
"""
import importlib.abc
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class WithoutResearch(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'numpy','torch','pyspiel','pyspark','delta','mlflow','pyarrow','sklearn'}:
            raise ModuleNotFoundError('Optional research dependency deliberately unavailable: '+fullname,name=fullname)

sys.meta_path.insert(0,WithoutResearch())
import pytest
raise SystemExit(pytest.main(['-q']))
