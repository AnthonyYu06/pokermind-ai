"""Measured Delta compaction with pinned-reader verification; no destructive VACUUM."""
from .workload import data_identity


def compact(spark, uri, pinned_versions):
    from delta.tables import DeltaTable
    table = DeltaTable.forPath(spark, uri)
    def read(version):
        return spark.read.format('delta').option('versionAsOf', version).load(uri)
    before_version = int(table.history(1).select('version').first()[0])
    details = table.detail().select('numFiles', 'sizeInBytes', 'minReaderVersion', 'minWriterVersion').first().asDict()
    before = {str(v): data_identity(read(v), read(v).columns) for v in sorted(set(pinned_versions + [before_version]))}
    metrics = [r.asDict(recursive=True) for r in table.optimize().executeCompaction().collect()]
    after_version = int(table.history(1).select('version').first()[0])
    for version, expected in before.items():
        frame = read(int(version))
        if data_identity(frame, frame.columns) != expected:
            raise ValueError('Pinned Delta snapshot changed during compaction')
    frame = read(after_version)
    if data_identity(frame, frame.columns) != before[str(before_version)]:
        raise ValueError('Compaction changed logical data')
    after = table.detail().select('numFiles', 'sizeInBytes', 'minReaderVersion', 'minWriterVersion').first().asDict()
    return {'before': details, 'after': after, 'before_version': before_version, 'after_version': after_version, 'metrics': metrics,
            'pinned_versions_verified': sorted(set(pinned_versions)), 'vacuum_executed': False,
            'retention': 'No files deleted. VACUUM is refused while any retained manifest pins this table; retain original files and Delta logs or export an independent frozen snapshot first.'}


def vacuum_allowed(uri, manifests):
    if any(any(table['uri'] == uri for table in manifest.get('tables', {}).values()) for manifest in manifests):
        raise ValueError('VACUUM would risk a retained training or application snapshot')
    # This product intentionally provides no destructive cleanup execution.
    return {'eligible_for_manual_review': True, 'deletion_authorized': False}
