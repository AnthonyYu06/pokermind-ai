"""Checksummed local application backup and restore into a new directory."""
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from packages.pokermind_core.contracts import canonical


def checksum(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def backup(store,destination):
    destination=destination.resolve()
    if destination.exists():raise ValueError('Backup destination must not exist')
    if destination.is_relative_to(store.root):raise ValueError('Backup must be outside the active data directory')
    destination.mkdir(parents=True,mode=0o700)
    with store.connect() as conn:
        with sqlite3.connect(destination/'pokermind.sqlite3') as out:conn.backup(out)
    # These contain immutable report/policy/model artifacts and simulator states.
    # Spark lake / MLflow experiment storage are rebuildable research workspaces.
    for name in ['originals','models','strategy','behavior','releases','evidence']:
        source=store.root/name
        if source.exists():
            if source.is_symlink() or any(p.is_symlink() for p in source.rglob('*')):raise ValueError('Backup source contains a symlink')
            def skip_training_buffers(directory,names):
                relative=Path(directory).relative_to(store.root)
                if relative.parts[:2]==('models','holdem-nfsp'):
                    return [n for n in names if n in ('checkpoint.pt','checkpoint.tmp')]
                return []
            shutil.copytree(source,destination/name,ignore=skip_training_buffers)
    for name in ['research.json','strategy-kuhn.json','strategy-leduc.json','behavior.json','active-release.json','release-audit.jsonl','local-research.json','river-research.json','river-coach.json','holdem-nfsp.json','installed-publication.json']:
        source=store.root/name
        if source.exists():shutil.copy2(source,destination/name)
    manifest={p.relative_to(destination).as_posix():checksum(p) for p in destination.rglob('*') if p.is_file()}
    (destination/'manifest.json').write_text(canonical(manifest));return manifest


def restore(source,destination):
    source=source.resolve();destination=destination.resolve()
    if destination.exists():raise ValueError('Restore destination must not exist; active data is never overwritten')
    manifest=json.loads((source/'manifest.json').read_text())
    if not isinstance(manifest,dict) or 'pokermind.sqlite3' not in manifest:raise ValueError('Invalid backup manifest')
    for name,sha in manifest.items():
        path=(source/name).resolve()
        if not path.is_relative_to(source) or Path(name).is_absolute() or any(part=='..' for part in Path(name).parts):raise ValueError('Unsafe backup path')
        if (source/name).is_symlink() or not path.is_file() or checksum(path)!=sha:raise ValueError('Backup checksum mismatch: '+name)
    with sqlite3.connect(f'file:{source / "pokermind.sqlite3"}?mode=ro',uri=True) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Backup database integrity check failed')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_name(destination.name+'.restore-'+uuid.uuid4().hex)
    temp.mkdir(mode=0o700)
    try:
        for name in manifest:
            target=temp/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target)
        os.rename(temp,destination)
    except BaseException:
        shutil.rmtree(temp);raise
    return {'status':'restored','destination':str(destination),'verified_files':len(manifest)}
