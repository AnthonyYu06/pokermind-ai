import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from packages.pokermind_core.contracts import canonical
from packages.pokermind_core.eligibility import utcnow
from packages.simulation.generate import generate_hand
from services.api.app import create_app
from services.api.store import Store

HEADERS={'X-PokerMind-Local':'1'}

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path),headers=HEADERS) as client:yield client


def upload(client,hands,filename='hands.jsonl'):
    data='\n'.join(canonical(h) for h in hands)+'\n'
    report=client.post('/api/imports/preview',files={'file':(filename,data,'text/plain')})
    assert report.status_code==200,report.text
    return client.post('/api/imports/'+report.json()['file_id']+'/commit').json()


def test_import_overlap_crlf_bom_and_filename_independence(client):
    h1,h2=generate_hand(1),generate_hand(2)
    first=upload(client,[h1]); assert first['counts']['imported']==1
    second=upload(client,[h1,h2]); assert second['counts']['imported']==1 and second['counts']['duplicate']==1
    data=('\ufeff'+canonical(h1)+'\r\n').encode()
    preview=client.post('/api/imports/preview',files={'file':('../../escape.jsonl',data)}).json()
    assert preview['name']=='escape.jsonl'
    report=client.post('/api/imports/'+preview['file_id']+'/commit').json()
    assert report['counts']['duplicate']==1
    assert client.post('/api/imports/'+preview['file_id']+'/commit').json()==report
    assert len(client.get('/api/overview').json()['hands'])==2


def test_cancel_and_repreview(client):
    data=canonical(generate_hand(2))
    r=client.post('/api/imports/preview',files={'file':('a.jsonl',data)}).json()
    assert client.post('/api/imports/'+r['file_id']+'/cancel').status_code==200
    assert client.post('/api/imports/'+r['file_id']+'/commit').status_code==422
    r=client.post('/api/imports/preview',files={'file':('a.jsonl',data)}).json()
    assert client.post('/api/imports/'+r['file_id']+'/commit').status_code==200


def test_recent_hand_cannot_reach_any_review_path(client):
    h=generate_hand(1)
    h.timestamps.started_at=(utcnow()-timedelta(hours=2)).isoformat()
    h.timestamps.session_end=(utcnow()-timedelta(hours=1)).isoformat()
    report=upload(client,[h]);assert report['counts']['waiting']==1
    row=client.get('/api/overview').json()['hands'][0];hid=row['hand_id'];did=f'{hid}:0'
    assert row['net'] is None
    assert client.get('/api/hands/'+hid+'/replay').status_code==403
    assert client.get('/api/hands/'+hid+'/replay?retrospective=true').status_code==403
    for suffix in ('analyze','bookmark'):
        assert client.post('/api/decisions/'+did+'/'+suffix).status_code==403
    assert client.get('/api/decisions/'+did+'/analysis').status_code==403
    assert client.post('/api/hands/'+hid+'/hero',json={'seat':1}).status_code==403
    assert client.post('/api/practice').status_code==422
    result=subprocess.run([sys.executable,'-m','services.api.cli','--data',str(client.app.state.store.root),'replay',hid],capture_output=True,text=True)
    assert result.returncode==2 and 'delay' in result.stderr


def test_upload_cannot_claim_synthetic_provenance(client):
    h=generate_hand(5);h.timestamps.session_end=None
    r=upload(client,[h]); assert r['source_kind']=='real' and r['counts']['quarantined']==1
    hid=client.get('/api/overview').json()['hands'][0]['hand_id']
    declared=client.post('/api/hands/'+hid+'/declare-complete').json()
    repeated=client.post('/api/hands/'+hid+'/declare-complete').json()
    assert declared==repeated
    assert client.get('/api/hands/'+hid+'/replay').status_code==403
    raw=h.model_dump();raw['source_kind']='synthetic'
    r=upload(client,[raw]);assert r['counts']['failed']==1


def test_revision_blocks_old_decisions_and_preserves_original(client):
    h=generate_hand(9);upload(client,[h])
    hid=client.get('/api/overview').json()['hands'][0]['hand_id']
    assert client.get('/api/hands/'+hid+'/replay').status_code==200
    h.timestamps.evidence='corrected export'
    r=upload(client,[h]);assert r['counts']['quarantined']==1
    assert client.get('/api/hands/'+hid+'/replay').status_code==403
    assert len(list((client.app.state.store.root/'originals').iterdir()))==2


def test_demo_replay_drill_and_saved_progress(client):
    assert client.post('/api/demo').status_code==200
    overview=client.get('/api/overview').json()
    assert len(overview['hands'])==48 and overview['stats'][0]['source_kind']=='synthetic'
    hid=overview['hands'][0]['hand_id']
    replay=client.get('/api/hands/'+hid+'/replay').json()
    assert all(not s['cards'] for state in replay['snapshots'] for s in state['seats'] if s['seat']!=replay['hero'])
    retro=client.get('/api/hands/'+hid+'/replay?retrospective=true').json()
    assert all(len(s['cards'])==2 for s in retro['snapshots'][0]['seats'])
    did=replay['decisions'][0]['decision_id']
    assert client.post('/api/decisions/'+did+'/bookmark').status_code==200
    drill=client.post('/api/practice').json()
    assert 'events' not in drill and 'terminal_summary' not in drill
    legal=drill['observation']['state']['legal_actions']
    kind=next(k for k in legal if k in ('fold','check','call'))
    malicious=client.post('/api/practice/'+drill['attempt_id'],json={'kind':kind,'state':{'anything':'bypass'}})
    assert malicious.status_code==422
    attempt=client.post('/api/practice/'+drill['attempt_id'],json={'kind':kind,'confidence':4})
    assert attempt.status_code==200,attempt.text
    assert client.post('/api/practice/'+drill['attempt_id'],json={'kind':kind}).status_code==422
    assert client.get('/api/study').json()['progress']['completed']==1


def test_malicious_input_cross_origin_and_arbitrary_review(client):
    assert client.post('/api/demo',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/demo',headers={'X-PokerMind-Local':''}).status_code==403
    assert client.get('/api/health',headers={'Host':'evil.example'}).status_code==400
    assert client.post('/api/recommend',json={'state':'current'}).status_code==404
    assert client.post('/api/imports/preview',files={'file':('evil.zip',b'PK')}).status_code==422
    assert client.post('/api/imports/preview',files={'file':('evil.txt',b'\x00')}).status_code==422
    assert client.post('/api/imports/preview',files={'file':('big.txt',b'a'*(10*1024*1024+1))}).status_code==413
    r=client.post('/api/imports/preview',files={'file':('unknown.txt','Ignition unsupported text')}).json()
    assert r['counts']['unsupported']==1


def test_backup_roundtrip(tmp_path):
    store=Store(tmp_path/'app');store.seed_demo()
    manifest=store.backup(tmp_path/'backup')
    assert 'pokermind.sqlite3' in manifest
    restored=Store(tmp_path/'backup')
    assert restored.list_hands()==store.list_hands()
    hid=store.list_hands()[0]['hand_id']
    assert restored.replay(hid)==store.replay(hid)


def test_sessions_and_normalized_projections_respect_eligibility(client):
    old=generate_hand(11,session='historical-test')
    recent=generate_hand(12,session='recent-test')
    recent.timestamps.started_at=(utcnow()-timedelta(hours=2)).isoformat();recent.timestamps.session_end=(utcnow()-timedelta(hours=1)).isoformat()
    upload(client,[old,recent])
    sessions=client.get('/api/sessions').json();assert len(sessions)==2
    for s in sessions:
        detail=client.get('/api/sessions/'+s['session_key']).json()
        if s['session_id']=='recent-test':assert s['net']==0 and s['pending']==1 and detail['stats']==[] and detail['hands'][0]['net'] is None
        else:assert len(detail['stats'])==1 and s['eligible']==1
    store=client.app.state.store
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0]==len(old.events)+len(recent.events)
        assert conn.execute('SELECT COUNT(*) FROM retrospective_outcomes').fetchone()[0]==12
        assert conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]==5
    # Opening/migrating again preserves the exact projection, without duplicates.
    Store(store.root)
    with store.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0]==len(old.events)+len(recent.events)


def test_verified_restore_rejects_corruption_and_overwrite(tmp_path):
    from services.api.backup import restore
    store=Store(tmp_path/'app');store.seed_demo();store.backup(tmp_path/'backup')
    result=restore(tmp_path/'backup',tmp_path/'restored');assert result['status']=='restored'
    assert Store(tmp_path/'restored').list_hands()==store.list_hands()
    with pytest.raises(ValueError,match='must not exist'):restore(tmp_path/'backup',tmp_path/'restored')
    raw=next((tmp_path/'backup'/'originals').iterdir());raw.chmod(0o600);raw.write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):restore(tmp_path/'backup',tmp_path/'bad')
    assert not (tmp_path/'bad').exists()
