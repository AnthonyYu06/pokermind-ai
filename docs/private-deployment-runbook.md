# Private deployment candidate — local validation only

The [operations guide](cloud-operations.md) documents the implemented pinned
snapshot installer, scoped stop commands and billing reconciliation. Bootstrap
now restores a reviewed publication before serving. Provision the foundation
with `enable_app=false`, push/scan the image, then review the app phase with its
image/publication/snapshot digests. AWS Budget recipients, an existing app-alert
SNS topic and the activated cost-allocation tag are required before enabling.

Nothing has been provisioned. Authorized cloud spending remains $0. The Terraform
configuration in `infra/terraform` defaults to zero resources and refuses an enabled
configuration while the central cost policy is disabled. Terraform 1.16.6 was
downloaded from HashiCorp and checksum-verified; AWS provider selection is recorded
in `.terraform.lock.hcl`. Local schema validation is evidence about configuration,
not AWS access, runtime behavior, price or a working deployment.

The candidate uses an existing public subnet in an approved VPC: outbound HTTPS,
no inbound security-group rules, SSM authentication, IMDSv2, a digest-pinned ECR
image, encrypted persistent root disk, private versioned S3 buckets and CloudWatch
logs. The app role can read only published snapshot objects; it cannot write the
training lake. S3 denies plaintext transport and public access. ECR tags are
immutable. No NAT gateway, load balancer, GPU or always-on database is proposed.

A systemd expiry timer stops the instance after the approved interval on **every
boot**, including restarts. It is an operational bound, not a billing guarantee.
The root disk, bucket versions, images and logs continue to incur storage charges.
The stopped instance's automatically assigned public IPv4 is released. No Elastic
IP is allocated. AWS currently lists public IPv4 at $0.005/address-hour, checked
2026-10-15 on [AWS VPC pricing](https://aws.amazon.com/vpc/pricing/). All other rates
and totals still require region/account-specific verification before approval.

Before deployment, resolve and test: account/region/network inventory; Python image
and Node image digests; Linux lockfile/container compatibility; image scan; tested
AMI with Docker/SSM connectivity; exact cost worksheet; remote state bucket;
published snapshot import; IAM allow/deny probes; authenticated access and actual
health, backup, rollback and expiry behavior. The container recipe currently targets
base import/review; research simulator dependencies require a separately validated
serving image. The base serving image has now been built and functionally verified
as Linux/amd64 under Docker emulation on this ARM64 Mac. Official base-image
digests are pinned in `infra/container-images.json`; `make container-build` permits
only a local Docker socket. Tests exercised its production entrypoint/health,
frontend, import/replay and rejection of arbitrary-state requests as UID 10001
with a read-only root and no external network. See
`benchmarks/results/linux-container.json`. This is not an ECR push, a complete
image vulnerability assessment, or an AWS deployment.

Use `backend.tf.example` only after a reviewed state bucket exists. Configure
versioning, private access and S3 native locking (`use_lockfile=true`), as documented
in the [Terraform S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3).
Never commit backend credentials, `.tfvars`, state or plan artifacts.

After approval and successful deployment, the access command is:

```sh
aws ssm start-session --target APPROVED_INSTANCE_ID \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
```

This requires the authenticated AWS CLI and Session Manager plugin. The app stays
bound to instance loopback. Check `/api/health` through the tunnel. The [AWS SSM
instructions](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html)
describe prerequisites. No public app endpoint is created.

Stop compute with an explicit, reviewed instance ID and verify its stopped state.
Inventory retained disks, S3 versions, ECR images and logs separately. Do not equate
stopping compute with deleting data. Teardown requires a separate target inventory
and backup review; buckets deliberately have `prevent_destroy` and no force-delete.

Rollback means selecting a previously verified image digest and immutable snapshot,
restoring into a new data directory, checking health and eligibility tests, then
switching the service. A tag/alias change alone is not a validated rollback. Local
application backup/restore is now automated; remote image/snapshot rollback remains
unexecuted.
