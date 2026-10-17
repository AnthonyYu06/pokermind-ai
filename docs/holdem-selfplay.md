# Hold’em self-play research bot

The app now has a complete-hand **NFSP self-play strategy** opponent. Select it
under **Hold’em practice**, then start a hand. **Inspect policy mix** shows the
network's action frequencies; **Let the bot play this turn** samples one of those
actions for your seat. The other seat acts automatically. Cards and chance remain
server-owned, and refresh resumes the saved hand.

**This is an experimental bot, not a general optimal-play Hold’em solution.**
The first experiment failed its declared quality checks. The earlier Deep CFR
failures and existing baseline/default release pointers remain unchanged.

## Learning implementation

The approach is based on [Heinrich and Silver's Neural Fictitious Self-Play
paper](https://arxiv.org/abs/1603.01121). The installed [OpenSpiel PyTorch NFSP
implementation](https://github.com/google-deepmind/open_spiel/blob/master/open_spiel/python/pytorch/nfsp.py)
was inspected as a reference; this project contains its own bounded implementation.

Each player has a Double DQN response network, a target network and a separate
average-policy network. An anticipatory mixture chooses response versus average
play at the start of each episode. All trajectories enter bounded reinforcement
learning replay; only response-policy state/action samples enter the uniform
supervised reservoir. The average network learns those sampled actions with
masked cross-entropy. Huber temporal-difference loss, target updates and gradient
clipping stabilize response learning. Serving samples the learned **average
policy**, rather than treating the largest probability as a certified best action.

Transitions connect consecutive decisions by the same player. Terminal utility is
the change from starting stack, normalized by the 50-BB stack in Hold’em. There is
no discounting or terminal bootstrapping. Each player's fold still receives its
terminal loss. Resume checkpoints retain both agents, optimizers, target weights,
replay, reservoir, counters and independent random states at episode boundaries.

The 630-dimensional visible-state encoding contains the existing 45 public/own-card
features, up to 64 public action tokens, a legal-action mask, and a frozen river
equity feature plus availability indicator. The river feature is the mean of the
three previously trained structural networks. Before the river its value is 0.5
with an unavailable indicator. No current opponent cards, future board or future
actions are included. The card/history representation and bet choices are lossy
abstractions; perfect recall or general equilibrium convergence is not claimed.

## Supported simulation

- Two players, 50-BB starting stacks, blinds 0.1/0.2 simulated chips, no rake.
- Full preflop, flop, turn and river rules, including all-ins and refunds.
- Fold, check, call and deduplicated legal half-pot, pot, double-pot and all-in
  targets. Human moves in this mode use the same bet-size abstraction.
- Generated hands only. Policy inspection and bot actions require a stored game
  ID and matching revision. The API accepts no external table state or cards.
- Models and frozen river features are checked by SHA-256; existing games retain
  their pinned policy when the research pointer changes.

Action frequencies are **not win probabilities, confidence scores, or action
values**. No six-max, variable starting stacks, rake, tournament ICM, unrestricted
bet sizing, real-opponent prediction or platform integration is established.

## First measured experiment

The protocol and source hashes were saved before training. Seeds 101, 202 and 303
each ran 10,000 Kuhn episodes and 6,000 Hold’em episodes: **30,000 small-game and
18,000 full-hand training episodes**. Networks have two 96-unit hidden layers.
The final checkpoint of the first declared seed is the opt-in simulator artifact;
benchmark outcomes do not select a seed or checkpoint.

Each Hold’em seed was evaluated against six fixed synthetic opponents using 400
separate evaluation deal seeds with seats swapped. That is **14,400 benchmark
hands**, reusing the same 400 deal seeds across opponents and model seeds. The
training and evaluation RNG seeds differ; this is not a claim that all observable
card patterns are unique across them. Intervals are approximate 95% intervals
over paired deals for each seed/opponent, not confidence bounds on GTO performance.

| Opponent | Mean BB/hand across three model seeds |
|---|---:|
| Uniform legal actions | 1.7106 |
| Passive check/call | -0.4792 |
| Cautious synthetic policy | 1.3854 |
| Pressure synthetic policy | 1.5754 |
| Responsive synthetic policy | 3.2617 |
| Mixed synthetic policy | -0.4667 |

These means are descriptive. Many individual intervals span zero, and the
requirement for a positive lower interval against every opponent in every seed
**failed**. Beating these fixed baselines would not establish low exploitability.

Kuhn exploitability was **0.2170, 0.1732 and 0.1770** native game chips per hand,
against the predeclared maximum 0.01. The exact information-set evaluator agrees
with the independent OpenSpiel evaluator. This control also **failed**; no Hold’em
exploitability certificate was computed. Model promotion remains disabled.

Evidence: `benchmarks/results/holdem-nfsp-v1-{protocol,report,summary}.json` and
`holdem-nfsp-v1-training.txt`. Checkpoints and MLflow runs remain local. The policy
selected for simulation has SHA-256
`e987961d283be14db1173dea32bcbd7ad5d5741f035ac465368c2101445a49c4`.

## Reproduce locally

The research dependencies, prior river experiment and `make prepare-coach` are
prerequisites. The run uses two CPU threads, a 30-minute compute/evaluation bound
and bounded replay buffers. No cloud account, upload or spending is involved.

```sh
make train-holdem-selfplay
# Or choose a new directory explicitly:
.venv/bin/python -m training.holdem_nfsp --output .data/models/holdem-nfsp/NEW-RUN
# Resume only with the unchanged protocol, sources and frozen river models:
.venv/bin/python -m training.holdem_nfsp --output EXISTING-RUN --resume
```

Checkpoints are written every 2,000 episodes; an interrupted run can lose work
after its last checkpoint. The local report pointer is published only when all
training and evaluation stages complete. A complete run is not retrained by
resume. No thresholds are changed after inspecting results.

To install checked inference artifacts in another local workspace:

```sh
.venv/bin/python scripts/install_nfsp_research.py --source .data --destination NEW-WORKSPACE
```

This copies policies, frozen river weights and the report, not replay buffers.
Application backup/restore similarly excludes NFSP training checkpoints while
retaining saved games and required inference artifacts. The original workspace
retains training checkpoints for research recovery.

Next research work would require a new declared experiment: stronger small-game
convergence, capacity/data sweeps, feature ablations, stronger opponents, and
independent strategy-quality evidence. Existing favorable equity metrics do not
substitute for those strategy checks. The new NFSP experiment is local CPU work;
no Databricks run or distributed training result is claimed.
