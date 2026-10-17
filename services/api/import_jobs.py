"""Single local import worker with durable jobs and cooperative cancellation."""
import fcntl
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

from packages.ignition_import.parser import PARSER_VERSION, MAX_BYTES, ImportCancelled
from packages.pokermind_core.contracts import canonical, digest
from packages.pokermind_core.eligibility import POLICY_VERSION, utcnow

SCHEMA = '''
CREATE TABLE IF NOT EXISTS import_jobs(
 job_id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES ingest_files(file_id),
 phase TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, error TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_import ON import_jobs(file_id,phase)
 WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS import_diagnostics(
 file_id TEXT NOT NULL REFERENCES ingest_files(file_id), line INTEGER NOT NULL,
 status TEXT NOT NULL, reason TEXT NOT NULL, byte_start INTEGER, byte_end INTEGER,
 PRIMARY KEY(file_id,line));
'''


def initialize(store):
    with store.connect() as conn:
        conn.executescript(SCHEMA)
        if conn.execute('SELECT 1 FROM schema_migrations WHERE version=4').fetchone():return
        for row in conn.execute('SELECT file_id,report FROM ingest_files'):
            diagnostics(conn,row['file_id'],json.loads(row['report']).get('diagnostics',[]))
        for row in conn.execute('SELECT DISTINCT source_kind,session_id FROM hands'):
            conn.execute('INSERT OR IGNORE INTO sessions VALUES (?,?,?)',(digest([row[0],row[1]])[:24],row[0],row[1]))
        conn.execute('INSERT INTO schema_migrations VALUES (4,?)',(utcnow().isoformat(),))


def diagnostics(conn,file_id,items):
    conn.execute('DELETE FROM import_diagnostics WHERE file_id=?',(file_id,))
    conn.executemany('INSERT INTO import_diagnostics VALUES (?,?,?,?,?,?)',
                     [(file_id,d['line'],d['status'],d['reason'],d.get('byte_start'),d.get('byte_end')) for d in items])


class ImportJobs:
    def __init__(self,store):
        self.store=store;self.stop=threading.Event();self.wake=threading.Event();self.thread=None;self.lock=None;self.live={}
        self.control=store.root/'.import-control';self.control.mkdir(exist_ok=True,mode=0o700)

    def start(self):
        self.lock=(self.control/'worker.lock').open('a')
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close();self.lock=None;return  # another app process owns this durable queue
        with self.store.connect() as conn:
            conn.execute("UPDATE import_jobs SET status='interrupted',error='Worker stopped before completion; retry is safe' WHERE status='running'")
        self.thread=threading.Thread(target=self._loop,name='pokermind-import',daemon=True);self.thread.start()

    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=25)
        if self.lock and (not self.thread or not self.thread.is_alive()):self.lock.close()

    def get(self,job_id):
        with self.store.connect() as conn:row=conn.execute('SELECT * FROM import_jobs WHERE job_id=?',(job_id,)).fetchone()
        if not row:raise KeyError('Import job not found')
        return {**dict(row),**self.live.get(job_id,{})}

    def list(self):
        with self.store.connect() as conn:ids=[r[0] for r in conn.execute('SELECT job_id FROM import_jobs ORDER BY rowid DESC LIMIT 30')]
        return [self.get(i) for i in ids]

    def _insert(self,conn,file_id,phase):
        active=conn.execute("SELECT job_id FROM import_jobs WHERE file_id=? AND phase=? AND status IN ('queued','running')",(file_id,phase)).fetchone()
        if active:return active[0]
        if conn.execute("SELECT COUNT(*) FROM import_jobs WHERE status IN ('queued','running')").fetchone()[0]>=4:
            raise ValueError('Four imports are already queued; finish or cancel one first')
        job_id=uuid.uuid4().hex;now=utcnow().isoformat()
        conn.execute('INSERT INTO import_jobs VALUES (?,?,?,?,?,?,NULL)',(job_id,file_id,phase,'queued',now,now));return job_id

    def submit(self,data,name):
        if not 0<len(data)<=MAX_BYTES:raise ValueError('Choose a nonempty file up to 10 MiB')
        file_hash=hashlib.sha256(data).hexdigest();file_id=digest([file_hash,'real',PARSER_VERSION])
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            active=conn.execute("SELECT job_id FROM import_jobs WHERE file_id=? AND phase='preview' AND status IN ('queued','running')",(file_id,)).fetchone()
            if active:return self.get(active[0])
            if conn.execute("SELECT COUNT(*) FROM import_jobs WHERE status IN ('queued','running')").fetchone()[0]>=4:
                raise ValueError('Four imports are already queued; finish or cancel one first')
            if not conn.execute('SELECT 1 FROM ingest_files WHERE file_id=?',(file_id,)).fetchone():
                from .privacy import raw_path
                target=raw_path(self.store,file_hash)
                if not target.exists():
                    temp=target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
                    with temp.open('xb') as out:out.write(data);out.flush();os.fsync(out.fileno())
                    os.replace(temp,target);target.chmod(0o400)
                report={'file_id':file_id,'name':Path(name.replace('\\','/')).name[:120],'content_hash':file_hash,'source_kind':'real',
                        'parser_version':PARSER_VERSION,'policy_version':POLICY_VERSION,'status':'queued','counts':{},'diagnostics':[]}
                conn.execute('INSERT INTO ingest_files VALUES (?,?,?,?,?,?,?,?,?)',(file_id,file_hash,'real',report['name'],PARSER_VERSION,utcnow().isoformat(),POLICY_VERSION,'queued',canonical(report)))
            job_id=self._insert(conn,file_id,'preview')
        self.wake.set();return self.get(job_id)

    def commit(self,file_id):
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT status FROM ingest_files WHERE file_id=?',(file_id,)).fetchone()
            if not row:raise KeyError('Import not found')
            if row[0] not in ('preview','committed'):raise ValueError('Finish preview before committing')
            job_id=self._insert(conn,file_id,'commit')
        self.wake.set();return self.get(job_id)

    def cancel(self,job_id):
        job=self.get(job_id)
        if job['status'] not in ('queued','running'):return job
        # The marker is independent of SQLite's writer lock. The worker can see it
        # inside a long transaction and roll back before the cancellation write.
        (self.control/(job_id+'.cancel')).touch(mode=0o600)
        with self.store.connect() as conn:
            conn.execute("UPDATE import_jobs SET status='cancelled',updated_at=? WHERE job_id=? AND status='queued'",(utcnow().isoformat(),job_id))
            if job['phase']=='preview':
                conn.execute("UPDATE ingest_files SET status='cancelled',report=json_set(report,'$.status','cancelled') WHERE file_id=? AND status='queued'",(job['file_id'],))
        result=self.get(job_id)
        if result['status']=='cancelled':(self.control/(job_id+'.cancel')).unlink(missing_ok=True)
        self.wake.set();return result

    def retry(self,job_id):
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE');job=conn.execute('SELECT * FROM import_jobs WHERE job_id=?',(job_id,)).fetchone()
            if not job:raise KeyError('Import job not found')
            if job['status'] not in ('failed','interrupted','cancelled'):raise ValueError('Only stopped or failed jobs can be retried')
            new_id=self._insert(conn,job['file_id'],job['phase'])
        self.wake.set();return self.get(new_id)

    def execute_one(self):
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            job=conn.execute("SELECT * FROM import_jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not job:return False
            conn.execute("UPDATE import_jobs SET status='running',updated_at=? WHERE job_id=?",(utcnow().isoformat(),job['job_id']))
        job_id=job['job_id'];marker=self.control/(job_id+'.cancel')
        def check():
            if self.stop.is_set() or marker.exists():raise ImportCancelled('Import cancelled before publication')
        def progress(n,total):self.live[job_id]={'processed':n,'total':total}
        try:
            check()
            if job['phase']=='preview':
                from .privacy import original
                _,data=original(self.store,job['file_id'])
                with self.store.connect() as conn:name=conn.execute('SELECT original_name FROM ingest_files WHERE file_id=?',(job['file_id'],)).fetchone()[0]
                self.store._stage(data,name,'real',check=check,progress=progress)
            else:self.store.commit(job['file_id'],check=check,progress=progress)
            status,error='completed',None
        except ImportCancelled:
            status,error=('cancelled','Cancelled; no partial commit') if marker.exists() else ('interrupted','Worker stopped; retry is safe')
        except Exception as exc:status,error='failed',str(exc)[:500]
        with self.store.connect() as conn:
            conn.execute('UPDATE import_jobs SET status=?,error=?,updated_at=? WHERE job_id=?',(status,error,utcnow().isoformat(),job_id))
            if job['phase']=='preview' and status!='completed':
                conn.execute("UPDATE ingest_files SET status='cancelled',report=json_set(report,'$.status','cancelled') WHERE file_id=? AND status='queued'",(job['file_id'],))
        marker.unlink(missing_ok=True);self.live.pop(job_id,None)
        return True

    def _loop(self):
        try:
            while not self.stop.is_set():
                if not self.execute_one():self.wake.wait(1);self.wake.clear()
        finally:
            if self.lock:self.lock.close();self.lock=None
