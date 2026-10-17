# AI practice coach

Open **AI practice coach** in the local app. Deal a generated river hand, choose
one of its legal actions, or select **Compare all actions**. The result highlights
the highest estimated value, shows every compared action, and preserves the drill
and first comparison across reloads. All chips are simulated.

The neural path runs the three existing structural residual networks locally on
CPU. Their mean predicts showdown equity; seed spread and each member prediction
remain visible. The screen also enumerates all 990 possible opponent hands for an
exact reference. The model does not receive the generated opponent's private cards.

## Scope and calculation

This is a deliberately constrained teaching example: two players, river, zero
rake, a uniform opponent range, and an opponent that always checks or calls after
your action. Prior generated betting is independent of private cards. Available
choices include fold/check/call and legal, deduplicated half-pot, pot, double-pot
and all-in targets. It does not search every possible bet size.

For each candidate the rules engine completes the passive betting response and
refunds unmatched chips. If both players remain, expected value is
`remaining_stack_after_response - remaining_stack_before_decision + equity * pot`.
A fold uses its deterministic settlement. Values are divided by the big blind.
Previous contributions are sunk, and a positive value can include winning chips
already in the pot. This is expected future stack change, not final session profit.

Large bets frequently lead under this always-call assumption. Those choices do not
establish an equilibrium policy or a good action against an opponent who folds,
raises, or has a different range. The UI exposes disagreement with the exact
reference, disagreement across network seeds, and whether a ±5 percentage point
equity perturbation changes the best choice. That perturbation and the seed spread
are **not confidence intervals**. Equal-value actions remain ties. Choice feedback
uses the exact reference gap under the same toy opponent, not a general mistake label.

Earlier streets, multiway pots, rake, opponent-specific ranges and general Hold'em
strategy are outside this coach's model contract. The existing full-hand simulator
and historical review features remain separate. No model promotion is performed.

## Local preparation

With the completed v3 experiment retained locally:

```sh
make prepare-coach
make build
.venv/bin/python -m services.api.cli serve
```

`prepare-coach` verifies all three structural weight hashes and their declared
synthetic equity gates. It installs content-addressed artifacts beneath
`.data/models/river-coach/` and atomically updates `.data/river-coach.json`.
Existing drills pin their original manifest, so a later preparation cannot change
their model selection. This opt-in bundle does not change historical-review
defaults or active release pointers. It uses no cloud services.

The default source is `.data/research-river-v3/models/report.json`. To use another
completed experiment or another workspace:

```sh
.venv/bin/python scripts/prepare_river_coach.py --report PATH/models/report.json --data WORKSPACE
```

PyTorch is an optional research dependency. With no bundle or no PyTorch, generated
drills still work and explicitly show **Exact reference only**. Corrupt pinned
weights abstain from neural inference rather than inventing a prediction. A broken
top-level bundle pointer prevents starting a new pinned drill until repaired by
rerunning the preparation command. Application backup/restore includes the drill
table, model artifacts and pointer.

The API accepts only an empty start request and a generated drill ID with an
optional legal action label. It has no caller-supplied cards, seed, table state,
screenshot or external-game feed. Same-origin protections apply.

## Measured diagnostic

`benchmarks/results/river-coach-diagnostic.json` records 256 reproducible generated
drills (seed base 1,760,000), using the already trained models without retraining
or selecting new weights. Ensemble equity MAE was **3.30 percentage points**.
The leading neural action set overlapped the exact best set in **243/256 (94.9%)**
drills. The mean worst tied-choice gap was **0.086 BB**, with a maximum **6.129 BB**.
There were **16** disagreements across network seeds and **37** rankings sensitive
to the ±5 point check. These are exploratory toy-opponent diagnostics, not an
independent strategy-validation gate or evidence of real-play performance.

Reproduce to a new output path:

```sh
.venv/bin/python scripts/verify_river_coach.py --output NEW-RESULT.json
```

Integration tests independently settle every opponent combination through the
rules engine for several generated states, checking action values including
unmatched-chip refunds. Additional tests cover hidden-card independence, ties,
real three-network inference, artifact corruption after caching, model pinning,
missing-model fallback, strict input boundaries, persistence and backup/restore.
Desktop and mobile browser tests exercise both neural and reference-only modes.
