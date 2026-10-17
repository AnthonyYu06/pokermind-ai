# Larger local research — October 16, 2026

All execution used this Mac. Authorized cloud spend remains $0. No personal
histories, remote workers or paid services were used. The protocol is
`configs/local-research-v2.json`; original source hashes are retained in
`benchmarks/protocols/local-research-v2-source.json`. Model settings and gates
were fixed before their runs. Benchmark settings were added before benchmark
runs. Subsequent source corrections are described below rather than rewriting
the original protocol evidence.

## Run and inspect

```sh
make setup-research
make research-local
make demo
```

The Research lab shows the larger comparison alongside the earlier experiments.
`research-local` runs the pipeline, models, behavior and both strategy games,
benchmarks, scoring and evidence export sequentially. It is local-only, uses two
CPU threads per training/Spark job, checks for 6 GiB free space and a 2 GiB suite
workspace before each task, and enforces a two-hour suite timeout. Interrupt or
timeout terminates the owned process group, including JVM workers. Logs and the
completed-task ledger are in `.data/research-local-v2/logs`. The measured session
used individually launched bounded commands; the timeout wrapper was added after
those runs. These limits are not a cloud budget mechanism.

The larger review library is separate from the everyday 304-hand demo workspace:

```sh
.venv/bin/python -m services.api.cli --data .data/research-local-v2/equity serve --port 8001
```

Open `http://127.0.0.1:8001` and select a river decision to inspect exact equity
and its separately labeled research estimate. Scoring used the training report's
frozen manifest, even after the benchmark published newer lake versions. Exactly
7,395 records were scored; no neural model became a default. `make
export-local-research` regenerates the checked summary and figures without training.

## Dataset and leakage audit

All histories were internally generated with fixed seeds. The equity generator
produced 6,000 hands, 36,682 decisions and 7,395 exact river labels. Labels describe
heads-up showdown pot share against a uniform legal opponent, with no betting EV.
Frozen split counts are 5,038 train / 1,556 validation / 801 test decisions and
2,232 / 700 / 358 independent deal families. All exact feature collisions across
split boundaries are excluded. The independent audit found zero cross-split
family, MLP-feature and canonical card-feature overlap.

Behavior generated 8,000 trajectories across cautious, pressure, responsive and
held-out mixed policies. Retained examples total 21,349 train / 4,720 validation /
2,300 test / 6,119 unseen-policy decisions. They represent 3,728 / 911 / 453 / 1,619
independent families. The unseen policy is excluded from training and validation.
The audit found zero cross-split family or exact feature/prefix/mask overlap.

**Audit correction:** the initial behavior report counted 585 generated test
families, including families with no retained examples. Only 453 appear in the
frozen test shards. `local-gate-audit.json` records the correction and the original
report checksum. The original run is retained unchanged. Future training counts
retained examples directly, with a regression test. The corrected support gate
fails; predictions, thresholds and split membership were not changed.

The 500-family support threshold fails for both datasets. Test results were not
used to select more data, change thresholds or tune another model. No real-data
generalization result exists because no verified real export was supplied.

## Equity results

Values below are held-out MAE, mean ± population standard deviation over seeds
11, 23 and 47. Full reports also contain RMSE, signed bias, reliability bins,
500-resample family-cluster bootstrap intervals, training curves, model sizes
and inference timings. Bootstrap intervals condition on this synthetic domain.

| Model | All training families | 25% fixed training subset |
|---|---:|---:|
| Compact MLP | 0.11823 ± 0.00450 | 0.14857 ± 0.00432 |
| Card set, width 64 | 0.17007 ± 0.00556 | 0.24638 ± 0.00118 |
| Card set, width 16 | 0.22537 ± 0.01105 | 0.25387 ± 0.00351 |

Full-training baselines: constant 0.26467, training mean 0.26470, ridge 0.16966,
histogram boosted trees 0.11814. The 25% rows measure neural data sensitivity;
baselines were fit to the full training set. The deeper encoders did not justify
their added complexity here. All models fail the 0.05 MAE, 10% gain over the best
baseline, and independent-support gates. Validation selected checkpoints; test
scores did not select the serving seed.

The card encoder canonicalizes joint private/public suits, embeds each card and
pools hero/board sets independently. Order/suit invariance, impossible cards,
small-batch overfit and exact resume were tested. Its design follows the
[Deep Sets paper](https://papers.nips.cc/paper_files/paper/2017/hash/f22e4747da1aa27e363d86d40ff442fe-Abstract.html).
It supports river states only, so padding/earlier-street coverage is outside its
contract. No future or opponent private cards enter prediction features.

## Behavior and strategy results

| Behavior model | Test NLL | Unseen-policy NLL |
|---|---:|---:|
| Frequency | 1.09272 | 1.35840 |
| Logistic, no sequence | 1.076 ± 0.001 | 1.364 ± 0.004 |
| GRU, width 32 | 1.060 ± 0.001 | 1.404 ± 0.020 |
| GRU, width 8 | 1.060 ± 0.001 | 1.390 ± 0.007 |

Frequency remains the default. All classes have the required support, but
independent-family support, relative quality and unseen-policy gates fail. The
full report includes Brier score, ECE/reliability bins, action support and
sequence-length/policy strata. NLL is in nats; lower is better. This predicts a
synthetic data policy, not optimal poker play.

Deep CFR used 100 iterations, 400 traversals per player per iteration, three seeds,
60,000-capacity reservoirs, width 128 and weighting/reset ablations. Exploitability
is NashConv/2 in each game's chips per hand, computed against an independent exact
OpenSpiel best response. The tested games do not establish Hold'em competence.

| Game | Main Deep CFR | No iteration weights | No network reset | Tabular |
|---|---:|---:|---:|---:|
| Kuhn | 0.01078 ± 0.00166 | 0.02339 ± 0.00833 | 0.02136 ± 0.00928 | 0.000938 |
| Leduc | 0.41543 ± 0.04049 | 0.36804 ± 0.06841 | 0.31143 ± 0.00807 | 0.053838 |

Both fail promotion. The tabular policies remain defaults, and the Hold'em neural
strategy extension remains blocked by the required small-game quality gate.
An ablation beating the main run does not justify changing the predeclared model
after testing. Curves against traversals and time are retained in CSV and reports.

## Local execution benchmark

One warmup and three measured repetitions per setting used identical unique hands,
observations, exact labels and Gold counts. Every output hash comparison passed.
The Mac had 12 logical CPUs and 18 GiB RAM. Spark used `local[2]` in both settings;
four partitions do not mean four machines or four simultaneous cores.

| Execution | Mean seconds ± SD | Measured repetitions |
|---|---:|---:|
| Streaming Python, one process | 74.793 ± 0.185 | 3 |
| Spark, two partitions / two local cores | 51.155 ± 2.737 | 3 |
| Spark, four partitions / two local cores | 49.377 ± 0.447 | 3 |

The Python implementation materializes JSONL snapshots; Spark materializes Delta
and frozen Parquet and performs reference validation. Timings include those format
and transaction costs, idempotent import and warm filesystem effects. Other local
research overlapped part of the measurements. These are implementation comparisons,
not pure scaling efficiency, multi-worker speedup or a cloud cost crossover.
Raw reports retain stage/task time, shuffle/spill, partition rows, event-log IDs
and Python-process high-water RSS. RSS excludes the JVM and is not total job memory.

At 6,000 hands, warmed in-process API p95 across three measurements was 11.81 ms
for a hand page (target 250), 2.67 ms for imports (250), 34.26 ms for sessions
(1,000), and 3,014.53 ms for the overview (5,000). Three measurements provide only
an exploratory latency estimate, not a production SLO. Response sizes remain bounded.

The recovery rerun passed: a failure after Silver preserved the old publication,
retry extracted eight new hands, full and incremental results matched, and
quarantining one hand retracted its rows. No atomic multi-table transaction is claimed.

Metal checks passed CPU forward parity and four finite optimizer steps for the
MLP, card set and GRU. CUDA was unavailable. This is operation compatibility only;
all reported quality experiments ran on CPU. See `device-compatibility.json`.

## Evidence

Post-training slices and single-state inference timings are recorded separately
in `local-inference-slices.json`, using the pre-timing definitions in
`configs/local-inference-evaluation.json`. They did not drive tuning. Full-test
MLP MAE averaged 0.14532 on four-or-more-same-suit boards (26 decisions / 13
families), and 0.29374 when the hero had a straight or better (74 / 62). These
small slices reinforce the lack of a promotion-quality result.

On 40 fixed unique test card states, mean median latency across three measured
repetitions (one warmup excluded), including encoding and single-state inference,
was 10.462 ms for exact enumeration, 1.041 ms for 100-draw Monte Carlo, 10.909 ms
for 1,000 draws, 0.031 ms for the MLP, 0.075 ms for the card set, and 0.064 ms for
the small card set. Corresponding subset MAE was 0, 0.04300, 0.00876, 0.09089,
0.15078 and 0.22427. Model seed 11 was fixed before timing; artifact loading is
excluded. This 40-state subset is for speed/error diagnosis, not a replacement
for full-test results. Exact enumeration remains practical and more accurate.

`benchmarks/results/local-research-summary.json` and `local-research-report.png`
summarize the experiment. `local-research.json`, `local-behavior.json`,
`local-strategy-kuhn.json`, `local-strategy-leduc.json`, curve CSVs,
`local-split-audit.json`, `local-gate-audit.json`, `local-pipeline-comparison.json`
and `local-app-latency.json` retain measurements. Frozen data, weights, MLflow and
Delta files stay under ignored `.data/research-local-v2`. The app uses a checksummed
summary under `.data/evidence`; publishing it does not change trainer policies.
