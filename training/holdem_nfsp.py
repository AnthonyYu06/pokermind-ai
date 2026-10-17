"""Local NFSP experiment with frozen protocol, small-game control and held-out arena."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
from statistics import mean, stdev
import time
import uuid

import torch
from packages.features.behavior import ACTIONS, candidates
from packages.models.river_inference import checked_bytes, manifest
from packages.pokermind_core.contracts import canonical, digest
from packages.strategy.holdem_nfsp import DIM, FEATURE_VERSION, Features, HoldemEnv
from packages.strategy.nfsp import Agent, VERSION, train_episode
from packages.simulation.behavior_data import probabilities

ROOT = Path(__file__).resolve().parents[1]


class KuhnEnv:
    def __init__(self, seed):
        import pyspiel
        self.state = pyspiel.load_game('kuhn_poker').new_initial_state()
        self.rng = random.Random(seed); self._advance()

    def _advance(self):
        while self.state.is_chance_node():
            choices = dict(self.state.chance_outcomes())
            self.state.apply_action(self.rng.choices(list(choices), weights=list(choices.values()))[0])
        self.terminal = self.state.is_terminal()
        self.returns = self.state.returns() if self.terminal else None

    @property
    def player(self): return self.state.current_player()

    def observation(self):
        return torch.tensor(self.state.information_state_tensor(self.player)), torch.tensor(
            [a in self.state.legal_actions() for a in range(2)], dtype=torch.bool)

    def step(self, action): self.state.apply_action(action); self._advance()


def kuhn_metrics(agents):
    import numpy as np
    from packages.strategy.games import game_tree
    tree = game_tree('kuhn')
    p = np.stack([agents[owner].probabilities(torch.tensor(x), torch.tensor(mask)).numpy()
                  for x, mask, owner in zip(tree.features, tree.mask, tree.owners)])
    value = tree.evaluate(p); reference = tree.reference_metrics(p)
    if abs(value['exploitability'] - reference['exploitability']) > 1e-9:
        raise ValueError('Independent Kuhn evaluator mismatch')
    return value


def arena(agents, features, config, deadline=float('inf')):
    results = {}; streets = {s: 0 for s in ['preflop', 'flop', 'turn', 'river']}
    for opponent in config['evaluation_opponents']:
        paired = []
        for deal in range(config['evaluation_deals']):
            if time.monotonic() > deadline: raise TimeoutError('Local NFSP arena time budget reached')
            returns = []
            for seat in (0, 1):
                env = HoldemEnv(config['evaluation_seed'] + deal, features)
                rng = random.Random(f'{opponent}:{deal}:{seat}')
                while not env.terminal:
                    obs = env.visible(); labels = candidates(obs)
                    if env.player == seat:
                        x, mask = env.observation(); p = agents[seat].probabilities(x, mask).tolist()
                        streets[obs['state']['street']] += 1
                    elif opponent == 'uniform': p = [float(a in labels) / len(labels) for a in ACTIONS]
                    elif opponent == 'passive': p = [float(a == ('check' if 'check' in labels else 'call')) for a in ACTIONS]
                    else: p = probabilities(obs, opponent)
                    env.step(rng.choices(range(len(ACTIONS)), weights=p)[0])
                returns.append(env.returns[seat] * 50)
            paired.append(mean(returns))
        avg = mean(paired); se = stdev(paired) / math.sqrt(len(paired)) if len(paired) > 1 else 0.
        results[opponent] = {'paired_deals': len(paired), 'hands': len(paired) * 2,
                             'bb_per_hand': avg, 'standard_error': se, 'ci95': [avg - 1.96 * se, avg + 1.96 * se]}
    return {'opponents': results, 'street_decisions': streets,
            'scope': 'Fixed synthetic baselines, duplicate deals with seats swapped. Not exploitability or a GTO certificate.'}


def atomic_torch(path, value, max_bytes):
    temporary = path.with_suffix('.tmp'); torch.save(value, temporary)
    if temporary.stat().st_size > max_bytes:
        temporary.unlink(); raise ValueError('NFSP checkpoint storage bound exceeded')
    temporary.replace(path)


def fit(domain, seed, config, output, features, identity, deadline, resume=False):
    output.mkdir(parents=True, exist_ok=True)
    inputs, actions = (DIM, len(ACTIONS)) if domain == 'holdem' else (len(KuhnEnv(0).observation()[0]), 2)
    agents = [Agent(inputs, actions, config['agent'], seed + p * 1009) for p in (0, 1)]
    episode, curve = 0, []
    checkpoint = output / 'checkpoint.pt'
    if resume and checkpoint.exists():
        if checkpoint.stat().st_size > config['max_checkpoint_bytes']: raise ValueError('Oversized checkpoint')
        saved = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if (saved['identity'], saved['domain'], saved['seed']) != (identity, domain, seed):
            raise ValueError('Checkpoint protocol, model or seed mismatch')
        for agent, state in zip(agents, saved['agents']): agent.restore(state)
        episode, curve = saved['episode'], saved['curve']
    maximum = config[domain + '_episodes']
    for step in range(episode, maximum):
        if time.monotonic() > deadline: raise TimeoutError('Local NFSP time budget reached; resume the retained checkpoints')
        game_seed = config['training_seed'] + seed * 100000 + step
        env = HoldemEnv(game_seed, features) if domain == 'holdem' else KuhnEnv(game_seed)
        train_episode(env, agents)
        if (step + 1) % config['evaluation_interval'] == 0 or step + 1 == maximum:
            entry = {'episode': step + 1, 'losses': [a.last_losses for a in agents],
                     'decisions': [a.decisions for a in agents], 'updates': [a.updates for a in agents],
                     'supervised_seen': [a.seen for a in agents]}
            if domain == 'kuhn': entry['metrics'] = kuhn_metrics(agents)
            curve.append(entry)
            atomic_torch(checkpoint, {'version': VERSION, 'identity': identity, 'domain': domain, 'seed': seed,
                         'episode': step + 1, 'agents': [a.state() for a in agents], 'curve': curve}, config['max_checkpoint_bytes'])
            print(canonical({'domain': domain, 'seed': seed, **entry}), flush=True)
    return agents, curve


def run(root, output, config, resume=False):
    import mlflow
    root = root.resolve(); output = output.resolve()
    if not output.is_relative_to(root): raise ValueError('Research output must remain inside the local workspace')
    if config['cloud_enabled'] is not False or config['cloud_cost_usd'] != 0: raise ValueError('Local-only experiment')
    if shutil.disk_usage(root).free < config['minimum_free_disk_gib'] * 1024**3: raise ValueError('Insufficient free disk')
    torch.set_num_threads(config['threads']); torch.use_deterministic_algorithms(True)
    pointer = json.loads((root / 'river-coach.json').read_text()); bundle = manifest(root, pointer)
    for item in bundle['members']: checked_bytes(root, item, 16 * 1024 * 1024)
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
               ['packages/strategy/nfsp.py', 'packages/strategy/holdem_nfsp.py', 'training/holdem_nfsp.py',
                'packages/features/behavior.py', 'packages/pokermind_core/engine.py', 'packages/models/river.py',
                'packages/models/river_inference.py']}
    protocol = {'config': config, 'river_pointer': pointer, 'source_sha256': sources,
                'features_version': FEATURE_VERSION, 'algorithm': VERSION}
    identity = digest(protocol)
    if output.exists():
        if not resume: raise ValueError('Existing research directory; use --resume explicitly')
        if json.loads((output / 'protocol.json').read_text()) != protocol: raise ValueError('Cannot resume a changed protocol or source')
    else:
        output.mkdir(parents=True); (output / 'protocol.json').write_text(canonical(protocol))
    features = Features(root, pointer); deadline = time.monotonic() + config['max_seconds']
    mlflow.set_tracking_uri('sqlite:///' + str(root / 'mlflow.sqlite3')); mlflow.set_experiment('pokermind-holdem-nfsp')
    runs = []
    for domain in ['kuhn', 'holdem']:
        for seed in config['seeds']:
            folder = output / f'{domain}-{seed}'; result_path = folder / 'result.json'
            if result_path.exists():
                result = json.loads(result_path.read_text())
                checked_bytes(root, result['artifact'], 16 * 1024 * 1024)
                runs.append(result); continue
            with mlflow.start_run(run_name=f'{domain}-{seed}') as tracking:
                mlflow.log_params({'domain': domain, 'seed': seed, 'protocol_id': identity, 'algorithm': VERSION,
                                   'episodes': config[domain + '_episodes'], 'cloud_cost_usd': 0})
                mlflow.log_dict(protocol, 'protocol.json')
                agents, curve = fit(domain, seed, config, folder, features, identity, deadline, resume)
                # Fixed final checkpoint, no test-based checkpoint or seed selection.
                artifact = {'version': VERSION, 'features_version': FEATURE_VERSION, 'domain': domain, 'seed': seed,
                            'width': config['agent']['width'], 'inputs': agents[0].inputs, 'actions': agents[0].actions,
                            'average': [a.average.state_dict() for a in agents], 'river_pointer': pointer,
                            'protocol_id': identity, 'stack_bb': 50}
                path = folder / 'average-policy.pt'; atomic_torch(path, artifact, 16 * 1024 * 1024)
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                metrics = kuhn_metrics(agents) if domain == 'kuhn' else arena(agents, features, config, deadline)
                result = {'domain': domain, 'seed': seed, 'metrics': metrics, 'curve': curve,
                          'artifact': {'path': path.relative_to(root).as_posix(), 'sha256': sha},
                          'run_id': tracking.info.run_id, 'episodes': config[domain + '_episodes']}
                mlflow.log_dict(metrics, 'evaluation.json'); mlflow.log_dict({'curve': curve}, 'curve.json')
                mlflow.log_artifact(str(path), 'policy')
                if domain == 'kuhn': mlflow.log_metric('exploitability', metrics['exploitability'])
                else:
                    for name, value in metrics['opponents'].items(): mlflow.log_metric(name + '_bb_per_hand', value['bb_per_hand'])
                result_path.write_text(canonical(result)); runs.append(result)
                print(canonical({'completed': domain, 'seed': seed, 'metrics': metrics}), flush=True)
    kuhn = [r for r in runs if r['domain'] == 'kuhn']; holdem = [r for r in runs if r['domain'] == 'holdem']
    report = {'version': VERSION, 'status': 'research-only', 'protocol_id': identity, 'protocol': protocol,
              'runs': runs, 'selected': holdem[0]['artifact'], 'selection': 'First predeclared seed, final episode; never selected by arena results.',
              'quality_gates': {'kuhn': all(r['metrics']['exploitability'] <= config['quality_gates']['kuhn_max_exploitability'] for r in kuhn),
                                'holdem_baselines': all(v['ci95'][0] > 0 for r in holdem for v in r['metrics']['opponents'].values()),
                                'holdem_exploitability_certified': False},
              'promotion': {'eligible': False, 'reason': 'Exploratory, abstracted heads-up self-play. General Hold’em optimality is unproven; prior Deep CFR gates remain unchanged.'},
              'cloud_enabled': False, 'cloud_cost_usd': 0}
    path = output / 'report.json'; path.write_text(canonical(report))
    pointer = {'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    temp = root / ('holdem-nfsp-' + uuid.uuid4().hex + '.tmp'); temp.write_text(canonical(pointer)); temp.replace(root / 'holdem-nfsp.json')
    print(canonical({'report': str(path), 'quality_gates': report['quality_gates']}), flush=True)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, default=ROOT / '.data')
    p.add_argument('--output', type=Path); p.add_argument('--protocol', type=Path, default=ROOT / 'configs/holdem-nfsp-v1.json')
    p.add_argument('--resume', action='store_true'); args = p.parse_args()
    run(args.data, args.output or args.data / 'models/holdem-nfsp' / ('run-' + uuid.uuid4().hex[:12]),
        json.loads(args.protocol.read_text()), args.resume)
