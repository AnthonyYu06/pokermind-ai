"""Build an isolated batch wheel without replacing managed Spark/ML libraries."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for package, version in {'setuptools': '84.0.0', 'wheel': '0.46.3'}.items():
    try:
        installed = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        installed = None
    if installed != version:
        raise SystemExit(f'Install the pinned build tool first: python -m pip install {package}=={version}')
target = ROOT / '.runtime/cloud-wheel-build'
if target.exists(): shutil.rmtree(target)
target.mkdir(parents=True)
sources = {}
for directory in ['packages', 'pipelines', 'training', 'services', 'evaluation']:
    for path in (ROOT / directory).rglob('*.py'):
        relative = path.relative_to(ROOT); destination = target / relative; destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination); sources[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
sources['scripts/build_cloud_wheel.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
identity = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()[:12]
(target / 'pyproject.toml').write_text('''[build-system]
requires = ["setuptools==84.0.0", "wheel==0.46.3"]
build-backend = "setuptools.build_meta"
[project]
name = "pokermind-batch"
version = "0.1.0+''' + identity + '''"
requires-python = ">=3.12,<3.14"
dependencies = ["treys==0.1.8", "pydantic>=2.9,<3"]
[project.scripts]
batch = "pipelines.cloud.job:main"
[tool.setuptools.packages.find]
include = ["packages*", "pipelines*", "training*", "services*", "evaluation*"]
''')
destination = ROOT / 'dist/cloud'; destination.mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--wheel-dir', str(destination), str(target)],
               check=True, env={**os.environ, 'SOURCE_DATE_EPOCH': '1792108800'})
wheels = list(destination.glob('*' + identity + '*.whl'))
assert len(wheels) == 1
result = {'wheel': str(wheels[0].relative_to(ROOT)), 'sha256': hashlib.sha256(wheels[0].read_bytes()).hexdigest(), 'python': sys.version.split()[0], 'source_sha256': sources,
          'managed_libraries': 'Spark, Delta, Torch, MLflow, NumPy, PyArrow and sklearn are supplied by DBR ML, not installed by this wheel.'}
(destination / 'build-manifest.json').write_text(json.dumps(result, indent=2)); print(json.dumps({k:v for k,v in result.items() if k != 'source_sha256'}, indent=2))
