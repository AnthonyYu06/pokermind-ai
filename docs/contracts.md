# Contracts v1

Executable hand contract: `packages/pokermind_core/contracts.py`. Extra fields are
rejected. `pokermind-canonical-v1` is **not an Ignition format**. UTF-8, optional BOM,
one whole hand per line, LF/CRLF. Bounds: 10 MiB/file, 128 KiB/record, 10,000 records,
20 seconds of parsing. Processing is bounded to the selected file, never the corpus.

File IDs hash original bytes + source kind + parser version. Hand IDs hash source
kind + session ID + source hand ID. Record hashes cover canonical content.
Conflicting revisions quarantine all versions; automatic correction selection is
not implemented. Original names never become filesystem paths.

| Entity | Key and implemented content |
|---|---|
| ingest_files | file_id; original hash/name, source, parser, import time, policy/status/report |
| import_records | file_id + line; byte range, status/reason, canonical record/hash |
| hands | hand_id; source/session IDs, timestamps/timezone/precision/end evidence, rules, blinds, stacks, cards, events, summary, rake, quality, hero |
| decision_observations | hand_id:action_index; actor, actor cards only, current public state, prefix, legal bounds, feature version/hash |
| analysis_results | decision_id; reference model, data hash, method/budget/uncertainty/time |
| study_queue | decision_id; due date, attempts |
| practice_sessions | server attempt_id; decision, timestamps, response, confidence, latency, feedback |
| audit / schema_migrations | record action/time; schema version/time |
| Delta Bronze | hand_id; canonical validated row, source/session, hash and declaration |
| Delta Silver | decision_id; reconstructed observation, separate next-action target, deal family, split |
| Delta Gold | source/street/action counts |
| Delta labels | decision_id; features, exact equity, sample count, label SE and split |
| dataset manifest | immutable shards/checksums, Delta versions, feature/split/policy versions |
| model experiment | MLflow run ID, seeds, dataset identity, checksummed model/checkpoint, metrics |

Amounts are integer USD cents. `Event.to` is total contribution on the current
street, not the raise increment. All-ins are bets/raises/calls exhausting a stack.
Engine-posted blinds precede events. Boards are explicit. Terminal source awards,
stacks and rake must exactly reconcile; source summaries are never rewritten.
After refunds: contributions = awards + rake. Observations exclude future actions,
future boards, opponent hidden cards, winners and terminal stacks.

The migrations below implement local projections and governance. Production
distributed-score entities and an executed cloud model registry remain unverified.

## Local schema v2

Migration 2 backfills `sessions` (source-scoped session key), `actions` (hand/action
index key, pre-action street, actor, kind, exact raise-to and deal cards) and
`retrospective_outcomes` (hand/seat, initial/final stack, net and award). Migration
and new import projections are transactional and idempotent. Reconstruction remains
the authority. Invalid/conflicting hands cannot reach review, even when old
projection rows are preserved for audit. Outcomes are not model inputs.

`simulator_games` and `holdem_games` persist app-generated states, policy path/hash,
server chance seed and monotonic revision. Clients submit only a legal action and
the expected revision. The response excludes opponent cards, RNG and raw state.
These games are separate from imported histories and never acquire real provenance.

## Migration 3 and browsing

`data_settings` stores abandoned-preview retention; `pending_file_deletions` makes
original cleanup durable after transactional deletion. See [data controls](data-controls.md)
for scope and exclusions. `/api/hands` returns `items`, `total`, `next_cursor`
with source/query filtering and a 100-row cap. Session detail returns paged hands
and complete eligible statistics. Overview returns only 50 recent hands per source
and separate source-level counts. Statistics iterate rows with Welford variance.

## Migrations 4 and 5

Migration 4 adds durable `import_jobs` and indexed `import_diagnostics`, and
backfills session identities for quarantined records. Job state is distinct from
the import's publication state; cancellation rolls back commit projections.
`/api/import-pages` and `/api/session-pages` use bounded cursor responses;
`/api/imports/{id}` returns a bounded, filterable diagnostics page. Legacy list
routes are bounded too.

Migration 5 adds `decision_analysis_cache`, keyed by stored decision, observation
hash, model version, range preset and budget. Eligibility is checked on cache
hits and before publication. Privacy deletion includes these entries.

`/api/research/local` verifies a summary hash; it does not select serving policies.
Release manifests pin report roles and validate model/policy hashes. One
`active-release.json` pointer permits coherent activation/rollback. Every neural
default promotion remains disabled until all quality, schema and domain gates pass.
