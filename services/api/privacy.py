"""Local data lifecycle controls. Deletion is explicit, previewed and retryable."""
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from packages.pokermind_core.contracts import canonical, digest
from packages.pokermind_core.eligibility import utcnow

SCHEMA = '''
CREATE TABLE IF NOT EXISTS data_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending_file_deletions(content_hash TEXT PRIMARY KEY);
'''
LIMITS = [
    'Deletion covers the selected local originals, import records, hands, decisions, scores and study attempts.',
    'Copies you downloaded or backed up remain under your control. This cannot erase backups, S3 versions, Delta history, or learned model weights.',
    'Current research pipelines accept synthetic data only. If you previously moved real data into another system, remove it there and retrain affected models separately.',
    'SQLite logical deletion and secure_delete do not guarantee forensic erasure from SSDs, snapshots or operating-system backups.',
]


def initialize(store):
    with store.connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute('INSERT OR IGNORE INTO data_settings VALUES (?,?)', ('preview_retention_days', '30'))
        conn.execute('INSERT OR IGNORE INTO schema_migrations VALUES (3,?)', (utcnow().isoformat(),))
    cleanup(store)


def raw_path(store, file_hash):
    if len(file_hash) != 64 or any(c not in '0123456789abcdef' for c in file_hash):
        raise ValueError('Invalid original content hash')
    directory = store.root / 'originals'
    target = directory / file_hash
    if directory.is_symlink() or target.is_symlink() or target.resolve().parent != directory:
        raise ValueError('Invalid original file path')
    return target


def cleanup(store):
    # Serialize against import staging so a reused original cannot be removed
    # between the reference check and unlink. Failures stay durable for retry.
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for row in conn.execute('SELECT content_hash FROM pending_file_deletions').fetchall():
            key = row[0]
            if conn.execute('SELECT 1 FROM ingest_files WHERE content_hash=?', (key,)).fetchone():
                conn.execute('DELETE FROM pending_file_deletions WHERE content_hash=?', (key,))
                continue
            try:
                raw_path(store, key).unlink(missing_ok=True)
            except OSError:
                continue
            conn.execute('DELETE FROM pending_file_deletions WHERE content_hash=?', (key,))
        return conn.execute('SELECT COUNT(*) FROM pending_file_deletions').fetchone()[0]


def inventory(store):
    with store.connect() as conn:
        days = json.loads(conn.execute("SELECT value FROM data_settings WHERE key='preview_retention_days'").fetchone()[0])
        counts = {r['source_kind']: r['n'] for r in conn.execute('SELECT source_kind,COUNT(*) AS n FROM ingest_files GROUP BY source_kind')}
        files = [dict(r) for r in conn.execute('SELECT file_id,original_name,source_kind,status,import_time FROM ingest_files ORDER BY import_time DESC LIMIT 100')]
        pending = conn.execute('SELECT COUNT(*) FROM pending_file_deletions').fetchone()[0]
    return {'preview_retention_days': days, 'counts': counts, 'files': files, 'file_list_limit': 100,
            'pending_file_deletions': pending, 'limitations': LIMITS,
            'retention_note': 'Applies only to uncommitted previews and cancelled imports. Cleanup always requires your preview and confirmation. Committed histories are kept until explicitly deleted.'}


def configure(store, days):
    if days is not None and (type(days) is not int or not 1 <= days <= 3650):
        raise ValueError('Retention must be 1–3650 days, or disabled')
    with store.connect() as conn:
        conn.execute("UPDATE data_settings SET value=? WHERE key='preview_retention_days'", (canonical(days),))
        store.audit(conn, 'set-preview-retention', 'local')
    return inventory(store)


def selection(conn, scope):
    if scope not in ('all_real', 'expired_previews'):
        raise ValueError('Unknown deletion scope')
    if scope == 'all_real':
        files = conn.execute("SELECT file_id,content_hash,status FROM ingest_files WHERE source_kind='real' ORDER BY file_id").fetchall()
    else:
        days = json.loads(conn.execute("SELECT value FROM data_settings WHERE key='preview_retention_days'").fetchone()[0])
        if days is None:
            files = []
        else:
            cutoff = (utcnow() - timedelta(days=days)).isoformat()
            files = conn.execute("SELECT file_id,content_hash,status FROM ingest_files WHERE status IN ('preview','cancelled') AND import_time < ? ORDER BY file_id", (cutoff,)).fetchall()
    conn.execute('CREATE TEMP TABLE IF NOT EXISTS purge_files(file_id TEXT PRIMARY KEY)')
    conn.execute('DELETE FROM purge_files')
    conn.executemany('INSERT INTO purge_files VALUES (?)', [(r['file_id'],) for r in files])
    hands = 'SELECT hand_id FROM hands WHERE source_file_id IN (SELECT file_id FROM purge_files)'
    decisions = f'SELECT decision_id FROM decision_observations WHERE hand_id IN ({hands})'
    if conn.execute("SELECT 1 FROM import_jobs WHERE file_id IN (SELECT file_id FROM purge_files) AND status IN ('queued','running')").fetchone():
        raise ValueError('Cancel active import jobs before previewing deletion')
    counts = {'files': len(files)}
    for table, clause in [('hands', f'hand_id IN ({hands})'), ('decision_observations', f'decision_id IN ({decisions})'),
                          ('analysis_results', f'decision_id IN ({decisions})'), ('study_queue', f'decision_id IN ({decisions})'),
                          ('practice_sessions', f'decision_id IN ({decisions})'), ('import_records', 'file_id IN (SELECT file_id FROM purge_files)')]:
        counts[table] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE {clause}').fetchone()[0]
    # Reference-count originals; uploaded copies of a generated file can share bytes.
    removable = [r[0] for r in conn.execute('SELECT DISTINCT content_hash FROM ingest_files WHERE file_id IN (SELECT file_id FROM purge_files) AND content_hash NOT IN (SELECT content_hash FROM ingest_files WHERE file_id NOT IN (SELECT file_id FROM purge_files))')]
    counts['original_files'] = len(removable)
    plan = {'scope': scope, 'counts': counts,
            'token': digest({'scope': scope, 'files': [dict(r) for r in files], 'counts': counts}),
            'limitations': LIMITS, 'confirmation': 'DELETE'}
    return plan, hands, decisions, removable


def preview(store, scope):
    with store.connect() as conn:
        return selection(conn, scope)[0]


def delete(store, scope, token, confirmation):
    if confirmation != 'DELETE':
        raise ValueError('Type DELETE to confirm the previewed deletion')
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        plan, hands, decisions, removable = selection(conn, scope)
        if plan['token'] != token:
            raise ValueError('Data changed after the preview. Review a fresh deletion preview.')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='decision_analysis_cache'").fetchone():
            conn.execute(f'DELETE FROM decision_analysis_cache WHERE decision_id IN ({decisions})')
        for table in ('analysis_results', 'study_queue', 'practice_sessions'):
            conn.execute(f'DELETE FROM {table} WHERE decision_id IN ({decisions})')
        conn.execute(f'DELETE FROM audit WHERE record_id IN ({decisions}) OR record_id IN ({hands}) OR record_id IN (SELECT file_id FROM purge_files)')
        conn.execute(f'DELETE FROM decision_observations WHERE hand_id IN ({hands})')
        for table in ('actions', 'retrospective_outcomes'):
            conn.execute(f'DELETE FROM {table} WHERE hand_id IN ({hands})')
        conn.execute(f'DELETE FROM hands WHERE hand_id IN ({hands})')
        conn.execute('DELETE FROM sessions WHERE NOT EXISTS (SELECT 1 FROM hands h WHERE h.source_kind=sessions.source_kind AND h.session_id=sessions.session_id)')
        for table in ('import_jobs','import_diagnostics','import_records'):
            conn.execute(f'DELETE FROM {table} WHERE file_id IN (SELECT file_id FROM purge_files)')
        conn.execute('DELETE FROM ingest_files WHERE file_id IN (SELECT file_id FROM purge_files)')
        conn.executemany('INSERT OR IGNORE INTO pending_file_deletions VALUES (?)', [(key,) for key in removable])
        store.audit(conn, 'data-deletion', plan['token'])
    pending = cleanup(store)
    with store.connect() as conn:
        checkpoint = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    return {'status': 'cleanup-pending' if pending else 'deleted', 'counts': plan['counts'],
            'pending_file_deletions': pending, 'sqlite_checkpoint_busy': bool(checkpoint[0]), 'limitations': LIMITS}


def original(store, file_id):
    with store.connect() as conn:
        row = conn.execute('SELECT content_hash FROM ingest_files WHERE file_id=?', (file_id,)).fetchone()
    if not row:
        raise KeyError('Import not found')
    try:
        content = raw_path(store, row[0]).read_bytes()
    except FileNotFoundError as exc:
        raise KeyError('Original file is missing; restore it from a verified backup') from exc
    if hashlib.sha256(content).hexdigest() != row[0]:
        raise ValueError('Original checksum mismatch; export refused')
    return row[0], content
