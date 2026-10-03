# Existing metro run recovery — October 3, 2026

The existing Chicago–Naperville–Elgin metro plus Kenosha run resumed without
rebuilding its plan or clearing its queue. The target remains 95,399 aligned
512 m tiles over 24,809.6 km². Illinois, Indiana and Wisconsin source indexes
and the inherited Chicago world frame remain in use.

## Diagnosis and applied recovery

Both background-launched workers had stopped before claiming jobs.
A native stack sample and a registered Python diagnostic handler located the
base worker inside `MetricWayIndex.__init__`, opening the retained Illinois
OSM PBF on LaCie for checksum verification. Its CPU time was unchanged.
Bounded one-byte reads of all three retained PBFs succeeded through the
authorized MacBook-to-mini SSH route. These observations identify the
blocked operation and successful alternate context; they do not establish
which macOS privacy or filesystem component caused the wait.

Before the handoff, the owner verified the task launcher label and process
identities, confirmed that neither lane owned a current job, and retained
byte-verified backups of its plist, frozen plan, indexes configuration,
frame, deadline and status. Only that launcher was unregistered. Its two idle
children exited under their existing supervisor's shutdown procedure.

The existing supervisor resumed through `--ensure` over SSH with one base
lane, one scan lane and the original deadline. The old background service was
disabled for future logins; its plist remains available. The existing save
publisher remains responsible for recovery and delivery. No frozen source
checksum was bypassed, no jobs were duplicated, and no new paid workload,
installation, credentials or public publication was started.

## Verification at 16:49 UTC

| Evidence | Observed result |
| --- | --- |
| Geometry receipts | 8,313 before recovery; 8,331 afterward |
| Installed tile acknowledgements | 8,313 before recovery; 8,331 afterward |
| New delivery reports | 18 tiles; 17,408 chunks added; 1,024 existing chunks preserved |
| Scan candidates and installed scan receipts | 294; next scan tile leased and processing |
| Fresh tile `-96_25` | All 1,024 Anvil chunks correctly positioned; region bytes and manifest match the generation receipt |
| Existing frame | Same metre CRS, origin, vertical translation and live binding |
| Photo entity region | Byte-identical to the verified deduplication-preparation backup; no entities removed |
| Preserved source edits | The existing building-layer edit and two owner scripts retained their prior hashes |
| Storage | About 573 GiB free on LaCie, 34.5 GiB on mini control storage, 70.3 GiB on the save volume |

The 150 GiB bulk, 12 GiB control and 50 GiB receiver reserves remain active.
The receiver has about 20 GiB above its boundary; full metro completion is
not a promise that the remaining save and scan caches will fit. Later samples
showed slow LaCie file creation and point-cache writes while leases renewed.
Those storage delays remain a throughput limitation.

The fresh tile audit checks coordinate continuity and generated Anvil bytes,
not a rendered client walkthrough or independent source-building accuracy.
Its receipt still says `game_load_verified=false` and
`appearance_verified=false`. Earlier frozen-excerpt native load/save tests
remain separate evidence. The whole metro is unfinished.

No real facade photo colors were applied. The twenty-building pilot still
lacks registered, eligible facade observations. The shared importer and
reversible deduplication preparation remain available, with no application
to the original photo entities. The interrupted AWS originals retrieval was
not restarted as part of this recovery.

Private owner evidence is retained in `runs/metro-generation-recovery-20261003`
and the mini control directory's `startup-recovery-20261003`. It includes
startup backups, process handoff, frozen-input comparisons, fresh alignment
proof and verified pre-installation backups. This document is a dated
snapshot; use the live generation and publisher receipts for later counts.
