import hashlib
import json
import stat
import zipfile
import pytest
from services.api.store import Store
from services.api.snapshots import install
from services.api.backup import checksum


def publication(tmp_path, mutate=None):
    store = Store(tmp_path / 'app'); store.seed_demo(); backup = tmp_path / 'backup'; store.backup(backup)
    archive = tmp_path / 'snapshot.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for path in backup.rglob('*'):
            if path.is_file(): z.write(path, path.relative_to(backup))
        if mutate: mutate(z)
    spec = {'source_kind': 'synthetic', 'reference_retained': True, 'sha256': checksum(archive), 'bytes': archive.stat().st_size,
            'hands': 48, 'scores': 0, 'dataset_id': 'fixture'}
    path = tmp_path / 'publication.json'; path.write_text(json.dumps(spec))
    return path, archive, checksum(path)


def test_snapshot_install_is_verified_and_does_not_erase_study_progress(tmp_path):
    manifest, archive, sha = publication(tmp_path); destination = tmp_path / 'installed'
    assert install(manifest, archive, destination, sha)['hands'] == 48
    note = destination / 'personal-study-note'; note.write_text('retain me')
    assert install(manifest, archive, destination, sha, if_installed=True)['status'] == 'already-installed'
    assert note.read_text() == 'retain me'
    with pytest.raises(ValueError, match='never overwritten'): install(manifest, archive, destination, sha)


@pytest.mark.parametrize('attack', ['traversal', 'symlink', 'duplicate', 'size', 'manifest', 'archive', 'counts'])
def test_snapshot_rejects_corruption_and_unsafe_archives_atomically(tmp_path, attack):
    def mutate(z):
        if attack == 'traversal': z.writestr('../escape', 'unsafe')
        elif attack == 'duplicate': z.writestr('MANIFEST.JSON', '{}')
        elif attack == 'symlink':
            info = zipfile.ZipInfo('link'); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, '../outside')
    manifest, archive, sha = publication(tmp_path, mutate); kwargs = {}
    if attack == 'size': kwargs['max_unpacked_bytes'] = 1
    if attack == 'manifest': sha = '0' * 64
    if attack == 'archive': archive.write_bytes(archive.read_bytes() + b'tampered')
    if attack == 'counts':
        spec = json.loads(manifest.read_text()); spec['hands'] = 49; manifest.write_text(json.dumps(spec)); sha = checksum(manifest)
    destination = tmp_path / 'installed'
    with pytest.raises(ValueError): install(manifest, archive, destination, sha, **kwargs)
    assert not destination.exists() and not (tmp_path / 'escape').exists()
