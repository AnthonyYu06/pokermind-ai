"""Streaming shard training, validation-only selection, and sealed final evaluation.

Works with local MLflow or the Databricks tracking URI. Spark never collects the
training corpus: PyArrow streams immutable files, and only the bounded classical
baseline materializes a matrix (outside the Spark ETL).
"""
import argparse
import copy
import hashlib
import json
import platform
import random
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import IterableDataset, DataLoader
from packages.models.river import RiverNet, VERSION, DIM
from packages.pokermind_core.contracts import canonical, digest


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def checked_dataset(path):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text())
    if manifest['features_version'] != VERSION or manifest['source_kind'] != 'synthetic':
        raise ValueError('Unsupported river dataset contract')
    for f in manifest['files']:
        target = (path.parent / f['path']).resolve()
        if not target.is_relative_to(path.parent) or checksum(target) != f['sha256']:
            raise ValueError('Frozen shard checksum/path mismatch')
    if any(manifest['counts'].get(s, 0) == 0 for s in ('train', 'validation', 'test')):
        raise ValueError('Empty split; increase independent sample support')
    return manifest


class Shards(IterableDataset):
    def __init__(self, manifest, root, split, seed=None):
        self.manifest, self.root, self.split, self.seed = manifest, root, split, seed

    def __iter__(self):
        import pyarrow.parquet as pq
        files = [f for f in self.manifest['files'] if f['split'] == self.split]
        rng = random.Random(self.seed)
        if self.seed is not None:
            rng.shuffle(files)
        for f in files:
            for batch in pq.ParquetFile(self.root / f['path']).iter_batches(batch_size=512, columns=['features', 'equity', 'deal_family']):
                rows = batch.to_pylist()
                if self.seed is not None:
                    rng.shuffle(rows)
                for row in rows:
                    yield torch.tensor(row['features'], dtype=torch.float32), torch.tensor(row['equity'], dtype=torch.float32), row['deal_family']


def batches(manifest, root, split, config, seed=None):
    return DataLoader(Shards(manifest, root, split, seed), batch_size=config['batch_size'], num_workers=0)


def fit(manifest, root, config, seed, structural, output, resume=None, deadline=float('inf')):
    torch.set_num_threads(config['threads'])
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(seed)
    model = RiverNet(structural, config['width'])
    optimizer = torch.optim.AdamW(model.parameters(), lr=config['learning_rate'], weight_decay=.0001)
    signature = {'dataset': digest(manifest), 'config': digest({k: v for k, v in config.items() if k != 'epochs'}), 'seed': seed, 'structural': structural}
    start, stale, best, curve, best_state = 0, 0, float('inf'), [], None
    if resume:
        saved = torch.load(resume, weights_only=True, map_location='cpu')
        if saved['signature'] != signature:
            raise ValueError('Resume signature mismatch')
        model.load_state_dict(saved['model']); optimizer.load_state_dict(saved['optimizer']); torch.set_rng_state(saved['rng'])
        start, stale, best, curve, best_state = saved['epoch'], saved['stale'], saved['best'], saved['curve'], saved['best_state']
    for epoch in range(start, config['epochs']):
        if stale >= config['patience']:
            break
        if time.monotonic() > deadline:
            raise TimeoutError('Research time limit reached')
        model.train(); loss_sum = n = 0; norm_max = 0.
        for x, y, _ in batches(manifest, root, 'train', config, seed + epoch):
            optimizer.zero_grad(set_to_none=True)
            loss = (model(x) - y).square().mean()
            if not torch.isfinite(loss):
                raise FloatingPointError('Non-finite training loss')
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 5, error_if_nonfinite=True)
            optimizer.step(); loss_sum += float(loss.detach()) * len(y); n += len(y); norm_max = max(norm_max, float(norm))
        model.eval(); total = count = 0
        with torch.inference_mode():
            for x, y, _ in batches(manifest, root, 'validation', config):
                total += float((model(x) - y).abs().sum()); count += len(y)
        val = total / count
        curve.append({'epoch': epoch + 1, 'train_mse': loss_sum / n, 'validation_mae': val, 'gradient_norm_max': norm_max})
        if val < best - 1e-6:
            best, stale, best_state = val, 0, copy.deepcopy(model.state_dict())
        else:
            stale += 1
        temp = output / 'checkpoint.tmp'
        torch.save({'signature': signature, 'model': model.state_dict(), 'optimizer': optimizer.state_dict(), 'rng': torch.get_rng_state(),
                    'epoch': epoch + 1, 'best': best, 'best_state': best_state, 'stale': stale, 'curve': curve}, temp)
        temp.replace(output / 'checkpoint.pt')
    model.load_state_dict(best_state); model.eval()
    return model, curve, best


def measure(predict, data):
    predictions, truths, families = [], [], []
    for x, y, family in data:
        with torch.inference_mode():
            predictions.extend(np.asarray(predict(x)).tolist())
        truths.extend(y.tolist()); families.extend(family)
    p, y = np.asarray(predictions), np.asarray(truths)
    if not len(y) or not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise ValueError('Invalid evaluation prediction')
    e = p - y
    unique, ids = np.unique(families, return_inverse=True)
    sums = np.bincount(ids, weights=abs(e)); counts = np.bincount(ids)
    rng = np.random.default_rng(7001); boot = []
    for _ in range(500):
        sample = rng.integers(len(unique), size=len(unique)); boot.append(sums[sample].sum() / counts[sample].sum())
    bins = []
    for i in range(10):
        mask = (p >= i / 10) & ((p < (i + 1) / 10) if i < 9 else (p <= 1))
        if mask.any():
            bins.append({'n': int(mask.sum()), 'predicted': float(p[mask].mean()), 'reference': float(y[mask].mean())})
    return {'n': len(y), 'independent_families': len(unique), 'mae': float(abs(e).mean()), 'rmse': float(np.sqrt((e * e).mean())),
            'bias': float(e.mean()), 'mae_ci95': np.quantile(boot, [.025, .975]).tolist(), 'calibration_bins': bins}


def train(manifest_path, output, config, *, tracking_uri=None, experiment='pokermind-river-v3', registry_name=None):
    import mlflow
    from sklearn.ensemble import HistGradientBoostingRegressor
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    manifest = checked_dataset(manifest_path); root = manifest_path.parent
    if sum(manifest['counts'].values()) > 100_000:
        raise ValueError('Classical comparison bound is 100,000 labeled states')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'protocol.json').write_text(canonical(config))
    deadline = time.monotonic() + config['limits']['max_seconds']
    torch.set_num_threads(config['threads'])
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    if registry_name and tracking_uri == 'databricks':
        mlflow.set_registry_uri('databricks-uc')
    if tracking_uri and tracking_uri.startswith('sqlite:') and mlflow.get_experiment_by_name(experiment) is None:
        mlflow.create_experiment(experiment, artifact_location=(output.parent / 'mlflow-artifacts').as_uri())
    mlflow.set_experiment(experiment)
    # Fit ALL configurations and baselines before touching the final test split.
    train_rows = list(batches(manifest, root, 'train', config))
    x = torch.cat([r[0] for r in train_rows]).numpy(); y = torch.cat([r[1] for r in train_rows]).numpy(); del train_rows
    mean = float(y.mean()); augmented = np.c_[x, np.ones(len(x))]
    regularizer = np.eye(DIM + 1); regularizer[-1, -1] = 0
    ridge = np.linalg.solve(augmented.T @ augmented + regularizer, augmented.T @ y)
    boosted = HistGradientBoostingRegressor(max_iter=150, max_leaf_nodes=15, learning_rate=.1, l2_regularization=1., early_stopping=False, random_state=101).fit(x, y)
    del x, y, augmented
    runs = []
    for name in config['models']:
        for seed in config['seeds']:
            directory = output / f'{name}-{seed}'; directory.mkdir(); started = time.monotonic()
            with mlflow.start_run(run_name=directory.name) as run:
                mlflow.log_params({'dataset_id': manifest['dataset_id'], 'protocol_hash': digest(config), 'source': 'synthetic', 'features': VERSION,
                                   'seed': seed, 'architecture': name, 'width': config['width'], 'optimizer': 'AdamW', 'learning_rate': config['learning_rate'], 'device': 'cpu'})
                model, curve, best = fit(manifest, root, config, seed, name == 'structural', directory, deadline=deadline)
                path = directory / 'weights.pt'; torch.save(model.state_dict(), path)
                sha = checksum(path); immutable = path.with_name(f'weights-{sha[:16]}.pt'); path.rename(immutable)
                for entry in curve:
                    mlflow.log_metrics({k: v for k, v in entry.items() if k != 'epoch'}, step=entry['epoch'])
                mlflow.log_dict(manifest, 'dataset.json'); mlflow.log_dict(config, 'protocol.json'); mlflow.log_artifact(str(immutable), 'weights')
                mlflow.log_artifact(str(directory / 'checkpoint.pt'), 'checkpoint')
                runs.append({'model': name, 'seed': seed, 'structural': name == 'structural', 'width': config['width'], 'run_id': run.info.run_id,
                             'model_path': str(immutable.relative_to(output)), 'sha256': sha, 'model_bytes': immutable.stat().st_size,
                             'best_validation_mae': best, 'curve': curve, 'seconds': time.monotonic() - started})
                print(canonical({'trained': directory.name, 'validation_mae': best, 'seconds': runs[-1]['seconds']}), flush=True)
    selected = min(runs, key=lambda r: r['best_validation_mae'])
    predictors = {'constant_half': lambda x: np.full(len(x), .5), 'train_mean': lambda x: np.full(len(x), mean),
                  'ridge': lambda x: np.clip(np.c_[x.numpy(), np.ones(len(x))] @ ridge, 0, 1),
                  'histogram_boosted': lambda x: np.clip(boosted.predict(x.numpy()), 0, 1)}
    base = {name: measure(fn, batches(manifest, root, 'test', config)) for name, fn in predictors.items()}
    for result in runs:
        model = load_model(output / result['model_path'], result)
        result['test'] = measure(lambda x: model(x).numpy(), batches(manifest, root, 'test', config))
        with mlflow.start_run(run_id=result['run_id']):
            mlflow.log_metrics({f'test_{k}': result['test'][k] for k in ('mae', 'rmse', 'bias')})
            mlflow.log_dict(result, 'evaluation.json')
            if registry_name and result is selected:
                import mlflow.pytorch
                sample = next(iter(batches(manifest, root, 'validation', config)))[0][:2].numpy()
                from mlflow.models import infer_signature
                with torch.inference_mode(): example_output = model(torch.tensor(sample)).numpy()
                info = mlflow.pytorch.log_model(model, artifact_path='network', input_example=sample, signature=infer_signature(sample, example_output),
                                               code_paths=[str(Path(__file__).resolve().parents[1] / 'packages')])
                version = mlflow.register_model(info.model_uri, registry_name)
                mlflow.MlflowClient().set_model_version_tag(registry_name, version.version, 'pokermind_status', 'research-only')
                result['registered_version'] = str(version.version)
    threshold = config['thresholds']; best_base = min(r['mae'] for r in base.values()); gates = {}
    for name in config['models']:
        values = [r['test'] for r in runs if r['model'] == name]
        gates[name] = {'support': all(r['independent_families'] >= threshold['minimum_test_families'] for r in values),
                       'quality': all(r['mae'] <= threshold['maximum_mae'] and abs(r['bias']) <= threshold['maximum_absolute_bias'] for r in values),
                       'baseline_gain': bool(np.mean([r['mae'] for r in values]) <= best_base * (1 - threshold['minimum_relative_gain_vs_best_baseline']))}
    report = {'version': VERSION, 'status': 'research-only', 'dataset_id': manifest['dataset_id'], 'manifest_hash': digest(manifest), 'protocol': config,
              'counts': manifest['counts'], 'baselines': base, 'runs': runs, 'selected': selected, 'promotion_gates': gates,
              'runtime': {'python': platform.python_version(), 'torch': str(torch.__version__), 'platform': platform.platform()},
              'domain': 'Synthetic river / heads-up uniform opponent / showdown equity, no action EV',
              'promotion': 'No automatic promotion; explicit release and independent gate review required.'}
    path = output / 'report.json'; path.write_text(json.dumps(report, indent=2))
    return path


def load_model(path, spec):
    if checksum(path) != spec['sha256']:
        raise ValueError('Model checksum mismatch')
    model = RiverNet(spec['structural'], spec['width'])
    model.load_state_dict(torch.load(path, map_location='cpu', weights_only=True)); model.eval()
    return model


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--manifest', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=Path('configs/river-research-v3.json'))
    args = parser.parse_args()
    print(train(args.manifest, args.output, json.loads(args.protocol.read_text()), tracking_uri='sqlite:///' + str((args.output.parent / 'mlflow.sqlite3').resolve())))
