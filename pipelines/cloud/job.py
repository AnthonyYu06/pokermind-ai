"""Databricks wheel entrypoint: immutable materialize -> train -> score -> publish."""
import argparse
import json
import os
from pathlib import Path
import platform
import time
import uuid
from packages.pokermind_core.contracts import canonical, digest
from .budget import Ledger, DynamoBackend, validate
from .plan import validate_plan


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(canonical(value)); temporary.replace(path)


def stage(spark, root, plan, name):
    """Shared implementation for an authorized cloud job or isolated local test."""
    from training.river import checksum
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    plan_file = root / 'plan.json'
    if plan_file.exists() and json.loads(plan_file.read_text()) != plan:
        raise ValueError('An artifact root cannot be reused for a different plan')
    if not plan_file.exists(): atomic_json(plan_file, plan)
    def checked_artifact(record):
        target = (root / record['path']).resolve()
        if not target.is_relative_to(root.resolve()) or record['plan_hash'] != digest(plan) or checksum(target) != record['sha256']:
            raise ValueError('Stage artifact changed or escaped its root')
        return target
    pointer = root / (name + '.json')
    if pointer.exists():
        record = json.loads(pointer.read_text())
        checked_artifact(record)
        return record
    def upstream(stage_name):
        result = json.loads((root / (stage_name + '.json')).read_text())
        return checked_artifact(result)
    started = time.monotonic()
    attempt = name + '-' + uuid.uuid4().hex[:12]
    if name == 'materialize':
        from pipelines.spark.workload import materialize
        artifact = materialize(spark, plan['lake_uri'], root / attempt, count=plan['count'], seed=plan['seed'], partitions=plan['partitions'], mode='hands', previous_manifest=plan.get('previous_dataset_manifest'))
    elif name == 'train':
        from training.river import train
        artifact = train(upstream('materialize'), root / attempt, plan['research'], tracking_uri=plan.get('tracking_uri', 'databricks'),
                         experiment=plan['experiment'], registry_name=plan.get('registered_model'))
    elif name == 'score':
        from pipelines.spark.workload import score
        artifact = root / (attempt + '.json')
        score(spark, upstream('materialize'), upstream('train'), plan['lake_uri'], artifact)
    elif name == 'publish':
        from .publish import export_snapshot
        artifact = root / (attempt + '.json')
        result = export_snapshot(spark, upstream('materialize'), upstream('score'), root / (attempt + '.zip'), upstream('train'))
        result.update(plan_hash=digest(plan), created_at=time.time(), source_kind='synthetic', status='research-only')
        atomic_json(artifact, result)
    else:
        raise ValueError('Unknown stage')
    result = {'path': str(artifact.relative_to(root)), 'sha256': checksum(artifact), 'plan_hash': digest(plan), 'seconds': time.monotonic() - started}
    atomic_json(pointer, result)
    return result


def preflight(envelope):
    plan, policy, reservation = envelope['plan'], envelope['policy'], envelope['reservation']
    validate(policy, plan, time.time()); validate_plan(plan)
    from training.river import checksum
    if not os.environ.get('DATABRICKS_RUNTIME_VERSION'):
        raise ValueError('Cloud entrypoint requires a Databricks job runtime')
    if platform.python_version_tuple()[:2] != ('3', '12'):
        raise ValueError('Cloud compatibility contract requires Python 3.12')
    if checksum(Path(plan['wheel_uri'])) != plan['wheel_sha256']:
        raise ValueError('Deployed wheel checksum mismatch')
    import boto3
    session = boto3.Session(region_name=plan['region'])
    if session.client('sts').get_caller_identity()['Account'] != plan['account_id']:
        raise ValueError('Job instance profile account mismatch')
    ledger = Ledger(DynamoBackend(session.client('dynamodb'), plan['budget_table']))
    ledger.require(reservation['key'], reservation['token'], digest(plan), time.time())
    root = Path(plan['volume_root']) / reservation['token']
    check_storage(session.client('s3'), root, plan)
    return plan, root


def check_storage(client, root, plan):
    from urllib.parse import urlparse
    size = sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) if root.exists() else 0
    # Include retained object versions, not just current objects. Double-counting
    # a volume backed by the same prefix is conservative admission behavior.
    for key in ('lake_uri', 'publication_uri'):
        uri = urlparse(plan[key])
        for page in client.get_paginator('list_object_versions').paginate(Bucket=uri.netloc, Prefix=uri.path.strip('/') + '/'):
            size += sum(item['Size'] for item in page.get('Versions', []))
            if size > plan['max_storage_bytes']:
                raise ValueError('Retained S3/artifact storage exceeds the approved bound')
    if size > plan['max_storage_bytes']:
        raise ValueError('Artifact storage exceeds the approved bound')
    return size


def publish_s3(client, root, plan, record):
    """Write immutable data first; publish its small manifest last."""
    from urllib.parse import urlparse
    from training.river import checksum
    root = Path(root); publication = json.loads((root / record['path']).read_text())
    snapshot = root / publication['snapshot']
    if checksum(snapshot) != publication['sha256']:
        raise ValueError('Snapshot checksum mismatch')
    uri = urlparse(plan['publication_uri']); prefix = uri.path.strip('/') + '/' + publication['sha256']
    def put(key, body, sha, content_type):
        import base64
        try:
            client.put_object(Bucket=uri.netloc, Key=key, Body=body, IfNoneMatch='*', ServerSideEncryption='AES256',
                              ChecksumSHA256=base64.b64encode(bytes.fromhex(sha)).decode(), ContentType=content_type, Metadata={'sha256': sha})
        except client.exceptions.ClientError as exc:
            if exc.response['Error']['Code'] not in ('PreconditionFailed', '412'):
                raise
            existing = client.head_object(Bucket=uri.netloc, Key=key, ChecksumMode='ENABLED')
            if existing.get('ChecksumSHA256') != base64.b64encode(bytes.fromhex(sha)).decode():
                raise ValueError('Existing publication has a different checksum') from exc
    with snapshot.open('rb') as body: put(prefix + '/snapshot.zip', body, publication['sha256'], 'application/zip')
    publication['snapshot_uri'] = f's3://{uri.netloc}/{prefix}/snapshot.zip'
    payload = canonical(publication).encode()
    import hashlib
    put(prefix + '/publication.json', payload, hashlib.sha256(payload).hexdigest(), 'application/json')
    return {**publication, 'publication_uri': f's3://{uri.netloc}/{prefix}/publication.json',
            'publication_sha256': hashlib.sha256(payload).hexdigest()}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--stage', choices=['materialize', 'train', 'score', 'publish'], required=True)
    parser.add_argument('--envelope', required=True); args = parser.parse_args()
    plan, root = preflight(json.loads(args.envelope))
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    if spark.sparkContext.master.startswith('local') or not spark.version.startswith('4.0.'):
        raise ValueError('Expected classic Databricks Spark 4.0 compute')
    result = stage(spark, root, plan, args.stage)
    if args.stage == 'publish':
        import boto3
        publication = publish_s3(boto3.client('s3', region_name=plan['region']), root, plan, result)
        atomic_json(root / 's3-publication.json', publication)
    import boto3
    check_storage(boto3.client('s3', region_name=plan['region']), root, plan)
    print(canonical(result))


if __name__ == '__main__': main()
