"""Model comparisons confined to server-generated, simulated river drills."""
from copy import deepcopy
import json
import random
import secrets
import uuid

from packages.features.behavior import candidates
from packages.models.river_inference import manifest, predict
from packages.pokermind_core.cards import DECK
from packages.pokermind_core.contracts import Hand, Player, Event, Summary, canonical, digest
from packages.pokermind_core.engine import Engine
from packages.pokermind_core.equity import equity
from packages.pokermind_core.eligibility import utcnow

VERSION = 'synthetic-river-coach-v1'
SCHEMA = '''CREATE TABLE IF NOT EXISTS coach_drills(
 drill_id TEXT PRIMARY KEY, hand TEXT NOT NULL, model_pointer TEXT NOT NULL,
 created_at TEXT NOT NULL, result TEXT);
'''


def generate(seed):
    rng = random.Random(seed)
    deck = list(DECK); rng.shuffle(deck)
    hand = Hand.model_construct(
        source_hand_id='coach', session_id='internal-coach', button=1,
        small_blind=10, big_blind=20, hero=1,
        players=[Player(seat=s, stack=rng.choice([1000, 2000, 4000]),
                        cards=[deck.pop(), deck.pop()]) for s in [1, 2]],
        events=[], rake=0, terminal_summary=Summary(awards={}, final_stacks={}, rake=0))
    engine = Engine(hand)
    def apply(event):
        engine.apply(event); hand.events.append(event)
    apply(Event(kind='raise', seat=1, to=rng.choice([60, 100, 200, 400])))
    apply(Event(kind='call', seat=2))
    for street in range(1, 4):
        apply(Event(kind='deal', cards=[deck.pop() for _ in range(3 if street == 1 else 1)]))
        if street < 3:
            apply(Event(kind='check', seat=2)); apply(Event(kind='check', seat=1))
    if rng.choice([False, True]):
        target = min(engine.legal()['bet']['max_to'], max(20, round(engine.pot * rng.choice([.5, 1, 2]))))
        apply(Event(kind='bet', seat=2, to=target))
    else:
        apply(Event(kind='check', seat=2))
    return Hand.model_validate(hand.model_dump())


def visible_engine(hand):
    public = deepcopy(hand)
    for player in public.players:
        if player.seat != 1: player.cards = []
    engine = Engine(public)
    for event in public.events: engine.apply(event)
    return engine


def coefficients(hand):
    engine = visible_engine(hand)
    if (len(hand.players) != 2 or hand.rake or engine.street != 3 or
            engine.actor != 1 or len(engine.cards[1]) != 2):
        raise ValueError('Coach supports its own heads-up, zero-rake river drills only')
    obs = {'acting_player': 1, 'state': engine.snapshot(1)}
    before = engine.stacks[1]
    rows = []
    for label, action in candidates(obs).items():
        sim = deepcopy(engine); sim.apply(Event(**action))
        for _ in range(3):
            if not sim.pending: break
            sim.apply(Event(kind='check' if 'check' in sim.legal() else 'call', seat=sim.actor))
        if sim.pending: raise ValueError('Coach continuation did not terminate')
        if len(sim.active) == 1:
            offset = sim.finish().final_stacks[1] - before; pot = 0
        else:
            if len(set(sim.contributions.values())) != 1:
                raise ValueError('Unbalanced heads-up pot')
            offset, pot = sim.stacks[1] - before, sim.pot
        rows.append({'label': label, 'action': action, 'offset': offset, 'pot': pot})
    return engine, rows


def best(rows, probability):
    values = [r['offset'] + probability * r['pot'] for r in rows]
    top = max(values)
    return [r['label'] for r, v in zip(rows, values) if abs(v - top) < 1e-7]


def analyze(root, hand, pointer, choice=None):
    engine, rows = coefficients(hand)
    if choice is not None and choice not in {r['label'] for r in rows}:
        raise ValueError('Choose one of this drill’s legal actions')
    reference = equity(engine.cards[1], engine.board)
    try:
        neural = predict(root, pointer, engine.cards[1], engine.board)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError):
        neural = {'status': 'unavailable', 'reason': 'The pinned model bundle failed verification; showing the exact reference only.'}
    available = neural['status'] == 'available'
    p = neural['equity'] if available else reference['equity']
    winners = best(rows, p); exact_best = best(rows, reference['equity'])
    for row in rows:
        row['reference_ev_bb'] = (row['offset'] + reference['equity'] * row['pot']) / hand.big_blind
        row['neural_ev_bb'] = (row['offset'] + p * row['pot']) / hand.big_blind if available else None
    selected = next((r for r in rows if r['label'] == choice), None)
    sensitivity = [best(rows, max(0, p - .05)), best(rows, min(1, p + .05))]
    seed_choices = [best(rows, member['equity']) for member in neural.get('members', [])]
    return {'version': VERSION, 'status': 'available', 'choice': choice,
            'observation_hash': digest(engine.snapshot(1)), 'neural': neural,
            'reference': {'equity': reference['equity'], 'combinations': reference['samples']},
            'actions': rows, 'best_actions': winners, 'reference_best_actions': exact_best,
            'ranking_source': 'neural' if available else 'exact-reference',
            'reference_agrees': bool(set(winners) & set(exact_best)),
            'seed_choices': seed_choices, 'seed_disagreement': len({tuple(v) for v in seed_choices}) > 1,
            'sensitivity': {'equity_delta': .05, 'lower_best': sensitivity[0], 'upper_best': sensitivity[1],
                            'ranking_changes': sensitivity[0] != sensitivity[1],
                            'note': 'A ±5 percentage point what-if check, not a statistical confidence interval.'},
            'chosen_reference_gap_bb': (max(r['reference_ev_bb'] for r in rows) - selected['reference_ev_bb']) if selected else None,
            'assumptions': ['One opponent with a uniform legal hand range.',
                            'After your action the opponent checks or calls, including all-ins; it never folds or raises.',
                            'Zero rake. Only the listed bet sizes are compared. Earlier contributions are sunk.'],
            'ev_convention': 'Expected change from your remaining stack now, in big blinds of simulated chips.',
            'scope': 'Generated practice drills only. Best under these assumptions; not an equilibrium strategy.'}


class RiverCoach:
    def __init__(self, store):
        self.store = store
        with store.connect() as conn: conn.executescript(SCHEMA)

    def _row(self, drill_id):
        with self.store.connect() as conn:
            row = conn.execute('SELECT * FROM coach_drills WHERE drill_id=?', (drill_id,)).fetchone()
        if row is None: raise KeyError('Practice drill not found')
        return row

    def get(self, drill_id):
        row = self._row(drill_id)
        engine, rows = coefficients(Hand.model_validate_json(row['hand']))
        return {'drill_id': drill_id, 'state': engine.snapshot(1), 'big_blind': 20,
                'source_kind': 'synthetic', 'actions': [{'label': r['label'], 'action': r['action']} for r in rows],
                'result': json.loads(row['result']) if row['result'] else None}

    def start(self):
        pointer = None
        path = self.store.root / 'river-coach.json'
        if path.exists():
            pointer = json.loads(path.read_text()); manifest(self.store.root, pointer)
        hand = generate(secrets.randbits(128)); drill_id = uuid.uuid4().hex
        with self.store.connect() as conn:
            conn.execute('INSERT INTO coach_drills VALUES (?,?,?,?,NULL)',
                         (drill_id, canonical(hand), canonical(pointer), utcnow().isoformat()))
            self.store.audit(conn, 'coach-start', drill_id)
        return self.get(drill_id)

    def compare(self, drill_id, choice):
        row = self._row(drill_id)
        if row['result']: return self.get(drill_id)
        result = analyze(self.store.root, Hand.model_validate_json(row['hand']), json.loads(row['model_pointer']), choice)
        with self.store.connect() as conn:
            conn.execute('UPDATE coach_drills SET result=? WHERE drill_id=? AND result IS NULL', (canonical(result), drill_id))
        return self.get(drill_id)
