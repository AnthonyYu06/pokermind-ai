"""Pure report formatting shared by local research and cloud publication."""
import statistics
from packages.pokermind_core.contracts import digest


def river_summary(manifest, report, scored, report_sha256, *, execution, prior_family_audit=None):
    if report['manifest_hash'] != digest(manifest) or scored['manifest_hash'] != digest(manifest):
        raise ValueError('Research lineage mismatch')
    groups = []
    for name in report['protocol']['models']:
        runs = [r for r in report['runs'] if r['model'] == name]
        values = [r['test']['mae'] for r in runs]
        groups.append({'model': name, 'n': len(runs), 'mae_mean': statistics.mean(values), 'mae_std': statistics.pstdev(values),
                       'minimum_independent_test_families': min(r['test']['independent_families'] for r in runs), 'gates': report['promotion_gates'][name]})
    return {'status': 'measured', 'version': report['version'], 'domain': report['domain'], 'dataset_id': manifest['dataset_id'],
            'counts': report['counts'], 'groups': groups, 'baselines': {k: v['mae'] for k, v in report['baselines'].items()},
            'scored_rows': scored['rows'], 'prior_family_audit': prior_family_audit, 'promotion': report['promotion'],
            'execution': execution, 'cloud_enabled': False, 'report_sha256': report_sha256}
