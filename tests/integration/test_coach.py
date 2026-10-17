from copy import deepcopy
from itertools import combinations
import json

import pytest
from fastapi.testclient import TestClient

from packages.pokermind_core.cards import DECK
from packages.pokermind_core.contracts import Event, Hand
from packages.pokermind_core.engine import Engine
from services.api.app import create_app
from services.api.backup import restore
from services.api.coach import RiverCoach, analyze, best, coefficients, generate
from services.api.store import Store

HEADERS = {'X-PokerMind-Local': '1'}


def test_generated_states_and_actions_are_legal_and_blind_to_opponent():
    saw_call = saw_check = saw_allin = False
    for seed in range(100):
        hand = generate(seed); engine, rows = coefficients(hand)
        assert engine.actor == 1 and engine.street == 3 and engine.cards[2] == []
        labels = {r['label'] for r in rows}
        saw_call |= 'call' in labels; saw_check |= 'check' in labels
        saw_allin |= any(r['action'].get('to') == engine.bets[1] + engine.stacks[1] for r in rows)
        other = deepcopy(hand); other.players[1].cards = ['As', 'Ah']
        assert coefficients(other)[1] == rows
        for row in rows:
            sim = deepcopy(engine); sim.apply(Event(**row['action']))
    assert saw_call and saw_check and saw_allin


@pytest.mark.parametrize('seed', [1, 4, 12])
def test_exact_action_values_match_full_enumeration_including_refunds(seed, tmp_path):
    hand = generate(seed); engine, rows = coefficients(hand)
    result = analyze(tmp_path, hand, None)
    remaining = [c for c in DECK if c not in engine.cards[1] + engine.board]
    # Independent oracle: complete every legal opponent hand in the rules engine.
    for candidate in rows:
        total = 0
        for cards in combinations(remaining, 2):
            varied = deepcopy(hand); varied.players[1].cards = list(cards)
            sim = Engine(varied)
            for event in varied.events: sim.apply(event)
            before = sim.stacks[1]; sim.apply(Event(**candidate['action']))
            while sim.pending:
                sim.apply(Event(kind='check' if 'check' in sim.legal() else 'call', seat=sim.actor))
            total += sim.finish().final_stacks[1] - before
        expected = total / 990 / hand.big_blind
        actual = next(r['reference_ev_bb'] for r in result['actions'] if r['label'] == candidate['label'])
        assert actual == pytest.approx(expected)


def test_tied_values_and_missing_model_never_claim_neural(tmp_path):
    rows = [{'label': 'a', 'offset': -10, 'pot': 40}, {'label': 'b', 'offset': -20, 'pot': 60}]
    assert best(rows, .5) == ['a', 'b']
    result = analyze(tmp_path, generate(8), None)
    assert result['ranking_source'] == 'exact-reference'
    assert result['neural']['status'] == 'unavailable'
    assert all(r['neural_ev_bb'] is None for r in result['actions'])
    assert result['choice'] is None and result['chosen_reference_gap_bb'] is None


def test_api_confines_input_to_generated_drills_persists_choice_and_backs_up(tmp_path):
    root = tmp_path / 'app'
    with TestClient(create_app(root)) as client:
        assert client.post('/api/coach', json={}).status_code == 403
        assert client.post('/api/coach', json={'cards': ['As', 'Kh']}, headers=HEADERS).status_code == 422
        assert client.post('/api/coach', json={'seed': 123}, headers=HEADERS).status_code == 422
        drill = client.post('/api/coach', json={}, headers=HEADERS).json()
        assert drill['source_kind'] == 'synthetic' and drill['result'] is None
        assert drill['state']['seats'][1]['cards'] == []
        path = '/api/coach/' + drill['drill_id']
        assert client.post(path + '/analyze', json={'board': []}, headers=HEADERS).status_code == 422
        choice = drill['actions'][0]['label']
        value = client.post(path + '/analyze', json={'action': choice}, headers=HEADERS).json()
        assert value['result']['choice'] == choice
        assert value['result']['chosen_reference_gap_bb'] >= 0
        assert client.post(path + '/analyze', json={}, headers=HEADERS).json() == value
        assert client.get(path).json() == value
        assert client.post('/api/coach/nonexistent/analyze', json={}, headers=HEADERS).status_code == 404
    Store(root).backup(tmp_path / 'backup'); restore(tmp_path / 'backup', tmp_path / 'restored')
    assert RiverCoach(Store(tmp_path / 'restored')).get(drill['drill_id']) == value


def test_impossible_choice_and_bad_bundle_are_clear(tmp_path):
    hand = generate(2)
    with pytest.raises(ValueError, match='legal actions'):
        analyze(tmp_path, hand, None, 'invented')
    result = analyze(tmp_path, hand, {'path': 'missing.json', 'sha256': '0'*64})
    assert result['neural']['status'] == 'unavailable'
    assert result['ranking_source'] == 'exact-reference'
