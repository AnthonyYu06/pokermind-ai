"""Historical-ID-only model-dependent analysis with immutable input/cache identity."""
import json
from packages.pokermind_core.contracts import canonical,digest
from packages.pokermind_core.eligibility import utcnow
from packages.pokermind_core.ranges import weighted_equity,preset,VERSION as RANGE_VERSION
from packages.pokermind_core.rollout import compare,VERSION as EV_VERSION


def analyze(store,decision_id,kind,range_name,samples):
    row,hand,obs=store.decision(decision_id)  # always enforce eligibility, including cached reads
    version=EV_VERSION if kind=='rollout' else RANGE_VERSION
    key=digest([decision_id,obs['observation_hash'],version,range_name,samples])
    with store.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS decision_analysis_cache(
        cache_key TEXT PRIMARY KEY,decision_id TEXT NOT NULL REFERENCES decision_observations(decision_id),
        created_at TEXT NOT NULL,result TEXT NOT NULL)''')
        cached=conn.execute('SELECT result FROM decision_analysis_cache WHERE cache_key=?',(key,)).fetchone()
    if cached:return json.loads(cached[0])
    seed=int(obs['observation_hash'][:8],16)
    if kind=='rollout':result=compare(hand,row['action_index'],obs,range_name=range_name,samples=samples,seed=seed)
    else:
        state=obs['state'];active=[p for p in state['seats'] if not p['folded']];actor=next(p for p in active if p['seat']==obs['acting_player'])
        if len(active)!=2 or len(actor['cards'])!=2:result={'status':'unavailable','reason':'Weighted equity supports two active players with known actor cards','model_version':version}
        else:result=weighted_equity(actor['cards'],state['board'],preset(range_name),samples=samples,seed=seed)
    result.update(decision_id=decision_id,observation_hash=obs['observation_hash'],range_preset=range_name,range='Explicit '+range_name.replace('_',' ')+' range assumption')
    # Recheck before publishing in case a concurrent revision quarantined this hand.
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        store.decision(decision_id)
        conn.execute('INSERT OR IGNORE INTO decision_analysis_cache VALUES (?,?,?,?)',(key,decision_id,utcnow().isoformat(),canonical(result)))
    return result
