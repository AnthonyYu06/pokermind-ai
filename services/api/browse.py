"""Bounded browse queries; full diagnostics stay in the local index."""
import json
from packages.pokermind_core.contracts import digest


def import_page(store,cursor=None,limit=30,query=''):
    where=' WHERE instr(lower(original_name),lower(?))>0';args=[query]
    with store.connect() as conn:
        total=conn.execute('SELECT COUNT(*) FROM ingest_files'+where,args).fetchone()[0]
        if cursor is not None:where+=' AND rowid < ?';args.append(cursor)
        rows=conn.execute("SELECT rowid AS cursor,json_remove(report,'$.diagnostics') AS report FROM ingest_files"+where+' ORDER BY rowid DESC LIMIT ?',[*args,limit+1]).fetchall()
    return {'items':[json.loads(r['report']) for r in rows[:limit]],'total':total,'next_cursor':rows[limit-1]['cursor'] if len(rows)>limit else None}


def import_detail(store,file_id,after=0,limit=100,status=None):
    with store.connect() as conn:
        row=conn.execute("SELECT json_remove(report,'$.diagnostics') FROM ingest_files WHERE file_id=?",(file_id,)).fetchone()
        if not row:raise KeyError('Import not found')
        where=' WHERE file_id=?';args=[file_id]
        if status:where+=' AND status=?';args.append(status)
        total=conn.execute('SELECT COUNT(*) FROM import_diagnostics'+where,args).fetchone()[0]
        rows=conn.execute('SELECT line,status,reason,byte_start,byte_end FROM import_diagnostics'+where+' AND line > ? ORDER BY line LIMIT ?',[*args,after,limit+1]).fetchall()
    return {**json.loads(row[0]),'diagnostics':[dict(r) for r in rows[:limit]],'diagnostic_total':total,'next_line':rows[limit-1]['line'] if len(rows)>limit else None}


def session_page(store,source='synthetic',cursor=None,limit=30,query=''):
    with store.connect() as conn:
        where=' WHERE source_kind=? AND instr(lower(session_id),lower(?))>0';args=[source,query]
        total=conn.execute('SELECT COUNT(DISTINCT session_id) FROM hands'+where,args).fetchone()[0]
        rows=conn.execute('SELECT MIN(rowid) AS cursor,session_id FROM hands'+where+' GROUP BY session_id'+(' HAVING MIN(rowid) < ?' if cursor else '')+' ORDER BY cursor DESC LIMIT ?',[*args,*([cursor] if cursor else []),limit+1]).fetchall()
        items=[]
        for row in rows[:limit]:
            session={'session_key':digest([source,row['session_id']])[:24],'session_id':row['session_id'],'source_kind':source,'hands':0,'eligible':0,'pending':0,'net':0,'hero_hands':0,'started_at':None}
            for hand in conn.execute('SELECT * FROM hands WHERE source_kind=? AND session_id=?',(source,row['session_id'])):
                h=store.hand_summary(hand);session['hands']+=1;session['eligible']+=h['eligibility']['eligible'];session['pending']+=not h['eligibility']['eligible']
                if h['net'] is not None:session['net']+=h['net'];session['hero_hands']+=1
                if h['started_at'] and (not session['started_at'] or h['started_at']<session['started_at']):session['started_at']=h['started_at']
            items.append(session)
    return {'items':items,'total':total,'next_cursor':rows[limit-1]['cursor'] if len(rows)>limit else None}
