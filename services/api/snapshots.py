"""Install a digest-pinned published backup locally, without cloud credentials."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import zipfile
from .backup import checksum, restore


def install(publication, archive, destination, expected_sha256, *, max_archive_bytes=1 << 30,
            max_unpacked_bytes=4 << 30, if_installed=False):
    publication, archive, destination = Path(publication), Path(archive), Path(destination).absolute()
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256) or publication.stat().st_size > 1 << 20:
        raise ValueError('A reviewed publication SHA256 and bounded manifest are required')
    payload = publication.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError('Publication checksum mismatch')
    spec = json.loads(payload)
    if spec.get('source_kind') != 'synthetic' or spec.get('reference_retained') is not True:
        raise ValueError('Only synthetic publications retaining the exact reference are supported')
    if not re.fullmatch(r'[0-9a-f]{64}', spec.get('sha256', '')):
        raise ValueError('Invalid snapshot digest')
    for field in ('hands', 'scores', 'bytes'):
        if type(spec.get(field)) is not int or spec[field] < 0:
            raise ValueError('Invalid publication counts')
    identity = {'publication_sha256': expected_sha256, 'snapshot_sha256': spec['sha256'], 'dataset_id': spec['dataset_id']}
    if destination.exists() or destination.is_symlink():
        marker = destination / 'installed-publication.json'
        if if_installed and marker.is_file() and json.loads(marker.read_text()) == identity and (destination / 'pokermind.sqlite3').is_file():
            return {'status': 'already-installed', **identity, 'destination': str(destination)}
        raise ValueError('Snapshot destination must be new; existing study data is never overwritten')
    if archive.stat().st_size != spec['bytes'] or not 0 < spec['bytes'] <= max_archive_bytes or checksum(archive) != spec['sha256']:
        raise ValueError('Snapshot checksum or size mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # An exclusive lock serializes installation into the same destination.
    lock = destination.with_name(destination.name + '.install-lock')
    with lock.open('x'):
        try:
            with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=destination.parent) as folder:
                folder = Path(folder); source = folder / 'backup'; source.mkdir(mode=0o700)
                with zipfile.ZipFile(archive) as z:
                    entries = z.infolist(); names = set(); total = 0
                    if len(entries) > 100_000:
                        raise ValueError('Snapshot has too many files')
                    for entry in entries:
                        name = PurePosixPath(entry.filename)
                        mode = entry.external_attr >> 16
                        normalized = str(name).casefold()
                        if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename or ':' in entry.filename
                                or normalized in names or entry.flag_bits & 1
                                or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                            raise ValueError('Unsafe or duplicate snapshot path')
                        names.add(normalized); total += entry.file_size
                        if total > max_unpacked_bytes:
                            raise ValueError('Snapshot exceeds unpacked size limit')
                        target = source.joinpath(*name.parts)
                        if entry.is_dir(): target.mkdir(parents=True, exist_ok=True); continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(entry) as incoming, target.open('xb') as output:
                            copied = 0
                            while block := incoming.read(1 << 20):
                                copied += len(block)
                                if copied > entry.file_size: raise ValueError('Snapshot file size mismatch')
                                output.write(block)
                staged = folder / 'restored'; result = restore(source, staged)
                with sqlite3.connect(f'file:{staged / "pokermind.sqlite3"}?mode=ro', uri=True) as conn:
                    hands = conn.execute('SELECT COUNT(*) FROM hands').fetchone()[0]
                    real = conn.execute("SELECT COUNT(*) FROM hands WHERE source_kind != 'synthetic'").fetchone()[0]
                    scores = conn.execute('SELECT COUNT(*) FROM analysis_results').fetchone()[0]
                if real or (hands, scores) != (spec['hands'], spec['scores']):
                    raise ValueError('Snapshot contents do not match publication counts/provenance')
                (staged / 'installed-publication.json').write_text(json.dumps(identity, sort_keys=True))
                if destination.exists(): raise ValueError('Snapshot destination was created during installation')
                os.rename(staged, destination)
                return {**result, **identity, 'destination': str(destination), 'hands': hands, 'scores': scores}
        finally:
            lock.unlink()


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--publication', type=Path, required=True); p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True); p.add_argument('--publication-sha256', required=True)
    p.add_argument('--if-installed', action='store_true')
    a = p.parse_args()
    try: print(json.dumps(install(a.publication, a.archive, a.destination, a.publication_sha256, if_installed=a.if_installed), indent=2))
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, sqlite3.DatabaseError) as exc: p.exit(2, f'PokerMind snapshot: {exc}\n')


if __name__ == '__main__': main()
