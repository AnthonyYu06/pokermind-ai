import json
import threading
import time
import pytest
from fastapi.testclient import TestClient
from packages.pokermind_core.contracts import canonical
from packages.simulation.generate import generate_hand
from services.api.store import Store
from services.api.import_jobs import ImportJobs
from services.api.app import create_app


def payload(count=4):
    return ('\n'.join(canonical(generate_hand(9000+i,session=f'job-session-{i}')) for i in range(count))+'\n').encode()


def test_durable_queue_preview_commit_retry_and_browse(tmp_path):
    store=Store(tmp_path);jobs=ImportJobs(store)
    first=jobs.submit(payload(),'fixture.jsonl')
    assert jobs.submit(payload(),'renamed.jsonl')['job_id']==first['job_id']
    assert jobs.execute_one() and jobs.get(first['job_id'])['status']=='completed'
    job=jobs.commit(first['file_id']);assert jobs.execute_one()
    assert jobs.get(job['job_id'])['status']=='completed' and len(store.list_hands())==4
    with TestClient(create_app(tmp_path),headers={'X-PokerMind-Local':'1'}) as client:
        history=client.get('/api/import-pages?limit=1').json()
        assert history['total']==1 and 'diagnostics' not in history['items'][0]
        a=client.get('/api/imports/'+first['file_id']+'?limit=2').json()
        b=client.get('/api/imports/'+first['file_id'],params={'after':a['next_line'],'limit':2}).json()
        assert [x['line'] for x in a['diagnostics']+b['diagnostics']]==[1,2,3,4]
        assert b['next_line'] is None
        assert client.get('/api/imports/'+first['file_id']+'?status=failed').json()['diagnostic_total']==0
        page=client.get('/api/session-pages?source=real&limit=3').json()
        assert page['total']==4 and len(page['items'])==3
        last=client.get('/api/session-pages',params={'source':'real','limit':3,'cursor':page['next_cursor']}).json()
        assert len(last['items'])==1 and last['next_cursor'] is None


def test_cancelled_preview_and_interrupted_worker_are_retryable(tmp_path):
    store=Store(tmp_path);jobs=ImportJobs(store)
    job=jobs.submit(payload(),'example.jsonl');jobs.cancel(job['job_id'])
    assert not jobs.execute_one() and store.list_hands()==[]
    retry=jobs.retry(job['job_id']);assert retry['job_id']!=job['job_id']
    with store.connect() as conn:conn.execute("UPDATE import_jobs SET status='running' WHERE job_id=?",(retry['job_id'],))
    jobs.start();jobs.close()
    assert jobs.get(retry['job_id'])['status']=='interrupted'
    again=jobs.retry(retry['job_id']);jobs.stop.clear();jobs.execute_one()
    assert jobs.get(again['job_id'])['status']=='completed'


def test_active_commit_cancel_rolls_back_every_projection(tmp_path,monkeypatch):
    store=Store(tmp_path);jobs=ImportJobs(store)
    job=jobs.submit(payload(8),'fixture.jsonl');jobs.execute_one();commit=jobs.commit(job['file_id'])
    original=store.commit;entered=threading.Event()
    def paused(file_id,*,check,progress):
        def observe(n,total):
            progress(n,total)
            if n==1:
                entered.set();deadline=time.monotonic()+3
                while not (jobs.control/(commit['job_id']+'.cancel')).exists():
                    if time.monotonic()>deadline:raise AssertionError('Cancellation marker never arrived')
                    time.sleep(.001)
        return original(file_id,check=check,progress=observe)
    monkeypatch.setattr(store,'commit',paused)
    worker=threading.Thread(target=jobs.execute_one);worker.start();assert entered.wait(3)
    jobs.cancel(commit['job_id']);worker.join(3);assert not worker.is_alive()
    assert jobs.get(commit['job_id'])['status']=='cancelled'
    with store.connect() as conn:
        for table in ('hands','decision_observations','actions','retrospective_outcomes'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
        assert conn.execute('SELECT status FROM ingest_files').fetchone()[0]=='preview'
    monkeypatch.setattr(store,'commit',original)
    retry=jobs.retry(commit['job_id']);jobs.execute_one()
    assert jobs.get(retry['job_id'])['status']=='completed' and len(store.list_hands())==8


def test_preview_failure_preserves_original_and_blocks_deletion_while_active(tmp_path):
    from services.api.privacy import preview,original
    store=Store(tmp_path);jobs=ImportJobs(store)
    job=jobs.submit(b'\x00malformed','broken.txt')
    with pytest.raises(ValueError,match='Cancel active'):preview(store,'all_real')
    jobs.execute_one()
    assert jobs.get(job['job_id'])['status']=='failed'
    assert original(store,job['file_id'])[1]==b'\x00malformed'
    assert preview(store,'all_real')['counts']['files']==1


def test_api_background_upload_keeps_real_provenance_and_strict_limits(tmp_path):
    with TestClient(create_app(tmp_path),headers={'X-PokerMind-Local':'1'}) as client:
        assert client.post('/api/import-jobs',files={'file':('bad.zip',b'zip')}).status_code==422
        r=client.post('/api/import-jobs',files={'file':('../../fixture.jsonl',payload())})
        assert r.status_code==202
        job=r.json();deadline=time.monotonic()+5
        while client.get('/api/import-jobs/'+job['job_id']).json()['status'] in ('queued','running'):
            assert time.monotonic()<deadline;time.sleep(.01)
        report=client.get('/api/imports/'+job['file_id']).json()
        assert report['source_kind']=='real' and report['name']=='fixture.jsonl'
        assert client.post('/api/import-jobs/'+job['job_id']+'/retry').status_code==422
        assert client.post('/api/import-jobs/'+job['job_id']+'/cancel',headers={'Origin':'https://evil.example'}).status_code==403


def test_full_queue_does_not_save_untracked_originals(tmp_path):
    store=Store(tmp_path);jobs=ImportJobs(store)
    for n in range(1,5):jobs.submit(payload(n),'queued.jsonl')
    originals=set(tmp_path.rglob('*'))
    with pytest.raises(ValueError,match='Four imports'):jobs.submit(payload(5),'overflow.jsonl')
    assert set(tmp_path.rglob('*'))==originals
    first=jobs.list()[0];jobs.cancel(first['job_id'])
    assert not (jobs.control/(first['job_id']+'.cancel')).exists()


def test_new_analysis_uses_stored_ids_and_cannot_bypass_eligibility(tmp_path):
    from datetime import timedelta
    from packages.pokermind_core.eligibility import utcnow
    hand=generate_hand(142,table_size=2)
    with TestClient(create_app(tmp_path),headers={'X-PokerMind-Local':'1'}) as client:
        store=client.app.state.store
        file=store.preview(canonical(hand).encode(),'old.jsonl');store.commit(file['file_id'])
        hid=store.list_hands()[0]['hand_id'];did=store.replay(hid)['decisions'][0]['decision_id']
        choice={'range_preset':'pair_heavy','samples':100}
        first=client.post('/api/decisions/'+did+'/range-analysis',json=choice)
        assert first.status_code==200 and first.json()['status']=='available'
        assert client.post('/api/decisions/'+did+'/range-analysis',json=choice).json()==first.json()
        assert client.post('/api/decisions/'+did+'/rollout',json={**choice,'state':{}}).status_code==422
        assert client.post('/api/decisions/'+did+'/rollout',json=choice).status_code==200
        with store.connect() as conn:conn.execute("UPDATE hands SET quality='conflicting_revision' WHERE hand_id=?",(hid,))
        for endpoint in ('rollout','range-analysis'):
            assert client.post('/api/decisions/'+did+'/'+endpoint,json=choice).status_code==403
        plan=client.post('/api/data/delete-preview',json={'scope':'all_real'}).json()
        response=client.post('/api/data/delete',json={k:plan[k] for k in ('scope','token')} | {'confirmation':'DELETE'})
        assert response.status_code==200
        with store.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM decision_analysis_cache').fetchone()[0]==0
