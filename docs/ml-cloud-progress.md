# ML and cloud execution work — October 16, 2026

## Fresh supervised learning result

The v3 experiment generated **16,000 independent synthetic river states** with a
new seed. Canonical visible-card families from the two previous frozen datasets
were excluded before splitting; cross-split identical features were filtered.
The final shards contain **11,355 train / 3,107 validation / 1,536 test** states.
All labels enumerate the 990 legal opponent holdings exactly. This is heads-up
showdown equity against a uniform range; it does not estimate betting EV.

The model is a two-block residual PyTorch network. Its structural inputs include
the acting player's known-card hand rank and board texture. They do not include
opponent cards, future actions, or equity labels. An otherwise identical raw-count
network tests the value of those features. A boosted-tree baseline receives the
same structural inputs as the deeper network.

| Method | Held-out MAE | Seed variation |
|---|---:|---:|
| Structural residual network | 0.032568 | SD 0.000552 across 3 seeds |
| Raw-count residual network | 0.094150 | SD 0.004703 across 3 seeds |
| Histogram boosted trees | 0.042021 | Fixed baseline seed |
| Ridge regression | 0.078890 | Deterministic fit |
| Constant 0.5 | 0.253210 | Deterministic |

The structural model passes the declared support, accuracy/bias and relative
baseline-improvement gates; its mean MAE is about **22.5% lower** than the strongest
tested baseline. All six fits finish before final test evaluation, and validation
alone chooses checkpoints and the research candidate. CPU deterministic
checkpoint/resume tests pass. MLflow stores parameters, datasets, curves, gradient
diagnostics, metrics and checkpoints. No production model alias is changed.

A post-training shift diagnostic on prior simulator-policy test states (excluded
from v3 fitting) gives **0.031777 MAE** across 801 decisions / 358 independent
families. This is an exploratory synthetic distribution-shift check, not real-play
validation or a second sufficiently supported promotion set. A fixed 40-state
latency diagnostic measured **0.192 ms median** for encoding plus neural inference
versus **9.928 ms** for exact enumeration. These warmed local CPU timings are not a
production SLO. The network's MAE on those 40 states was 0.03307.

Spark produced **15,998 versioned predictions**. A separate offline Spark batch
also applies the validation-selected model to stored eligible historical decisions
in the sample app. The exact reference remains visible alongside the neural
comparison; training and scoring dataset identities are separate. Research reports
are available in the app and included in backup/restore. This does not establish
preflop/flop/turn, nonuniform-range, multiway or real-player generalization.

Evidence: `benchmarks/results/river-v3-report.json`, `river-v3-summary.json`,
`river-v3-protocol.json` and `river-v3-diagnostics.json`. The pre-run source hashes
are preserved; later cloud portability changes have separate final source hashes.

## Spark and Delta execution

`pipelines/spark/workload.py` runs the same transformations on an existing local
Spark or Databricks classic session. Work IDs and simulation seeds are assigned
per partition. Raw records are immutable, Silver reconstruction shares the poker
engine and eligibility checks, Gold uses joins/windows/aggregates, labels are
generated per partition, and model scoring loads one model per partition with
bounded inference batches. Driver training streams immutable Parquet shards;
the separately bounded classical fit materializes at most 100,000 rows.

Local execution verified the four cloud task bodies through backup restoration:
256 generated hands, 338 stored scores, idempotent completed-stage reuse and
failure isolation. Separate tests verified **96 → 128** hand incremental processing
with overlapping input and equal full-build output identities. Compaction on an
isolated fragmented copy reduced **8 files / 81,378 bytes → 1 file / 37,935 bytes**,
while its old version 0 remained readable after version 1. These are file-layout
and correctness results, not distributed speedup. No VACUUM was executed.

Each attempt publishes a manifest only after its tables/shards are complete.
Frozen table versions, shard hashes, query plans, worker identities and Spark
application IDs are recorded. Current worker evidence is from one machine.

## Databricks and AWS preparation

The executable cloud workflow has materialize, train, score and publish wheel
tasks with a shared bounded CPU cluster. It tracks MLflow runs, can register a
research-only Unity Catalog model version, and conditionally publishes an app
backup and then its manifest to private S3. The Jobs API contract is checked with
the real SDK without credentials. Shared job clusters require a saved job; the
launcher creates an **unscheduled** job, verifies its settings and calls run-now
with a stable idempotency token.

The launcher refuses disabled policy before constructing clients. It verifies
the AWS account and exact approved plan, then atomically reserves the full upper
cost bound in DynamoDB. Concurrent admissions cannot exceed the ledger allowance.
Lost acknowledgments retain the reservation. Job workers can read the ledger but
cannot reserve/refund money. Reconciliation requires stopped compute and final
cost evidence, including retained storage. This is fail-closed admission control,
not a promise that cloud billing cannot exceed an estimate.

Terraform now separates batch resources from the optional app VM and includes
the ledger, scoped batch instance profile, and launcher policy. SDK-level tests
exercise conditional DynamoDB writes, S3 conditional publication and job schema
round trips. No AWS or Databricks resources have been provisioned.

See [the execution guide](../databricks/README.md) for exact prerequisites and
commands. Actual workspace execution, IAM allow/deny probes, two-worker-machine
benchmarks, billing/stop evidence and private AWS deployment remain unverified.
The account-specific plan is deliberately unpriced. **Cloud spending stays $0.**

The previous behavior and Deep CFR results remain valid negative findings. A
better equity network does not satisfy the small-game strategy gate; the Hold'em
neural-strategy extension remains gated.

## Reproduce and inspect

With the local research dependencies and Java 17 installed, use:

```sh
# Open the existing app and measured report.
make demo
# In another terminal: exercise the four batch task bodies on fresh local data.
make verify-cloud-local
make verify-portable-incremental
# Reproduce the larger neural experiment in a fresh directory (up to one hour).
.venv/bin/python scripts/run_river_research.py --data .data/river-reproduction
# Read-only; confirms cloud submission remains disabled.
make cloud-preflight
```

An existing experiment's protocol and log cannot be overwritten. Reproduction
uses the retained prior datasets to exclude previously evaluated visible-card
families; a fresh checkout without them has a different exclusion set and is not
the exact original dataset. The saved protocol records those dataset identities.
The immutable original v3 report is preserved alongside the final source hashes.

Final verification is recorded in `benchmarks/results/ml-cloud-verification.json`.
The main research suite passes 107 tests; the patched Python 3.12 suite passes 95,
and its 20-test cloud/river subset passes. Four desktop/mobile browser regressions,
the production web build, Terraform validation and the Linux serving container
checks pass. The patched compatibility dependencies and frontend audit report no
known vulnerabilities; this is not a complete security audit. Older compatibility
results and their dependency findings remain preserved rather than relabeled.

No paid project resources were started. The temporary local Docker verification
containers and Docker Desktop have stopped; the loopback app is available at
`http://127.0.0.1:8000`. Actual account/workspace access, runtime patch review,
region-specific pricing, approved execution, multi-machine benchmarks and private
AWS deployment remain external gates. No personal histories were uploaded.


## Operational completion pass

The remaining account-independent operational gaps are implemented: scoped run/app
status and emergency stop, lost-acknowledgment recovery without resubmission,
final-cost reconciliation, bounded atomic publication installation, model reports
inside published app snapshots, and Spark event-log benchmark collection. The CLI
cloud-run command now delegates to the guarded launcher only when enabled with an
exact approved plan. Terraform supplies budget notifications and app status alarms,
requires reviewed account/notification configuration, and separates foundation/image
preparation from app startup. No account was contacted or infrastructure applied.

The new workflow restored 256 hands and 338 scores and verified the report in the
restored app. The small smoke model correctly fails its support gate. Collection
read 1,791 actual local Spark task attempts; the comparison correctly remains
incomplete because there is no cloud/multi-machine evidence. Original v3 research
metrics remain unchanged through the shared report formatter.

Checks: 122 primary tests (3 SDK tests skipped here and covered in the compatibility
environment), 111 Python 3.12 tests (2 OpenSpiel modules skipped), 35 targeted
operations/snapshot/policy checks, 4 browser regressions, production build,
Terraform validation and Linux snapshot installation passed. Base isolation passed
93 tests with 11 optional skips. See `benchmarks/results/operations-verification.json`
and the [operations guide](cloud-operations.md) for exact commands and limits.
