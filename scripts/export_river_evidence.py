"""Publish checksummed v3 research summaries without changing serving defaults."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from training.river import checksum, checked_dataset
from packages.models.evidence import river_summary
from packages.pokermind_core.contracts import canonical, digest


def export(root, destination):
    manifest = checked_dataset(root / 'dataset/dataset.json')
    report = json.loads((root / 'models/report.json').read_text()); scored = json.loads((root / 'scoring.json').read_text())
    if report['manifest_hash'] != digest(manifest) or scored['manifest_hash'] != digest(manifest):
        raise ValueError('Research lineage mismatch')
    for run in report['runs']:
        if checksum(root / 'models' / run['model_path']) != run['sha256']:
            raise ValueError('Research model changed')
    summary = river_summary(manifest, report, scored, checksum(root / 'models/report.json'),
                            prior_family_audit=json.loads((root / 'prior-family-audit.json').read_text()),
                            execution='Local Spark and CPU PyTorch; no cloud resources')
    evidence = destination / 'evidence'; evidence.mkdir(parents=True, exist_ok=True)
    payload = canonical(summary); filename = 'river-v3-' + digest(summary)[:16] + '.json'
    (evidence / filename).write_text(payload)
    pointer = {'path': 'evidence/' + filename, 'sha256': checksum(evidence / filename)}
    temporary = destination / 'river-research.tmp'; temporary.write_text(canonical(pointer)); temporary.replace(destination / 'river-research.json')
    results = ROOT / 'benchmarks/results'
    (results / 'river-v3-summary.json').write_text(json.dumps(summary, indent=2))
    (results / 'river-v3-report.json').write_text(json.dumps(report, indent=2))
    (results / 'river-v3-protocol.json').write_text((root / 'pre-run-protocol.json').read_text())
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, default=ROOT / '.data/research-river-v3'); p.add_argument('--publish', type=Path, default=ROOT / '.data')
    a = p.parse_args(); export(a.data, a.publish)
