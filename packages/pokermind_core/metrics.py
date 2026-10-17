"""Streaming descriptive hero statistics with explicit opportunity denominators."""
from collections import defaultdict
from math import sqrt


def summarize(hands):
    groups = {}
    for row, hand in hands:
        hero = row['hero']
        if hero is None:
            continue
        key = (row['source_kind'], hand.currency, hand.scale)
        g = groups.setdefault(key, {'hands': 0, 'net': 0, 'vpip': 0, 'pfr': 0,
                                   'three_bets': 0, 'three_opportunities': 0, 'mean': 0., 'm2': 0.,
                                   'positions': defaultdict(lambda: {'hands': 0, 'net': 0})})
        result = hand.terminal_summary.final_stacks[hero] - next(p.stack for p in hand.players if p.seat == hero)
        g['hands'] += 1
        g['net'] += result
        # Welford accumulation bounds memory without subtracting large squared sums.
        value = result / hand.big_blind
        delta = value - g['mean']
        g['mean'] += delta / g['hands']
        g['m2'] += delta * (value - g['mean'])
        events = []
        for event in hand.events:
            if event.kind == 'deal':
                break
            events.append(event)
        g['vpip'] += any(e.seat == hero and e.kind in ('call', 'raise', 'bet') for e in events)
        g['pfr'] += any(e.seat == hero and e.kind in ('raise', 'bet') for e in events)
        from .engine import reconstruct
        _, observations = reconstruct(hand)
        by_index = {o['action_index']: o for o in observations}
        raises = 0
        counted = False
        for i, event in enumerate(events):
            if event.seat == hero and raises == 1 and not counted and 'raise' in by_index[i]['state']['legal_actions']:
                g['three_opportunities'] += 1
                g['three_bets'] += event.kind == 'raise'
                counted = True
            raises += event.kind in ('bet', 'raise')
        seats = sorted(p.seat for p in hand.players)
        offset = (seats.index(hero) - seats.index(hand.button)) % len(seats)
        label = ('BTN/SB' if len(seats) == 2 else 'BTN') if offset == 0 else ('BB' if len(seats) == 2 else 'SB' if offset == 1 else 'BB' if offset == 2 else f'UTG+{offset-3}')
        g['positions'][label]['hands'] += 1
        g['positions'][label]['net'] += result
    results = []
    for (source, currency, scale), g in groups.items():
        n = g['hands']
        width = 1.96 * sqrt(max(0., g['m2']) / (n - 1) / n) * 100 if n >= 30 else None
        results.append({'source_kind': source, 'currency': currency, 'scale': scale, 'hands': n, 'net': g['net'],
                        'bb100': g['mean'] * 100,
                        'bb100_ci95': None if width is None else [g['mean']*100 - width, g['mean']*100 + width],
                        'uncertainty_note': 'Exploratory normal interval treats hands as independent; session correlation is not accounted for. Small samples do not establish leaks.',
                        'vpip': {'numerator': g['vpip'], 'denominator': n},
                        'pfr': {'numerator': g['pfr'], 'denominator': n},
                        'three_bet': {'numerator': g['three_bets'], 'denominator': g['three_opportunities']},
                        'positions': dict(g['positions'])})
    return results
