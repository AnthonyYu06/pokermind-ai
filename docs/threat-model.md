# Local threat model

| Threat | Implemented mitigation / evidence | Remaining |
|---|---|---|
| Malicious history | Byte/record/time bounds, strict contracts, no archives, React text rendering; tests | Real parser fuzzing |
| Traversal | Hash-named originals, display-only filenames; test | Hostile shared-host symlink hardening |
| Unauthorized access | Loopback binding, trusted hosts, same-origin write header, no CORS, CSP; tests | Authentication before any deployment |
| Large uploads | Streaming ASGI request cap + bounded file read | Remote per-user quotas |
| Live assistance | Stored IDs, shared delay, quarantine, server-owned drills; API/CLI tests | Re-audit every new route/job |
| Model leakage | Actor-only observations and card/suit independence tests | Broader temporal/augmentation audits |
| Unsafe model | Trusted local artifacts, checksum, weights-only loading | Cloud registry governance |
| Credentials / cloud disclosure | No platform credentials; no cloud run path | Scoped federated roles later |
| Runaway spending | $0 policy, cloud command fails closed | Approved bounds/expiry and actual billing checks |
| Data loss | SQLite backup API + originals/checksums, restore-to-new-directory test | Remote model rollback and off-device backup lifecycle |
| Data deletion | Preview-bound confirmation, source-wide overlap cleanup, referenced-original retention, durable retry; fixture tests | External copies, SSD snapshots and model unlearning |
| Dependency vulnerabilities | Lockfiles, npm audit and CI audits | Ongoing patch review |

The OS user can read local files; this is not a hostile multi-user server. Real
histories, models and runtime data are ignored by Git. Deleting an index does not
erase backups/cloud versions or learned models. No comprehensive erasure claim.
