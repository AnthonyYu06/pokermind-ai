"""Actual Spark/Delta recovery experiment in an isolated synthetic workspace."""
import hashlib,json,os,time,uuid
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipelines.spark.local import run,ROOT
from packages.pokermind_core.contracts import canonical

root=ROOT/'.data'/'pipeline-recovery'/('run-'+uuid.uuid4().hex[:12]);root.mkdir(parents=True,exist_ok=True)
# Refuse reuse: preserve previous evidence and avoid confusing existing runs.
if (root/'lake').exists():raise SystemExit('Recovery workspace exists; choose a new isolated directory before rerunning')
started=time.perf_counter()
run(root,count=24)
previous=(root/'lake/published.json').read_bytes()
try:run(root,count=32,incremental=True,fail_after='silver')
except RuntimeError as e:
    assert 'Injected' in str(e)
else:raise AssertionError('Failure injection did not fire')
assert (root/'lake/published.json').read_bytes()==previous
run(root,count=32,incremental=True)
incremental=json.loads((root/'lake/published.json').read_text())
assert incremental['report']['extracted_hands']==8
run(root,count=32,incremental=False)
full=json.loads((root/'lake/published.json').read_text())
for key in ['output_hash','source_index_hash','unique_hands','decisions','label_rows','dataset_manifest_hash']:
    assert incremental['report'][key]==full['report'][key],key
# Quarantine one existing source hand: the next snapshot must retract its rows.
from services.api.store import Store
store=Store(root)
with store.connect() as conn:
    hid=conn.execute('SELECT hand_id FROM hands ORDER BY hand_id LIMIT 1').fetchone()[0]
    conn.execute("UPDATE hands SET quality='test-quarantine' WHERE hand_id=?",(hid,))
run(root,count=32,incremental=True)
retracted=json.loads((root/'lake/published.json').read_text())
assert retracted['report']['unique_hands']==31 and retracted['report']['removed_hands']==1
run(root,count=32,incremental=False)
rebuilt=json.loads((root/'lake/published.json').read_text())
assert rebuilt['report']['output_hash']==retracted['report']['output_hash']
report={'status':'passed','execution':'local Spark on one Mac; not multiple workers','failure_stage':'silver',
        'prior_publication_unchanged':True,'incremental_extracted_hands':8,'full_incremental_hash_match':True,
        'quarantine_retraction':True,'unique_hands_before_retraction':32,'unique_hands_after_retraction':31,
        'seconds':time.perf_counter()-started,'cost_usd':0,'incremental':incremental['report'],'full':full['report'],'retracted':retracted['report']}
(ROOT/'benchmarks/results/pipeline-recovery.json').write_text(json.dumps(report,indent=2))
print(canonical({'status':'passed','seconds':report['seconds']}),flush=True)
