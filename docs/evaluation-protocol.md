# Evaluation protocol v1 — declared before the first training run

The first neural experiment is a compact MLP trained on exact heads-up river
showdown equity against a uniform legal random opponent. Labels enumerate all
990 legal opponent combinations. Ties receive one-half pot share. This is not
action EV, a solver or a betting recommendation.

## Splitting and features

Canonicalize the complete deal (both players' hole cards and board) over all 24
suit permutations. Hash that family into 70% train, 20% validation, 10% test.
Repeated decisions in a hand stay together. Only actor cards and current board
enter the encoder. Opponent cards can determine group membership but never the
encoded features. Check exact feature-equivalent observations do not cross splits.
The rank counts and sorted suit summaries are invariant to card and suit order.
All scalar baseline statistics and ridge coefficients fit on the training split.

## Tiny experiment

Generate 256 unique heads-up trajectories plus the 48 six-max demo hands.
Only river decisions with two active players and known actor cards get labels.
Compare 0.5, training-mean, ridge (fixed L2=1), and MLP (55/64/32/1) predictions.
Train CPU seeds 11, 23, 47, up to 100 epochs, Adam 0.003, batch 64. Select the
checkpoint on validation MAE, early stop after 20 epochs without improvement.
No test-driven tuning. Report MAE, RMSE, residual bias, five reliability bins,
mean/std across seeds, gradient diagnostics and loss histories. These are
exploratory smoke results; repeated decisions are not independent observations.

## Promotion gates (none waived for a small demo)

- At least 500 independent held-out deal families.
- Each seed's test MAE <= 0.05 and absolute bias <= 0.03.
- Mean MAE at least 10% lower than the best simple baseline.
- Checksummed artifact, identical reload scores, correct schema and domain.
- Independent label/evaluator checks and split audit pass.
- Per-street, rare-state and out-of-distribution evaluations before widening scope.

Tiny data cannot pass the support gate. The app retains direct reference equity.
A failed neural model remains an inspectable research artifact. No claim of
"improved accuracy" is allowed unless all comparisons support that exact claim.

## Subsequent local evaluation

The [larger local protocol and results](local-research.md) add boosted trees,
card-set encoders, capacity/data-scale ablations, family-cluster intervals, actual
pipeline repetitions, inference timings, rare-state slices, MPS operation checks,
behavior models and independent Kuhn/Leduc references. All neural promotion
gates remain failed. The independent audit corrected behavior support to count
families with retained examples; raw runs remain unchanged.

Broader streets/ranges and real-data domains remain outside the neural contract.
CUDA, distributed training and remote runtime behavior are unverified. Expanding
scope requires a newly declared experiment with independent evaluation; failed
test results are not grounds to lower the existing gates.

## Independent river v3 follow-up

`configs/river-research-v3.json` was saved before generation/training. Six residual
network fits (two encodings × three new seeds) finish before final test evaluation.
Validation selects checkpoints and the candidate. Exact-label family splitting,
prior-family exclusions, same-input classical baselines, unchanged quality/support
thresholds, bootstrap intervals, signed residual bias and calibration bins are
retained. The later fixed 40-state inference timing and prior-policy shift checks
are labeled post-training diagnostics and did not tune the model or thresholds.
See `docs/ml-cloud-progress.md` and `benchmarks/results/river-v3-*.json`.
