"""Persistent server-owned small-game matches; accepts actions, never states."""
import json
import random
import secrets
import uuid
from packages.pokermind_core.contracts import canonical
from packages.pokermind_core.eligibility import utcnow
from .artifacts import strategy_policy,verified_bytes

SCHEMA='''CREATE TABLE IF NOT EXISTS simulator_games(
 game_id TEXT PRIMARY KEY, game TEXT NOT NULL, hero INTEGER NOT NULL,
 seed INTEGER NOT NULL, opponent TEXT NOT NULL, policy_path TEXT NOT NULL,
 policy_hash TEXT NOT NULL, history TEXT NOT NULL, public_log TEXT NOT NULL,
 revision INTEGER NOT NULL, completed INTEGER NOT NULL, created_at TEXT NOT NULL);
'''


class SmallGames:
    def __init__(self,store):
        self.store=store
        with store.connect() as conn:conn.executescript(SCHEMA)

    def options(self):
        options=[]
        for game in ['kuhn','leduc']:
            try:
                selection,_=strategy_policy(self.store.root,game,'baseline')
                options.append({'game':game,'available':True,'baseline_metrics':selection['metrics']})
            except (ValueError,ImportError,OSError):options.append({'game':game,'available':False})
        return {'games':options,'scope':'Two-player small-game simulation only','research_label':'Unpromoted neural policy; baseline recommended'}

    def _load_policy(self,row):
        data=json.loads(verified_bytes(self.store.root,row['policy_path'],row['policy_hash']))
        return dict(zip(data['keys'],data['probabilities']))

    def _advance(self,row,state,history,log):
        # Random stream depends on persisted server seed + action count: retries
        # and process restarts do not change the chance/opponent outcomes.
        policy=self._load_policy(row)
        while not state.is_terminal() and (state.is_chance_node() or state.current_player()!=row['hero']):
            rng=random.Random(f"{row['seed']}:{len(history)}")
            if state.is_chance_node():
                choices=dict(state.chance_outcomes());action=rng.choices(list(choices),weights=list(choices.values()))[0]
            else:
                player=state.current_player();key=f'{player}|{state.information_state_string(player)}'
                probs=policy.get(key)
                if probs is None:raise ValueError('Policy does not cover this small-game state')
                legal=state.legal_actions();action=rng.choices(legal,weights=[probs[a] for a in legal])[0]
                log.append({'player':player,'action':state.action_to_string(player,action)})
            state.apply_action(action);history.append(action)
        return history,log

    def _state(self,row):
        from packages.strategy.games import load_game
        state=load_game(row['game']).new_initial_state()
        for a in json.loads(row['history']):state.apply_action(a)
        return state

    def _view(self,row,state):
        history=json.loads(row['history']);hero=row['hero'];completed=state.is_terminal()
        private_index=history[hero]
        rank='JQK'[private_index if row['game']=='kuhn' else private_index//2]
        public=getattr(state,'public_card',lambda:-1)()
        public_rank='JQK'[public//2] if public>=0 else None
        # This response intentionally excludes serialized state, RNG, policy table,
        # action values, opponent private cards and future chance outcomes.
        return {'game_id':row['game_id'],'game':row['game'],'hero':hero,'your_card':rank,'public_card':public_rank,
                'opponent':row['opponent'],'policy_hash':row['policy_hash'],'revision':row['revision'],'completed':completed,
                'legal_actions':[] if completed else [{'id':a,'label':state.action_to_string(hero,a)} for a in state.legal_actions()],
                'log':json.loads(row['public_log']),'return_chips':state.returns()[hero] if completed else None,
                'message':'Returns are game chips, not dollars. A single outcome does not measure decision quality.'}

    def start(self,game,opponent):
        selection,_=strategy_policy(self.store.root,game,opponent)
        row={'game_id':uuid.uuid4().hex,'game':game,'hero':secrets.randbelow(2),'seed':secrets.randbits(48),'opponent':opponent,
             'policy_path':selection['policy'],'policy_hash':selection['sha256'],'history':'[]','public_log':'[]','revision':0,'completed':0,'created_at':utcnow().isoformat()}
        state=self._state(row);history,log=self._advance(row,state,[],[])
        row.update(history=canonical(history),public_log=canonical(log),completed=int(state.is_terminal()))
        with self.store.connect() as conn:
            conn.execute('INSERT INTO simulator_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',tuple(row.values()))
            self.store.audit(conn,'simulator-start',row['game_id'])
        return self._view(row,state)

    def get(self,game_id):
        with self.store.connect() as conn:row=conn.execute('SELECT * FROM simulator_games WHERE game_id=?',(game_id,)).fetchone()
        if row is None:raise KeyError('Simulation not found')
        return self._view(row,self._state(row))

    def act(self,game_id,action,revision):
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            stored=conn.execute('SELECT * FROM simulator_games WHERE game_id=?',(game_id,)).fetchone()
            if stored is None:raise KeyError('Simulation not found')
            row=dict(stored);state=self._state(row)
            if row['completed']:raise ValueError('This hand is already complete')
            if revision!=row['revision']:raise ValueError('This action is stale; reload the current hand')
            if state.current_player()!=row['hero'] or action not in state.legal_actions():raise ValueError('Illegal simulator action')
            history=json.loads(row['history']);log=json.loads(row['public_log'])
            log.append({'player':row['hero'],'action':state.action_to_string(row['hero'],action)})
            state.apply_action(action);history.append(action)
            history,log=self._advance(row,state,history,log)
            row.update(history=canonical(history),public_log=canonical(log),revision=revision+1,completed=int(state.is_terminal()))
            conn.execute('UPDATE simulator_games SET history=?,public_log=?,revision=?,completed=? WHERE game_id=?',(row['history'],row['public_log'],row['revision'],row['completed'],game_id))
            self.store.audit(conn,'simulator-action',game_id)
        return self._view(row,state)
