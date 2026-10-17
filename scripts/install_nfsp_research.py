"""Copy verified inference artifacts/report to an isolated local app workspace.

Training replay buffers and checkpoints are deliberately not part of serving.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from packages.models.river_inference import checked_bytes,manifest
from packages.pokermind_core.contracts import canonical


def install(source,destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    pointer=json.loads((source/'holdem-nfsp.json').read_text())
    report_bytes=checked_bytes(source,pointer,8*1024*1024);report=json.loads(report_bytes)
    river=report['protocol']['river_pointer'];bundle=manifest(source,river)
    items=[pointer,river]+bundle['members']+[run['artifact'] for run in report['runs']]
    # Verify every source before changing the destination pointer.
    contents=[(item,checked_bytes(source,item,16*1024*1024)) for item in items]
    destination.mkdir(parents=True,exist_ok=True)
    for item,data in contents:
        target=(destination/item['path']).resolve()
        if not target.is_relative_to(destination):raise ValueError('Unsafe model installation path')
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Immutable model destination conflict')
        else:
            with target.open('xb') as out:out.write(data)
    temporary=destination/('holdem-nfsp-'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(canonical(pointer));temporary.replace(destination/'holdem-nfsp.json')
    return {'installed_files':len(contents),'selected_sha256':report['selected']['sha256'],'cloud_enabled':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=ROOT/'.data');p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args();print(json.dumps(install(a.source,a.destination),indent=2))
