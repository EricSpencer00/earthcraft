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

The local launch agent `com.eric.earthcraft.map` runs existing Python, serves only
loopback, and reads original save metadata while serving assets from the isolated
checkout. Its configuration and private map cache live under
`/Users/eric/earthcraft/runs/chicagoland-scans-20260929/map/`. It does not write
Minecraft entities, chunks, save metadata, credentials, or worker state. The
original publisher and storage thresholds are unchanged.

Validation: eight metro data regressions, nine existing dashboard regressions,
and four saved-coverage regressions passed. The frontend logic checks cover
negative world coordinates, independent saved/generated counts, scan labels,
stale-message clearing, and static-feed fallback. The running HTTP feed and
updated HTML were checked at the original address; JSON transfers use gzip.
Desktop and phone visual checks are unverified because both browser automation
and the app open-tab tool returned `Transport closed`. No software was installed
and no public deployment or upload was performed.
