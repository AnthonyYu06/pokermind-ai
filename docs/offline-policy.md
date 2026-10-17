# completed-session-24h-v1

Backend, CLI and batch extraction share a fail-closed gate. Review/equity/bookmarks
take stored IDs; trainer state belongs to the server. No arbitrary-state inference
endpoint, user-provided clock, provenance switch or external actuation interface.

Real records require second-resolution timestamps with UTC offsets and a matching
IANA timezone. Start cannot be future; end must follow start and cannot be future.
A documented end marker sets eligibility to end + 24 hours. With a missing end,
the user may declare completion; the server timestamps it and waits at least 24
hours. Repeated declarations never shorten the delay. Missing start/timezone,
imprecise, future and inconsistent dates remain quarantined. File mtime is ignored.

Rule/accounting failures and conflicting revisions block review even if old enough.
Cached analyses remain behind the gate. Files/declarations are not tamper-proof;
these controls prevent intended live workflows, not activity elsewhere.

No folder watching, screen/clipboard capture, client memory, packet inspection,
platform log scraping, credentials, automated download or persistent anonymous
opponent identities. Real data stays local; there is no remote deployment yet.

## Official sources checked October 15, 2026

[Ignition features](https://www.ignitioncasino.eu/poker/features) describes delayed
complete histories that reveal all players' hole cards.
[Terms](https://www.ignitioncasino.eu/terms-of-use), sections 6.3/13.3, restrict robot
software and external assistance. Neither page approves PokerMind. The product
delay is a conservative restriction, not proof of permission. Exact export-menu
instructions and real format compatibility remain unverified. Recheck guidance
before implementing a real parser and enforce any longer required delay.
