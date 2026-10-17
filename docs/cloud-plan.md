# Cloud preparation — no spending authorization

Authorized spend **$0**. Personal uploads **not authorized**. No cloud resources
provisioned. `make cloud-run` exits nonzero. Proposed learning limits of $50/month
and $10/experiment are suggestions, not approval or current price estimates.

First proposed run: synthetic data only, smallest suitable one-worker Databricks
jobs cluster, no GPU, one job, one retry, maximum 15 minutes. Persist a frozen Delta
dataset manifest, MLflow run/model artifact, batch scores and app snapshot. Stop
compute after success/failure. Separately cost a two-worker-machine run for the
distributed gate. Local threads cannot satisfy that gate.

S3 candidate: block public access, SSE-S3, TLS-only and narrowly scoped prefixes.
Separate job writer, training reader and serving snapshot-reader roles. Temporary
identities; no notebook keys. CloudWatch logs and owner/expiry/project tags.

Private serving candidate: small on-demand EC2 accessed through authenticated SSM
port forwarding, no public inbound app port, read-only published snapshot. Price
SSM/ECR/S3 egress, public IPv4 or endpoints, disks/logs/snapshots before provisioning.
No default NAT gateway or load balancer.

| Approval worksheet | Current state |
|---|---|
| Region/account/existing resources | Unknown; read-only inventory not performed |
| Databricks workspace/billing/runtime | Unknown; no assumed free-tier capabilities |
| Instance / workers / GPUs | Unselected; 0 GPUs proposed |
| Time / retry / concurrency | Proposed 15 minutes / 1 / 1 |
| Unique hands / rows / bytes | Set from measured local manifest |
| Compute, DBUs, disk, storage/requests, transfer, logs, networking, idle | Obtain official region-specific quotes; verify billing to avoid double-counting |
| Expected/upper cost | Cannot estimate honestly before those selections |
| Stop/expiry/retained storage costs | Must be concrete before execution approval |
| Personal data | Excluded from first run |

AWS Budgets notifications lag use and are not hard caps. Implement central budget
reservation, resource limits and expiry before paid jobs. Stop new runs if tracking
fails. Compute stop and destructive data deletion must remain separate.
Terraform storage/private-app definitions and a container recipe are now in `infra/`.
See [deployment runbook](private-deployment-runbook.md) for their local validation
and remaining integration gates. Exact pricing and actual Databricks deployment
remain pending. The end-to-end adapter and four-stage job are implemented and
exercised locally, including Spark data creation, training, scoring and app
snapshot restoration. See [the execution guide](../databricks/README.md). This is
not an executed cloud gate.

The separate batch wheel now supports Python 3.12 while the local app keeps its
Python 3.13 contract. The full workflow passed locally on Python 3.12.15, Spark
4.0.0, Delta 4.0.0, PyTorch 2.7.0 and MLflow 3.0.1, including local model registry
registration, scoring and backup restore. The target is DBR 17.3 LTS ML; its exact
managed environment, UC grants and worker execution remain unverified. Source:
[official 17.3 LTS ML environment](https://docs.databricks.com/aws/en/release-notes/runtime/17.3lts-ml).
The wheel supplies project code/Treys/Pydantic only. Do not install the macOS
research lock over the Databricks managed Spark runtime.

The original upstream compatibility packages have known dependency advisories.
Their successful run is API compatibility evidence only. Before enabling a plan,
review the exact workspace runtime's current patch evidence; vendor backports
cannot be inferred from the upstream package version. The local Python 3.12
environment was upgraded separately; see [the runtime matrix](runtime.md).
