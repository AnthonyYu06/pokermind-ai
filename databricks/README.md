# PokerMind batch jobs

See [operational commands](../docs/cloud-operations.md) for scoped stop/status,
cost reconciliation, pinned snapshot installation and benchmark evidence collection.

The executable job has four dependent wheel tasks: **materialize → train → score → publish**.
`pipelines/cloud/plan.py` renders the Jobs API definition; the task entrypoint is
`pipelines.cloud.job:main`. `scripts/build_cloud_wheel.py` creates a content-versioned
batch wheel that installs only Treys and Pydantic. It does **not** overwrite the
runtime-managed Spark, Delta, PyTorch or MLflow packages.

The target is classic dedicated CPU jobs compute, DBR **17.3 LTS ML**, Python 3.12,
Spark 4.0 and PyTorch 2.7. The exact runtime key must also be present in the target
workspace inventory. Local compatibility tests are not an executed Databricks gate.

The job generates deterministic work IDs on Spark partitions, persists synthetic
raw records to S3, reconstructs hands into Silver, builds Gold aggregates and exact
river labels, exports frozen Parquet shards to a Unity Catalog volume, fits neural
models and baselines, tracks MLflow runs and a research-only UC model version,
scores partitions, and publishes a checksummed app backup to a private S3 prefix.
All batch stages share the local implementations. Training is single-driver CPU
PyTorch; ETL and batch scoring are Spark-distributed. This is not distributed
gradient training or a live poker service.

## Local commands

```sh
make cloud-preflight
.venv/bin/python -m pip install setuptools==84.0.0 wheel==0.46.3
make cloud-wheel
make verify-cloud-local
make verify-portable-incremental
```

`cloud-preflight` reads configuration only. The example plan is deliberately
unpriced and fails admission. `verify-cloud-local` exercises every task body,
retry reuse, failure isolation, backup restoration and score lineage with local
Spark. The incremental check compares full and overlapping builds, then compacts
a fragmented copy while verifying the old pinned Delta version remains readable.
Neither command connects to AWS or Databricks.

## Account-specific setup still required

1. Inventory the existing AWS account, region, Databricks workspace, available
   runtime keys, instance profiles, cluster policies and Unity Catalog permissions.
   `scripts/cloud_preflight.py --plan YOUR_PLAN --inventory` uses metadata APIs only
   with existing SDK unified authentication. Never place credentials in the plan.
2. Review the exact managed runtime's current package/security patch evidence.
   The upstream packages used for the original local compatibility test had
   advisories; that run establishes API compatibility, not deployment clearance.
   A local upgrade does not prove the vendor runtime has the same patches. Review
   region-specific rates and include driver/worker compute, DBUs, storage,
   requests, networking, logs, the budget ledger and retained storage. The plan's
   upper bound must equal its complete cost worksheet. Configure an exact timeout,
   worker cap, instance allowlist, expiry and a Databricks cluster policy enforcing
   these values. A notebook/task check runs after allocation, so the launcher must
   make the reservation **before** submitting any compute.
3. Review Terraform's private S3/batch IAM/DynamoDB definitions. Batch infrastructure
   no longer implicitly creates an app VM (`enable_app=false`). Provisioning itself
   needs a separately approved envelope. Register the instance profile and configure
   UC external locations/volume grants; these are workspace/account-specific.
4. Validate the built wheel, upload it to the reviewed volume path and record its
   SHA256. Validate the job definition against the workspace. The wheel installs
   no Spark/Delta/Torch pins over managed libraries. For a learning run, use one
   worker and synthetic input only; cost a two-worker benchmark separately.
5. Only after explicit approval, record the exact plan hash and nonzero approved
   limits in the central cost policy. Submit with a unique retained token using
   `python -m pipelines.cloud.launch --plan YOUR_PLAN --token UNIQUE_REQUEST_ID`.
   It checks policy before constructing SDK clients, verifies the AWS account,
   reserves the full quoted bound in DynamoDB with conditional writes, creates or
   recovers an unscheduled job, verifies its exact task/cluster settings, then calls
   run-now with the same idempotency token. No schedules are created. Unscheduled
   job definitions remain as execution evidence and allocate no compute by themselves.

## Failure, publication and cost accounting

Attempt directories are immutable. Completed stage pointers contain artifact
hashes; retries reuse only verified outputs. An interrupted ETL leaves unpublished
tables and no completed dataset manifest. Incremental runs pin the previous table
versions, reject conflicting revisions and retain the old publication. Unknown
submission outcomes keep their reservation and reuse the same Jobs idempotency
token. An expiry never refunds reserved money.

S3 publication writes the backup first and its manifest last with conditional
create and SHA256 checks. No mutable production model alias is advanced. The app
backup contains exact reference scores with clearly labeled neural comparisons;
restore it to a new directory with the existing `restore` CLI after verifying and
extracting the checksum-protected archive. Serving never contacts Spark.

`pipelines.cloud.observe` captures actual run/cluster/worker/stop evidence. The
distributed gate requires at least two observed worker hosts and executor IDs,
not a configured count or local threads. Reconcile a reservation only with a
terminal run, all its clusters terminated, and final cost evidence including a
reserve for retained storage. Timeout, resource caps and reservation accounting
reduce risk but do not guarantee a billing hard cap. Inability to read/update the
ledger blocks new work. No VACUUM or destructive teardown runs automatically.

Official references: [DBR ML environment](https://docs.databricks.com/aws/en/release-notes/runtime/17.3lts-ml),
[shared job clusters](https://docs.databricks.com/aws/en/reference/jobs-api-2-1-updates),
[Unity Catalog volume files](https://docs.databricks.com/aws/en/volumes/volume-files),
[DynamoDB conditional expressions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.ConditionExpressions.html),
[Delta compaction](https://docs.delta.io/optimizations-oss/).
