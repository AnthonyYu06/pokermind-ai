"""Internal heads-up simulation against a learned descriptive population policy."""
import io
import json
import random
import secrets
import uuid
from functools import lru_cache
from packages.pokermind_core.contracts import Hand,Event,canonical
from packages.pokermind_core.engine import Engine
from packages.pokermind_core.cards import DECK
from packages.pokermind_core.eligibility import utcnow
from packages.simulation.generate import generate_hand
from .artifacts import report,verified_bytes,safe_path

SCHEMA='''CREATE TABLE IF NOT EXISTS holdem_games(
 game_id TEXT PRIMARY KEY, seed INTEGER NOT NULL, initial_hand TEXT NOT NULL,
 events TEXT NOT NULL, opponent TEXT NOT NULL, artifact TEXT NOT NULL,
 artifact_hash TEXT NOT NULL, revision INTEGER NOT NULL, completed INTEGER NOT NULL,
 created_at TEXT NOT NULL);
'''


@lru_cache(maxsize=4)
def _model(data):
    import torch
    from packages.models.behavior import BehaviorModel
    from packages.features.behavior import VERSION
    payload=torch.load(io.BytesIO(data),map_location='cpu',weights_only=True)
    if payload['features']!=VERSION:raise ValueError('Behavior feature contract mismatch')
    model=BehaviorModel(payload['state_size'],payload['width'],payload['context']);model.load_state_dict(payload['model']);model.eval()
    return model,payload['temperature']


class HoldemGames:
    def __init__(self,store):
        self.store=store
        with store.connect() as conn:conn.executescript(SCHEMA)

    def _restore(self,row):
        hand=Hand.model_validate_json(row['initial_hand']);engine=Engine(hand)
        events=[Event.model_validate(e) for e in json.loads(row['events'])]
        for e in events:engine.apply(e)
        deck=[c for c in DECK if c not in [c for p in hand.players for c in p.cards]]
        random.Random(row['seed']+1).shuffle(deck)
        for e in events:
            if e.kind=='deal':
                for c in e.cards:
                    if deck.pop()!=c:raise ValueError('Simulation chance history mismatch')
        return hand,engine,events,deck

    def _advance(self,row,engine,events,deck):
        from packages.features.behavior import candidates,encode,ACTIONS
        if row['opponent']=='selfplay':
            from packages.models.river_inference import checked_bytes
            data=checked_bytes(self.store.root,{'path':row['artifact'],'sha256':row['artifact_hash']},16*1024*1024)
        else:data=verified_bytes(self.store.root,row['artifact'],row['artifact_hash'])
        if row['opponent']=='research':model,temp=_model(data)
        elif row['opponent']=='baseline':frequency=json.loads(data)['frequency']
        for _ in range(200):
            if len(engine.active)==1 or (engine.street==3 and not engine.pending):engine.finish();return
            if engine.actor==1:return
            if not engine.pending:event=Event(kind='deal',cards=[deck.pop() for _ in range(3 if engine.street==0 else 1)])
            else:
                obs={'acting_player':engine.actor,'state':engine.snapshot(engine.actor),'public_action_prefix':[e.model_dump() for e in events]}
                choices=candidates(obs)
                if row['opponent']=='selfplay':
                    from .nfsp_policy import action_probabilities
                    policy=action_probabilities(self.store.root,data,obs)
                    rng=random.Random(f"{row['seed']}:{len(events)}")
                    selected=rng.choices(policy,weights=[p['probability'] for p in policy])[0]
                    event=Event(**selected['action']);engine.apply(event);events.append(event)
                    continue
                x,sequence,mask=encode(obs)
                if row['opponent']=='research':
                    import torch
                    with torch.inference_mode():
                        probabilities=(model(torch.tensor(x).unsqueeze(0),torch.tensor(sequence).unsqueeze(0),torch.tensor([len(sequence)]),torch.tensor(mask).unsqueeze(0))/temp).softmax(-1)[0].tolist()
                else:
                    key=engine.snapshot()['street']+'|'+''.join('1' if v else '0' for v in mask)
                    probabilities=frequency['table'].get(key,frequency['global'])
                allowed=[a for a in ACTIONS if a in choices]
                rng=random.Random(f"{row['seed']}:{len(events)}")
                label=rng.choices(allowed,weights=[probabilities[ACTIONS.index(a)] for a in allowed])[0]
                event=Event(**choices[label])
            engine.apply(event);events.append(event)
        raise ValueError('Simulator action budget exceeded')

    def _view(self,row,engine,events):
        from packages.features.behavior import candidates
        choices=candidates({'acting_player':engine.actor,'state':engine.snapshot(engine.actor)}) if engine.actor else {}
        return {'game_id':row['game_id'],'revision':row['revision'],'opponent':row['opponent'],'completed':engine.settled,
                'state':engine.snapshot(viewer=1),'events':[e.model_dump() for e in events],
                'net':engine.stacks[1]-next(p.stack for p in engine.hand.players if p.seat==1) if engine.settled else None,
                'artifact_hash':row['artifact_hash'],'choices':[{'label':k,'action':v} for k,v in choices.items()],
                'model_note':('NFSP self-play research; 50 BB, zero rake, discrete bet sizes. Strategy quality remains unproven.' if row['opponent']=='selfplay' else 'Synthetic behavior cloning predicts recorded policy actions; it is not a solver.')}

    def start(self,opponent):
        if opponent=='selfplay':
            from .nfsp_policy import research,networks
            from packages.models.river_inference import checked_bytes
            result=research(self.store.root)
            if result['status']=='not-run':raise ValueError('Run the local NFSP research workflow first')
            selection=result['selected'];artifact=selection['path'];sha=selection['sha256']
            networks(checked_bytes(self.store.root,selection,16*1024*1024))
        elif opponent=='research':
            selection=report(self.store.root,'behavior')['selected'];artifact=selection['artifact'];sha=selection['sha256']
        elif opponent=='baseline':
            import hashlib
            pointer=json.loads((self.store.root/'behavior.json').read_text());artifact=pointer['report'];sha=hashlib.sha256(safe_path(self.store.root,artifact).read_bytes()).hexdigest()
        else:raise ValueError('Unknown simulator opponent')
        seed=secrets.randbelow(2**31)
        if opponent=='selfplay':
            from packages.strategy.holdem_nfsp import passive_template
            hand=passive_template(seed)
        else:hand=generate_hand(seed,table_size=2,session='internal-play')
        row={'game_id':uuid.uuid4().hex,'seed':seed,'initial_hand':canonical(hand),'events':'[]','opponent':opponent,
             'artifact':artifact,'artifact_hash':sha,'revision':0,'completed':0,'created_at':utcnow().isoformat()}
        _,engine,events,deck=self._restore(row);self._advance(row,engine,events,deck)
        row.update(events=canonical([e.model_dump() for e in events]),completed=int(engine.settled))
        with self.store.connect() as conn:
            conn.execute('INSERT INTO holdem_games VALUES (?,?,?,?,?,?,?,?,?,?)',tuple(row.values()));self.store.audit(conn,'holdem-start',row['game_id'])
        return self._view(row,engine,events)

    def get(self,game_id):
        with self.store.connect() as conn:row=conn.execute('SELECT * FROM holdem_games WHERE game_id=?',(game_id,)).fetchone()
        if row is None:raise KeyError('Simulation not found')
        _,engine,events,_=self._restore(row)
        if row['completed']:engine.finish()
        return self._view(row,engine,events)

    def act(self,game_id,kind,to,revision):
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE');stored=conn.execute('SELECT * FROM holdem_games WHERE game_id=?',(game_id,)).fetchone()
            if stored is None:raise KeyError('Simulation not found')
            row=dict(stored)
            if row['completed']:raise ValueError('This hand is already complete')
            if revision!=row['revision']:raise ValueError('Stale action; reload the current hand')
            hand,engine,events,deck=self._restore(row)
            if engine.actor!=1:raise ValueError('Not your turn')
            if row['opponent']=='selfplay':
                from packages.features.behavior import candidates
                choices=candidates({'acting_player':1,'state':engine.snapshot(1)})
                if not any(c['kind']==kind and c.get('to')==to for c in choices.values()):
                    raise ValueError('This research game supports only its listed bet sizes')
            event=Event(kind=kind,seat=1,to=to);engine.apply(event);events.append(event)
            self._advance(row,engine,events,deck)
            row.update(events=canonical([e.model_dump() for e in events]),revision=revision+1,completed=int(engine.settled))
            conn.execute('UPDATE holdem_games SET events=?,revision=?,completed=? WHERE game_id=?',(row['events'],row['revision'],row['completed'],game_id))
            self.store.audit(conn,'holdem-action',game_id)
        return self._view(row,engine,events)

    def policy(self,game_id,revision):
        from .nfsp_policy import action_probabilities
        from packages.models.river_inference import checked_bytes
        with self.store.connect() as conn:row=conn.execute('SELECT * FROM holdem_games WHERE game_id=?',(game_id,)).fetchone()
        if row is None:raise KeyError('Simulation not found')
        if row['opponent']!='selfplay':raise ValueError('Policy inspection is available only in generated self-play games')
        if row['completed'] or revision!=row['revision']:raise ValueError('Hand complete or stale revision; reload the hand')
        _,engine,events,_=self._restore(row)
        data=checked_bytes(self.store.root,{'path':row['artifact'],'sha256':row['artifact_hash']},16*1024*1024)
        obs={'acting_player':1,'state':engine.snapshot(1),'public_action_prefix':[e.model_dump() for e in events]}
        policy=action_probabilities(self.store.root,data,obs)
        return {'revision':revision,'policy':policy,'artifact_hash':row['artifact_hash'],
                'note':'Action frequencies from the learned average policy, not win probabilities or certified optimal actions.'}

    def bot_action(self,game_id,revision):
        value=self.policy(game_id,revision)
        with self.store.connect() as conn:row=conn.execute('SELECT seed FROM holdem_games WHERE game_id=?',(game_id,)).fetchone()
        rng=random.Random(f"{row['seed']}:hero-policy:{revision}")
        action=rng.choices(value['policy'],weights=[p['probability'] for p in value['policy']])[0]['action']
        return self.act(game_id,action['kind'],action.get('to'),revision)
