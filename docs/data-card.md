# Synthetic dataset card v1

Source: PokerMind `legal-baselines-v1`, generated locally with Python seeded random
decks and legal-action sampling. No external corpus or personal history. Intended
use: replay smoke tests, reference equity labels and exploratory model training.

Demo: seeds 2026–2073, 48 six-max hands. Research: seeds 46000–46255, 256 heads-up
hands. Stack sizes sampled from 1,000/2,000/3,000 cents, 10/20 blinds, no rake.
Policies probabilistically check/call, fold or make a bounded half-pot raise. They
are baseline behaviors, not competent poker or a representation of Ignition play.

The first pipeline produced 304 unique hands, 2,512 decision observations, 486,029
input JSON bytes and 323 eligible exact river labels. Label counts: 223 train,
61 validation, 39 test. Repeated actions from the same deal are not independent
statistical samples. Frozen paths/checksums and Delta versions live in the manifest.

Leakage controls: actor cards/current board only; no revealed opponent cards,
terminal results or future action prefix. Suit-canonical full-deal family splitting
keeps both players and repeated decisions together. No feature fitting on held-out
data. Rank/suit set features are invariant to card order and suit names.

Exclusions: missing actor cards, non-river or multiway decisions, ineligible or
inconsistent imports. Only internal synthetic provenance enters the first training
profile. Exact labels enumerate 990 legal random-opponent combinations; no label
Monte Carlo noise. Opponent behavior/range mismatch remains a substantial bias.

Limitations: narrow stakes/stack/rule/policy coverage; showdown survivorship and
river-only selection; very small held-out sample; no real-vs-synthetic validation.
This card is not evidence about any real player's results.

## Incremental snapshot recovery

`make pipeline-incremental` compares the committed source index and extracts changed
synthetic records only. Spark reconstructs and labels changed hands, retains rows
from explicit previously published Delta versions, and retracts missing/quarantined
IDs. Full streaming parity verification is still performed and included in runtime.
A workspace lock rejects concurrent pipeline writers. Partial table commits remain
unpublished; retry reconstructs from the last coherent pointer. Frozen shards are
written to a staging directory and renamed before the manifest is published.
Dataset identity includes source index, transformation source hash, eligibility,
feature, label and split versions. Old training manifests are never rewritten.

`make verify-recovery` uses a new isolated synthetic workspace each run. It tests
Silver-stage interruption, retry, full/incremental equality, and quarantine
retraction. Its evidence is saved in `benchmarks/results/pipeline-recovery.json`.
These tests do not establish all failure scenarios, cloud behavior, or speedup.

## Larger synthetic profiles

The October 16 run generated 6,000 equity trajectories and 8,000 behavior
trajectories under fixed independent seeds. Exact frozen identities, retained
split counts, exclusions, independent-family audit and the behavior support-count
correction are in [local research](local-research.md). No real or mixed dataset
was created. The data remains synthetic and subject to generator/range and
river-selection bias, regardless of its larger row count.

## Uniform river research v3

A new seed (946000) generated 16,000 distinct synthetic hero/board states. Families
canonicalize hero/board card order and suit permutations. Visible-card families
from both prior frozen equity datasets were excluded; cross-split identical
features were removed. Final counts: train 11,355, validation 3,107, test 1,536.
The test set has 1,536 independent families. Exact enumeration provides all
labels; no opponent cards or equity labels enter the structural encoder.
This uniform card-state distribution differs from reached game states and real
play. No personal histories were used or uploaded. New training and scoring
manifests retain separate identities in historical review. The source snapshot
and frozen shard checksums are in the v3 report and pre-run protocol artifacts.
