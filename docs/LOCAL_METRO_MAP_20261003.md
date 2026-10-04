# Local metro map

The explorer at `http://127.0.0.1:8767/` now defaults to the frozen metro plan:
95,399 tiles at 512 m, covering the Census Chicago-Naperville-Elgin metro plus
Kenosha County. Its boundary covers 24,809.6 km². The inherited Chicago metre
frame supplies all tile corners; the existing save is never recentered.

The map reads the original mini journal through the established `mac-mini`
route. It checks that route before each read, opens SQLite read-only, normalizes
expired leases, and copies sanitized states once a minute. The journal's plan
hash must match the frozen local plan. Failed refreshes retain the last observed
time and show a stale notice. No generation workers are started.

Saved coverage is the union of accepted 256 m and 512 m save declarations. Four
legacy cells must cover a complete 512 m tile before it counts as saved. The
publisher acknowledgment is kept separately. Only actual installed scan receipts
with upgraded chunks count as scan upgrades. A receipt can cover part of a tile;
it does not prove photo coloring, every current block, or client rendering.

At the live-feed check on 2026-10-04 02:42 UTC, the map reported 9,945 full metro
tiles covered by saved records, 9,743 regional geometry jobs complete, 303 saved
tiles with scan upgrades, and two active workers. Earlier installed areas explain
why saved coverage exceeds the current journal's completed geometry count. The
publisher had acknowledged 9,210 tiles and stopped at its existing storage
reserve. Generation remained running. Street-photo colors and all regional game
checks remain pending. Elmhurst tile `-52_-1` retained world offset
`[-26624, -512]` and its exact inherited geographic corners.

The private map preview runs in `/Users/ericspencer/earthcraft-map-20261004`
on the existing mini using existing Python. It opens the original journal
read-only on that host; it starts no generation workers. Thirty copied files,
23.6 MB total, passed SHA-256 checks. Only interface assets and save metadata were
copied, never Minecraft entities or chunks. The plan is linked to its original
frozen file. The copied save footprint and frame hashes were compared with the
original MacBook save through both final HTTP views and matched exactly. The
save metadata copy is dated; original save delivery remains storage-paused. Its
observation date is shown separately from the fresh queue date, and the copied
coverage and scan receipt hashes must match that observation before export.

An SSH tunnel keeps the browser address at `http://127.0.0.1:8767/`. Both ends
bind only loopback. Before every tunnel reconnect, its wrapper checks the
MacBook origin and the configured `mac-mini` alias. The preview and the tunnel
have separate per-host launch agents named `com.eric.earthcraft.map`. Their
private configurations are in the isolated mini preview and
`/Users/eric/earthcraft/runs/chicagoland-scans-20260929/map/` respectively.
The original publisher, workers, and storage thresholds are unchanged. The
saved-record copy was refreshed and all 31 preview files verified at
2026-10-04 03:21 UTC. This is a dated private preview, not a continuously synced
copy of the original save.

The first local Python server passed an HTTP check, but repeatedly received
external SIGTERM roughly every 33 seconds. A tracked local execution session was
also terminated. That background restart loop was stopped before moving the
read-only preview to the mini. The origin of those stop signals was not
identified; no stop handler, policy, or other process was disabled.

The forwarded check on 2026-10-04 03:12 UTC returned 9,748 regional
geometry jobs complete, 9,945 full metro tiles covered by saved records, 303
saved tiles with scan upgrades, and a fresh journal observation. The saved view
contained 40,006 unique 256 m cells over 2,621.8 km². Its manifest and frame
hashes matched the original save. Photo-colored cells and regional game checks
remain zero and pending respectively. At the final 03:24 UTC check, geometry
completion had advanced to 9,762, two worker leases were active, the queue read
was fresh, and the local tunnel remained running with a single launch. Its
saved-record observation was still correctly dated 03:21 UTC.
Validation: eight metro data regressions, nine existing dashboard regressions,
and four saved-coverage regressions passed. The frontend logic checks cover
negative world coordinates, independent saved/generated counts, scan labels,
stale-message clearing, and static-feed fallback. The running HTTP feed and
updated HTML were checked at the original address; JSON transfers use gzip.
Desktop and phone visual checks are unverified because both browser automation
and the app open-tab tool returned `Transport closed`. No software was installed
and no public deployment or upload was performed.
