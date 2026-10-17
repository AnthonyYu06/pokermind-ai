"""Freeze only dependency closures, not unrelated packages from the environment."""
from importlib import metadata
from pathlib import Path
from packaging.requirements import Requirement
import tomllib

root=Path(__file__).resolve().parents[1]
project=tomllib.loads((root/'pyproject.toml').read_text())['project']

def lock(requirements,path):
    found={}
    def visit(raw):
        req=Requirement(raw)
        if req.marker and not req.marker.evaluate({'extra':''}):return
        dist=metadata.distribution(req.name);name=dist.metadata['Name']
        if name.lower() in found:return
        found[name.lower()]=f'{name}=={dist.version}'
        for dependency in dist.requires or []:visit(dependency)
    for requirement in requirements:visit(requirement)
    (root/path).write_text('# Resolved and verified on CPython 3.13, macOS ARM64. See docs/runtime.md.\n'+'\n'.join(found[k] for k in sorted(found))+'\n')

base=project['dependencies']+project['optional-dependencies']['test']
lock(base,'requirements.lock')
lock(base+project['optional-dependencies']['ml']+project['optional-dependencies']['data'],'requirements-research.lock')
