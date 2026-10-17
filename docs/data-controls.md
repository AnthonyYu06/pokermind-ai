# Local data controls

Open **Manage data** in the app. No data leaves the device unless you explicitly
save or move an exported file yourself.

- **Export originals:** download the unchanged selected input. The server checks
  its SHA-256 first and uses a safe download filename. A quarantined original is
  still your original file; exporting it does not generate analysis or bypass
  eligibility.
- **Retention:** choose 1–3650 days, or leave the setting blank to disable it. This
  policy covers only uncommitted previews and cancelled imports. Thirty days is
  the default. It never automatically removes committed histories. Preview the
  expired selection and confirm it when you want cleanup.
- **Delete real imports:** review the count of originals, imports, hands and study
  attempts, then type `DELETE`. This intentionally operates on all real imports
  together so overlapping exports cannot retain alternate copies of removed
  hands. There is no single-hand deletion that falsely leaves copies elsewhere.
  Synthetic hands and simulator sessions remain.

Deletion uses one SQLite transaction for source records, normalized projections,
observations, cached scores, bookmarks and practice attempts. Original files enter
an atomic cleanup queue in that transaction. A failed unlink stays pending and is
retried explicitly or on next startup. Reference counting retains bytes shared
with another retained import. Import staging and original cleanup serialize on
SQLite's write lock. A changed selection invalidates the confirmation token and
requires a fresh preview. No existing histories were deleted during development;
destructive tests use temporary fixture stores.

The app enables SQLite `secure_delete` and requests a WAL checkpoint, but neither
can promise forensic erasure from SSDs or OS snapshots. Exported copies, backups,
cloud object versions, Delta history and trained weights are outside this control.
Current research jobs select synthetic data only. If real data was independently
copied into another system, remove it there and retrain affected models. App
backups include the SQLite settings and lifecycle state; research workspaces are
separate. See [backup instructions](../README.md).

The CLI exposes the same rules:

```sh
.venv/bin/python -m services.api.cli data-status
.venv/bin/python -m services.api.cli data-retention 30
.venv/bin/python -m services.api.cli data-delete-preview expired_previews
.venv/bin/python -m services.api.cli data-delete-preview all_real
.venv/bin/python -m services.api.cli export-original FILE_ID /path/new-export.txt
```

Only after reviewing the returned targets, pass the exact preview token and
`--confirm DELETE` to `data-delete SCOPE --token TOKEN --confirm DELETE`.
Exports refuse to overwrite an existing destination. The app has no cloud
cleanup action and cannot delete resources outside its local data root.

## Bounded browsing

The hand library searches on the server and uses a row-ID cursor with 50 hands
per page (API limit 1–100). Session detail uses the same paging contract. The
Overview returns up to 50 recent hands **per source**, with separate total and
eligible counts. Its chart describes only those displayed hands. Statistics
consume all eligible rows with a streaming accumulator and preserve opportunity
counts and uncertainty calculations. They still require a full scan/reconstruction;
this is a bounded-memory implementation. The 6,000-hand latency measurements
are in [local research](local-research.md). Import history and session directories
now have server-side search and cursor pages. Import diagnostics use a line cursor
and status filter; history summaries never embed all diagnostics.

Verification: `tests/integration/test_data_controls.py` covers paging, complete
statistics, export integrity, overlap deletion, shared originals, stale preview
rejection, eligibility-independent raw export, retention exclusions, interrupted
cleanup and CLI behavior. Browser checks covered page 1 → page 2, search resetting
the cursor, deletion preview/cancel, and desktop/390px data-control layouts.

## Import jobs

The app queues preview and commit separately, with four active jobs maximum.
Original bytes are preserved before parsing, including malformed files. A cancelled
commit rolls back every application projection; it never leaves half an import.
Restarted workers mark unfinished work interrupted rather than silently committing
it. Use **Retry** to resume from the preserved original. Raw files are not written
when a full queue rejects a new job. Cancel active jobs before previewing deletion.

```sh
.venv/bin/python -m services.api.cli import-queue /path/export.jsonl
.venv/bin/python -m services.api.cli import-worker
.venv/bin/python -m services.api.cli import-jobs
.venv/bin/python -m services.api.cli import-commit-queue FILE_ID
.venv/bin/python -m services.api.cli import-worker
.venv/bin/python -m services.api.cli import-job-cancel JOB_ID
.venv/bin/python -m services.api.cli import-job-retry JOB_ID
```

The running app already owns its worker; a second worker refuses ownership. CLI
worker failure returns nonzero. Completed-session time gates are rechecked at
commit and analysis. Conflicting revisions stay quarantined; no automatic choice
of a supposedly authoritative revision is made.
