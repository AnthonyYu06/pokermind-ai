# Verification and remaining gates

Updated October 16, 2026. The local import/replay, data platform, research and
trainer slices are implemented and measured within their stated domains. The
full master project is not complete: actual Ignition compatibility, executed
cloud/distributed work and private deployment remain separate gates. Authorized
cloud spending is **$0**, personal uploads are disabled, and no paid resources run.

## Current local evidence

- **138 tests passed, 3 SDK tests skipped** in the main Python 3.13 research
  environment, with one Starlette/httpx deprecation warning. The skipped SDK tests
  passed in the separate compatibility environment.
- **111 tests passed, 2 OpenSpiel-dependent modules skipped** in the patched Python
  3.12.15 compatibility environment. Its targeted cloud/operations/snapshot/policy suite passed all
  **35 tests**, including real SDK schema serialization and AWS request stubs.
- The base-runtime isolation check passed **100 tests, with 14 optional tests
  skipped** while research dependencies were deliberately unavailable.
- TypeScript/Vite production build passed. A startup check now tolerates an
  incomplete web build without preventing API construction.
- Source secret-pattern and disabled-cloud policy checks passed. The current npm
  dependency audit, including Playwright, reported zero advisories. The older
  Python 3.12 compatibility stack had upstream dependency advisories. After
  updating its affected packages, the audit found zero known vulnerabilities in
  its third-party dependencies; the local project wheel is not a PyPI audit target.
  See the runtime matrix for the patched rerun. These are not full security audits.
- Actual local Spark/Delta pipeline, 12 benchmark runs (3 warmups + 9 measured),
  7,395 scored records, and failure/retry/retraction verification passed.
- Three-seed v2 equity, card-set, behavior and Deep CFR experiments completed. The
  v2 neural promotion gates failed. The independent audit corrected retained
  behavior test support from 585 generated families to 453 represented families.
- The independent v3 residual equity experiment passes support, accuracy/bias and
  baseline-gain gates across three seeds: MAE 0.032568 versus boosted 0.042021 on
  1,536 independent test states. Spark produced 15,998 predictions and a separate
  batch added the new neural comparison to 323 eligible sample decisions. Exact
  reference equity remains the default. See [ML/cloud progress](ml-cloud-progress.md).
- The complete cloud task implementation passed locally under Python 3.12 /
  Spark 4.0 / Torch 2.7 / MLflow 3, including model registration and restoration
  of 256 hands / 338 scores. The content-versioned batch wheel imports outside
  the checkout. No managed Databricks runtime or AWS access is claimed.
- Portable full/incremental/overlap identities match. Measured Delta compaction
  reduced an isolated copy from 8 files to 1 while preserving pinned version 0.
- The Linux/amd64 serving container builds from pinned official base digests and
  passes its production-entrypoint health, frontend and replay checks under a
  nonroot user, read-only root and disabled external network.
- MPS forward/optimizer checks passed for three model families; CUDA was unavailable.
- Browser checks covered queued fixture upload/commit, import diagnostic paging
  and filtering, weighted river equity, bounded action EV, research reports and
  snapshot creation/activation/rollback. Desktop 1280px and phone 390px layouts
  had no horizontal document overflow. Existing broader replay/trainer/privacy
  browser evidence is retained in `docs/screenshots`.
- Eight automated desktop/mobile browser regressions pass for import
  preview/commit/provenance, the v3 research report, the complete-hand NFSP opponent and the AI practice coach with
  the actual three-network bundle. Two additional coach browser checks cover
  reference-only mode. Screenshots were visually checked; wide research tables
  scroll within their panel on a narrow screen.
- The [AI practice coach](ai-practice-coach.md) passes independent exhaustive
  showdown/action-value checks, model integrity/pinning, hidden-card isolation,
  generated-input boundaries, persistence and backup/restore. A 256-drill diagnostic
  measured 94.9% neural/exact ranking overlap under the simplified passive opponent,
  with meaningful errors (maximum 6.129 BB gap). No general strategy claim is made.
- The [full-hand NFSP experiment](holdem-selfplay.md) completed three seeds,
  18,000 Hold’em training hands, 30,000 Kuhn control hands and 14,400 simulated
  benchmark hands. Both measured quality gates failed; it remains an opt-in
  research opponent. Checkpoint parity, terminal credit, legal masking, hidden-card
  isolation, strict game/revision boundaries, model pinning and backup/restore pass.

Raw logs and reports are under `benchmarks/results`. The browser upload used an
isolated synthetic fixture workspace, whose upload correctly received real
provenance. Primary histories were not deleted or replaced. Browser verification
resumed after the user reopened a stale tab blocked by browser URL policy.
Coach evidence is in `benchmarks/results/river-coach-verification.json` and
`river-coach-diagnostic.json`. The earlier cloud operations, container and compatibility
checks remain recorded in `benchmarks/results/operations-verification.json`;
they were not rerun as cloud deployments for this local feature.
The latest self-play implementation checks and source hashes are recorded in
`benchmarks/results/nfsp-verification.json`; the measured training report is
`benchmarks/results/holdem-nfsp-v1-report.json`.

## Completion checklist

| Area | Evidence | Status |
|---|---|---|
| M0 local foundation | Doctor, lockfiles, base/research checks and production UI | Verified locally; remote CI unexecuted |
| M1 import/replay | Canonical fixtures, engine/evaluator, overlap/revision/time-gate tests | Verified within canonical NLHE scope |
| Durable import workflow | Preview/commit queue, cancellation rollback, interrupted retry, queue cap | Verified locally |
| Paged browsing and privacy | Bounded imports/diagnostics/hands/sessions, retention/export/delete/backup | Verified locally |
| Actual Ignition text | No sanitized official export supplied | Unsupported/unverified |
| Historical range/action analysis | Exact/MC weighted ranges, blocker checks, paired zero-rake passive rollouts | Verified in documented heads-up domain |
| M2 local data/ML workflow | Spark/Delta to frozen shards, three-seed models/MLflow, batch scores in app | Executed locally |
| Larger local pipeline comparison | Same output hashes; one process vs two local Spark cores, 2/4 partitions | Measured; not multiple machines |
| Larger app performance | 6,000-hand corpus, all predeclared page/overview targets pass | Local exploratory measurements |
| Fresh v3 residual equity | Three seeds, 1,536 independent test states, strong baseline comparison | Synthetic gates passed; exact reference retained |
| Portable cloud job implementation | Four task bodies, local MLflow registry, app snapshot restore, SDK/budget tests | Verified locally on Python 3.12/Spark 4.0; cloud unexecuted |
| Incremental and file-layout maintenance | Full/overlap equality, 8-to-1 compaction, pinned reader preserved | Verified locally; no VACUUM |
| M3 behavior/strategy | Larger GRU/logistic/frequency and Kuhn/Leduc Deep CFR plus ablations | Executed; neural unpromoted |
| Trainer safety | Server-owned state/chance, pinned models, legal actions and refresh/resume | Tested |
| AI practice coach | Three-network river equity, exact action comparison, generated drills and persisted feedback | Verified locally within the passive uniform-opponent teaching model |
| Full-hand self-play bot | NFSP training, frozen equity feature, paired simulated benchmarks and simulator integration | Implementation verified; strategy quality gates failed; no GTO claim |
| Model governance | Checksummed snapshots, activation/rollback, corruption refusal, backup/restore | Local baseline/report governance verified |
| Hold'em neural strategy extension | Small-game quality gate failed | Intentionally gated |
| Cloud operations and billing | Account/job/reservation-scoped status/stop, lost-ack recovery, final-cost reconciliation | Local contract and SDK stub tests pass; cloud unexecuted |
| Published snapshot deployment | Digest/size/path/SQLite checks, atomic install, report display, Linux image | Verified locally; real private deployment unexecuted |
| Spark benchmark collector | Real event-log metrics, immutable stage lineage, incompatible/local comparison refusal | Executed locally; no distributed speedup claimed |
| M4 cloud execution | Job/adapter and admission controls implemented; no account-specific priced run approved | Unexecuted; spending disabled |
| M5 distributed benchmark | No worker machines provisioned | Unexecuted |
| M6 private deployment | Linux image and local recovery tested; no remote access checks | Unexecuted |

Model failure does not erase the completed experiments, and completed experiments
do not establish promotion quality. See [larger research](local-research.md) for
all results, support counts, curves, latency and interpretation. The
[operations guide](cloud-operations.md) documents the completed local preparation
for cloud stopping, publication installation, billing alerts and benchmark collection.

## Reproduce

```sh
make demo
make test
.venv/bin/python scripts/check_base_runtime.py
.venv/bin/python scripts/security_check.py
make research-local
make verify-recovery
```

`make demo` serves `http://127.0.0.1:8000`. The already measured larger corpus can
be opened separately with:

```sh
.venv/bin/python -m services.api.cli --data .data/research-local-v2/equity serve --port 8001
```

Stop local servers with Ctrl-C. `research-local` enforces a two-hour timeout and
local disk/workspace checks; it never starts cloud resources. The actual measured
session launched components individually before that wrapper was added; timeout
cleanup is separately tested with a real local subprocess.

## Limits and next external gates

A sanitized official export with intact structure/times/summaries is required to
implement and verify actual Ignition text parsing. Current revised conflicts stay
quarantined; unknown dates cannot be guessed. Tournament ICM, Omaha, straddles,
multiple boards, arbitrary-state recommendations and multiway action EV remain
outside the supported contract. Descriptive bb/100 intervals state their
hand-independence assumption; they are not session-clustered confidence guarantees.

Before any paid work: resolve account/region/workspace/runtime, validate the locally
tested batch wheel and serving image on the actual target, prepare official region-specific
costs and stop/retention details, then obtain authorization for that concrete plan.
`cloud-run` continues to refuse execution. No CI, deployment, remote model
promotion, multi-machine scalability or real-player benefit is claimed here.

The exact reference remains the default alongside the new neural research
comparison. A future improvement experiment needs a fresh protocol and independent
evaluation; it must not tune against these final test results or weaken the gates.
