# Cloud operations: locally implemented, cloud execution pending

Spending and personal uploads remain disabled. Run these commands from the
repository root using the local SDK environment. Account-specific plans, pricing,
runtime patches, permissions and notifications still require real-account review.

## Inventory, stopping and reconciliation

`make cloud-inventory` is an offline readiness check. To inventory an existing
workspace through local authentication, use `make cloud-inventory
ARGS="--plan .data/approved-plan.json --inventory"`. This reads metadata and
does not start compute. Never put credentials in the plan or send keys in chat.

Retain the exact submitted plan, ledger key and original idempotency token:

```sh
python -m pipelines.cloud.operations status --plan .data/approved-plan.json --key pokermind:YYYY-MM --token ORIGINAL_TOKEN
python -m pipelines.cloud.operations stop --plan .data/approved-plan.json --key pokermind:YYYY-MM --token ORIGINAL_TOKEN
python -m pipelines.cloud.operations app-status --plan .data/approved-plan.json --instance-id INSTANCE_ID
python -m pipelines.cloud.operations app-stop --plan .data/approved-plan.json --instance-id INSTANCE_ID
```

Run operations verify the account, ledger reservation, job tags and run/job
binding. Stop requests cancellation and terminates only matching dedicated JOB
clusters. Shared/foreign clusters are refused. A uniquely recoverable lost submit
acknowledgment can be stopped without resubmission. Ambiguous ownership requires
inspection; the full reserve remains charged. Stop works when new spending is
disabled. A request is not proof of termination: inspect status until compute stops.

App operations require matching project/owner/private-app-name tags and stop only
EBS-backed app instances. Nothing terminates EC2 instances, deletes storage or
refunds a reservation automatically. S3 versions, disks, logs, images and the ledger
can continue to incur charges after compute stops.

Final billing JSON requires `run_id`, `status: "final"`, evidence `source`, integer
`cost_cents` for `compute`, `databricks`, `storage`, `requests`, `network`, `logs`,
`ledger`, and `retained_storage_reserve_cents`. Reconcile only after verified stop:

```sh
python -m pipelines.cloud.operations reconcile --plan .data/approved-plan.json --key pokermind:YYYY-MM --token ORIGINAL_TOKEN --billing .data/final-billing.json --output .data/closed-reservation.json
```

The ledger records billing checksum, stop evidence and actual charges, including
overruns. Unknown billing never produces a refund. Terraform defines monthly AWS
Budget notifications and a private-app EC2 status alarm. Configure reviewed
recipients/SNS topic and verify activation of the Project cost-allocation tag.
Delayed AWS alerts do not guarantee a cap or capture separately billed Databricks
charges; the admission ledger includes both providers.

## Verified snapshot installation and deployment

The publisher returns content-addressed snapshot/publication URIs and the
publication SHA256. It writes the ZIP first and manifest last. The snapshot includes
exact scores, neural comparisons and the model report displayed in Research lab.
Smoke runs retain failed quality/support gates; publication never promotes a model.

```sh
python -m services.api.snapshots --publication /path/publication.json --archive /path/snapshot.zip --publication-sha256 REVIEWED_SHA256 --destination /path/new-workspace
POKERMIND_DATA=/path/new-workspace python -m services.api.cli serve
```

Installation checks both outer digests, bounded sizes, unsafe/duplicate ZIP paths
and links, every inner backup checksum, SQLite integrity, synthetic provenance
and declared counts. It installs atomically into a new directory. `--if-installed`
recognizes the same pinned publication and preserves subsequent study work; it
does not certify that the workspace has remained unchanged. A killed installer
may leave its exclusive `.install-lock`: inspect the process before removing a
stale lock. It is never automatically stolen from another installer.

For rollback, stop serving, preserve the current workspace/backup, install an older
pinned publication in another directory and change the data-directory selection.
Existing study data is never overwritten. Foundation provisioning with
`enable_app=false` creates ECR before the VM, so a reviewed/scanned image can be
pushed first. Pin its digest plus `snapshot_sha256` and `publication_sha256` before
reviewing the app phase. Bootstrap downloads bounded objects, verifies them in a
non-networked container, and restores before starting the loopback app. The expiry
timer starts before downloads. Real IAM/SSM, alert delivery and shutdown still need
cloud verification.

## Benchmark collection

Copy scoped job artifacts and complete Spark JSON event logs into a local evidence
directory using approved read access. Preserve the launcher's monitoring JSON,
which retains worker identities after shutdown. Contract-test fixtures are never
measured evidence.

```sh
python -m benchmarks.cloud collect --root .data/run-artifacts --events .data/run-events --observation .data/cloud-execution/ORIGINAL_TOKEN.json --repetition 1 --kind measured --output .data/benchmark-run-1.json
python -m benchmarks.cloud compare .data/benchmark-run-*.json --output .data/scaling-comparison.json
```

Records include dataset/code/runtime identity, quality, shard bytes, throughput,
stage/startup/total durations, shuffle, spill, task skew and worker evidence. Task
totals include retries/repeated scans; peak task execution memory is not machine
RAM. The parser streams bounded uncompressed/gzip logs. Local Spark logs are tested;
managed runtime log delivery remains unverified.

Strong-scaling comparison requires one-worker and multiple-worker configurations,
at least three distinct measured repetitions each, identical input/code/runtime/
partition/hardware definitions, successful stopped compute and actual worker
identities. Warmups are excluded. Incomplete comparisons write reasons and return
exit code 2 without speedup claims. Local threads cannot pass. Both worker-only
and driver-inclusive efficiency are labeled.

Add `--receipt .data/closed-reservation.json --billing .data/final-billing.json` to
collection for verified billed cost. Per-million rates divide the whole four-stage
run's cost by hands/scores; retained-storage reserve is separate. Without final
billing, actual cost remains unknown.

References: [Databricks cancellation](https://docs.databricks.com/api/jobs/v2/cancel-run),
[cluster termination](https://docs.databricks.com/api/clusters/v2/delete-cluster),
[EC2 stopping](https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/stop_instances.html),
[S3 retrieval](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html),
[AWS budget filters](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create-filters.html).
