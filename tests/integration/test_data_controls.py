import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from packages.pokermind_core.contracts import canonical
from packages.pokermind_core.eligibility import utcnow
from packages.simulation.generate import generate_hand
from services.api.app import create_app
from services.api.store import Store

HEADERS = {'X-PokerMind-Local': '1'}


def stage(store, seed, source='real'):
    data = (canonical(generate_hand(seed, session='paged-session')) + '\n').encode()
    report = store._stage(data, 'example.jsonl', source)
    return report, data


def test_paging_search_and_overview_keep_complete_statistics(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    data = ('\n'.join(canonical(generate_hand(i, session='paged-session')) for i in range(60))+'\n').encode()
    store.commit(store._stage(data, 'sixty.jsonl', 'synthetic')['file_id'])
    with TestClient(app, headers=HEADERS) as client:
        overview = client.get('/api/overview').json()
        assert len(overview['hands']) == 50
        assert overview['counts']['synthetic'] == {'total': 60, 'eligible': 60}
        assert overview['stats'][0]['hands'] == 60
        from statistics import mean, stdev
        from math import sqrt
        returns = []
        for summary in store.list_hands():
            _, hand = store.require_hand(summary['hand_id'])
            returns.append(summary['net'] / hand.big_blind)
        stats = overview['stats'][0]
        assert stats['bb100'] == pytest.approx(mean(returns) * 100)
        assert stats['bb100_ci95'] == pytest.approx([mean(returns)*100 + sign*1.96*stdev(returns)/sqrt(60)*100 for sign in (-1,1)])
        first = client.get('/api/hands?source=synthetic&limit=37').json()
        second = client.get('/api/hands', params={'source':'synthetic','limit':37,'cursor':first['next_cursor']}).json()
        assert len(first['items']) == 37 and len(second['items']) == 23
        assert second['next_cursor'] is None and second['total'] == 60
        ids = [h['hand_id'] for h in first['items'] + second['items']]
        assert len(set(ids)) == 60
        assert client.get('/api/hands?limit=101').status_code == 422
        assert client.get('/api/hands?cursor=-1').status_code == 422
        assert client.get('/api/hands?source=anything').status_code == 422
        assert client.get('/api/hands', params={'query':"%' OR 1=1 --"}).json()['total'] == 0
        label = first['items'][0]['label']
        assert client.get('/api/hands', params={'query':label}).json()['items'][0]['label'] == label
        session = client.get('/api/sessions').json()[0]
        page = client.get('/api/sessions/'+session['session_key']).json()
        assert len(page['hands']) == 50 and page['total'] == 60 and page['stats'][0]['hands'] == 60
        last = client.get('/api/sessions/'+session['session_key'], params={'cursor':page['next_cursor']}).json()
        assert len(last['hands']) == 10 and last['next_cursor'] is None


def test_export_and_real_deletion_cover_overlaps_and_preserve_shared_synthetic(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    synthetic, raw = stage(store, 1, 'synthetic'); store.commit(synthetic['file_id'])
    real = store.preview(raw, '../../<private>.txt'); store.commit(real['file_id'])
    other, other_raw = stage(store, 2); store.commit(other['file_id'])
    overlap = store.preview(raw + other_raw, 'overlap.jsonl'); store.commit(overlap['file_id'])
    real_hid = next(h['hand_id'] for h in store.list_hands() if h['source_kind'] == 'real')
    did = store.replay(real_hid)['decisions'][0]['decision_id']
    store.bookmark(did)
    drill = store.start_practice()
    with TestClient(app, headers=HEADERS) as client:
        exported = client.get('/api/imports/'+real['file_id']+'/original')
        assert exported.content == raw and exported.headers['x-content-sha256'] == hashlib.sha256(raw).hexdigest()
        assert '<private>' not in exported.headers['content-disposition']
        plan = client.post('/api/data/delete-preview', json={'scope':'all_real'}).json()
        assert plan['counts']['files'] == 3 and plan['counts']['hands'] == 2
        assert plan['counts']['original_files'] == 2  # shared synthetic original retained
        invalid = {k:plan[k] for k in ('scope','token')}
        assert client.post('/api/data/delete',json={**invalid,'confirmation':'NO'}).status_code == 422
        assert client.post('/api/data/delete',json={**invalid,'confirmation':'DELETE'},headers={'Origin':'https://evil.example'}).status_code == 403
        result = client.post('/api/data/delete',json={**invalid,'confirmation':'DELETE'})
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'deleted'
        assert client.get('/api/hands/'+real_hid+'/replay').status_code == 404
        assert client.get('/api/imports/'+real['file_id']+'/original').status_code == 404
        assert client.post('/api/practice/'+drill['attempt_id'], json={'kind':'fold'}).status_code == 404
        assert len(store.list_hands()) == 1 and store.list_hands()[0]['source_kind'] == 'synthetic'
        assert (store.root/'originals'/synthetic['content_hash']).read_bytes() == raw
        with store.connect() as conn:
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
            for table in ('study_queue','practice_sessions'):
                assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM import_records WHERE file_id IN (?,?)", (other['file_id'],overlap['file_id'])).fetchone()[0] == 0
        assert len(list((store.root/'originals').iterdir())) == 1


def test_stale_plan_and_expired_preview_retention(tmp_path):
    app = create_app(tmp_path); store = app.state.store
    first, _ = stage(store, 1)
    with TestClient(app, headers=HEADERS) as client:
        plan = client.post('/api/data/delete-preview',json={'scope':'all_real'}).json()
        second, _ = stage(store, 2); store.commit(second['file_id'])
        result = client.post('/api/data/delete',json={k:plan[k] for k in ('scope','token')} | {'confirmation':'DELETE'})
        assert result.status_code == 422 and 'changed' in result.json()['detail']
        store.cancel(first['file_id'])
        with store.connect() as conn:
            conn.execute('UPDATE ingest_files SET import_time=?', ((utcnow()-timedelta(days=60)).isoformat(),))
        assert client.post('/api/data/retention',json={'days':0}).status_code == 422
        assert client.post('/api/data/retention',json={'days':None}).status_code == 200
        assert client.post('/api/data/delete-preview',json={'scope':'expired_previews'}).json()['counts']['files'] == 0
        client.post('/api/data/retention',json={'days':30})
        plan = client.post('/api/data/delete-preview',json={'scope':'expired_previews'}).json()
        assert plan['counts']['files'] == 1 and plan['counts']['hands'] == 0
        assert client.post('/api/data/delete',json={k:plan[k] for k in ('scope','token')} | {'confirmation':'DELETE'}).status_code == 200
        assert len(store.list_hands()) == 1
        assert client.get('/api/imports/'+second['file_id']+'/original').status_code == 200


def test_pending_original_cleanup_recovers_after_restart(tmp_path, monkeypatch):
    from services.api.privacy import preview, delete, inventory
    store = Store(tmp_path)
    report, _ = stage(store, 3); store.commit(report['file_id'])
    plan = preview(store, 'all_real')
    unlink = Path.unlink
    def fail(path, *args, **kwargs):
        if path.name == report['content_hash']: raise PermissionError('Simulated filesystem failure')
        return unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', fail)
        result = delete(store, 'all_real', plan['token'], 'DELETE')
        assert result['status'] == 'cleanup-pending'
        assert inventory(store)['pending_file_deletions'] == 1
        assert store.list_hands() == []
    reopened = Store(tmp_path)
    assert inventory(reopened)['pending_file_deletions'] == 0
    assert not (store.root/'originals'/report['content_hash']).exists()


def test_original_export_rejects_corruption_and_symlinks(tmp_path):
    from services.api.privacy import original
    store = Store(tmp_path/'app'); report, _ = stage(store, 5)
    raw = store.root/'originals'/report['content_hash']
    raw.chmod(0o600); raw.write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'): original(store, report['file_id'])
    raw.unlink(); raw.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(ValueError,match='path'): original(store, report['file_id'])


def test_cli_data_controls_require_preview_and_never_overwrite_exports(tmp_path):
    import json
    import subprocess
    import sys
    store = Store(tmp_path/'app'); report, raw = stage(store, 17)
    def cli(*args):
        return subprocess.run([sys.executable,'-m','services.api.cli','--data',str(store.root),*args],capture_output=True,text=True)
    destination = tmp_path/'export.txt'
    assert cli('export-original', report['file_id'], str(destination)).returncode == 0
    assert destination.read_bytes() == raw
    assert cli('export-original', report['file_id'], str(destination)).returncode == 2
    assert cli('data-retention','disabled').returncode == 0
    assert cli('data-retention','-1').returncode == 2
    plan = json.loads(cli('data-delete-preview','all_real').stdout)
    assert cli('data-delete','all_real','--token',plan['token']).returncode != 0
    deleted = cli('data-delete','all_real','--token',plan['token'],'--confirm','DELETE')
    assert deleted.returncode == 0 and json.loads(deleted.stdout)['status'] == 'deleted'
    assert destination.read_bytes() == raw  # exported copies remain explicitly outside deletion
