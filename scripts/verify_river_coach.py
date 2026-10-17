"""Reproducible diagnostic of model action ranking in generated toy drills."""
import argparse
import json
from pathlib import Path
from statistics import mean
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.api.coach import analyze, generate
from packages.pokermind_core.contracts import digest


def verify(root, count=256, seed=1760000):
    if not 1 <= count <= 1000: raise ValueError('Diagnostic accepts 1–1,000 drills')
    pointer = json.loads((root / 'river-coach.json').read_text())
    records = []; started = time.monotonic()
    for i in range(count):
        value = analyze(root, generate(seed + i), pointer)
        if value['neural']['status'] != 'available': raise ValueError(value['neural']['reason'])
        top = max(r['reference_ev_bb'] for r in value['actions'])
        # If multiple actions tie under the neural model, report the worst exact gap.
        gap = max(top - r['reference_ev_bb'] for r in value['actions'] if r['label'] in value['best_actions'])
        records.append({'observation_hash': value['observation_hash'],
                        'absolute_equity_error': abs(value['neural']['equity'] - value['reference']['equity']),
                        'ranking_agrees': value['reference_agrees'], 'worst_tied_choice_gap_bb': gap,
                        'seed_disagreement': value['seed_disagreement'],
                        'sensitive': value['sensitivity']['ranking_changes']})
    return {'status': 'measured', 'scope': 'Local synthetic river drills with a passive uniform opponent; exploratory diagnostic, not strategy validation.',
            'count': count, 'seed': seed, 'bundle_sha256': pointer['sha256'],
            'equity_mae': mean(r['absolute_equity_error'] for r in records),
            'ranking_agreement_fraction': mean(r['ranking_agrees'] for r in records),
            'mean_gap_bb': mean(r['worst_tied_choice_gap_bb'] for r in records),
            'max_gap_bb': max(r['worst_tied_choice_gap_bb'] for r in records),
            'seed_disagreements': sum(r['seed_disagreement'] for r in records),
            'sensitive_rankings': sum(r['sensitive'] for r in records),
            'seconds': time.monotonic() - started, 'records_sha256': digest(records),
            'cloud_enabled': False, 'records': records}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, default=ROOT / '.data')
    p.add_argument('--count', type=int, default=256); p.add_argument('--seed', type=int, default=1760000)
    p.add_argument('--output', type=Path, required=True); args = p.parse_args()
    if args.output.exists(): raise ValueError('Diagnostic output must be a new file')
    result = verify(args.data, args.count, args.seed)
    with args.output.open('x') as out: json.dump(result, out, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, indent=2))
