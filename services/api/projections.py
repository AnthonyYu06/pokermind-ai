"""Versioned normalized projections. Retrospective outcomes are kept separate."""
from packages.pokermind_core.contracts import Hand,canonical,digest
from packages.pokermind_core.engine import reconstruct
from packages.pokermind_core.eligibility import utcnow

SCHEMA='''
CREATE TABLE IF NOT EXISTS sessions(
 session_key TEXT PRIMARY KEY, source_kind TEXT NOT NULL, session_id TEXT NOT NULL,
 UNIQUE(source_kind,session_id));
CREATE TABLE IF NOT EXISTS actions(
 action_id TEXT PRIMARY KEY, hand_id TEXT NOT NULL REFERENCES hands(hand_id),
 action_index INTEGER NOT NULL, street TEXT NOT NULL, seat INTEGER, kind TEXT NOT NULL,
 raise_to INTEGER, cards TEXT NOT NULL, UNIQUE(hand_id,action_index));
CREATE INDEX IF NOT EXISTS actions_hand ON actions(hand_id,action_index);
CREATE TABLE IF NOT EXISTS retrospective_outcomes(
 hand_id TEXT NOT NULL REFERENCES hands(hand_id), seat INTEGER NOT NULL,
 initial_stack INTEGER NOT NULL, final_stack INTEGER NOT NULL, net INTEGER NOT NULL,
 award INTEGER NOT NULL, PRIMARY KEY(hand_id,seat));
'''


def project(conn,hid,hand,source):
    conn.execute('INSERT OR IGNORE INTO sessions VALUES (?,?,?)',(digest([source,hand.session_id])[:24],source,hand.session_id))
    snapshots,_=reconstruct(hand)
    for i,e in enumerate(hand.events):
        conn.execute('INSERT OR REPLACE INTO actions VALUES (?,?,?,?,?,?,?,?)',(f'{hid}:{i}',hid,i,snapshots[i]['street'],e.seat,e.kind,e.to,canonical(e.cards)))
    for player in hand.players:
        final=hand.terminal_summary.final_stacks[player.seat]
        conn.execute('INSERT OR REPLACE INTO retrospective_outcomes VALUES (?,?,?,?,?,?)',(hid,player.seat,player.stack,final,final-player.stack,hand.terminal_summary.awards[player.seat]))


def migrate(store):
    with store.connect() as conn:
        conn.executescript(SCHEMA)
        if conn.execute('SELECT 1 FROM schema_migrations WHERE version=2').fetchone():return
        for row in conn.execute("SELECT * FROM hands WHERE quality='valid'").fetchall():project(conn,row['hand_id'],Hand.model_validate_json(row['canonical']),row['source_kind'])
        conn.execute('INSERT INTO schema_migrations VALUES (2,?)',(utcnow().isoformat(),))
