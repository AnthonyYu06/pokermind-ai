# Parser capability matrix

| Feature | State / evidence |
|---|---|
| Canonical v1 JSONL | Synthetic fixtures and integration tests |
| Actual Ignition text | **Unsupported/unverified; no supplied real sample** |
| UTF-8/BOM, CRLF/LF | Tested |
| Preview/commit/cancel/retry | Tested; transactions and audit records |
| Filename-independent duplicates/overlap | Tested; byte and canonical hashes |
| Revised prior hand | All versions quarantined; raw originals retained |
| Truncated/binary/malformed input | Rejected or quarantined with report |
| Six-max/heads-up NLHE cash | Synthetic coverage only |
| Short/cumulative raises, all-in calls | Tested legal/reopening fixtures |
| Side pots, folded money, split/odd chips | Tested; odd chip clockwise from button |
| Rake | Explicit supplied integer; deducted from pots low-to-high; no inferred room schedule |
| Missing hero | Unknown until selected, never inferred |
| Missing showdown cards | Quarantine if needed for winner verification |
| Tournament/Omaha/straddle/multiple boards | Unsupported |
| Archives | Unsupported; never unpacked |

Next real-format gate needs a small sanitized official completed-session export
with intact structure, amounts, timestamps/timezone, session markers and summaries.
Include ordinary and edge cases if available. Remove account identifiers; never
send credentials. Do not obtain anyone else's histories without authorization.
