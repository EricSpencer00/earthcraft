# Earthcraft continuation — 4 October 2026

Work remains in `/Users/eric/earthcraft-worktrees/playable-pilot-20261002`, branch
`codex/playable-pilot-20261002`. The earlier preparation commit is
`2aa14fe1ad0d0299c8f82dd4c2cfa77ee07d73f3`. The shared owner checkout's existing
building edits and generation workers were preserved. No new paid compute,
credential acquisition, or generation restart was performed.

## Actual client acceptance

The existing Minecraft 1.21.10 client and installed assets loaded an independent
copy at `runs/visual-acceptance-20261004/client/saves/Frozen-Excerpt` in the owner
checkout. Source: `runs/playable-pilot-20261003/excerpt/world`. The original/user
save was never opened by this client. The full restored snapshot was not loaded.

Native window captures establish real rendering and movement:

| Capture in `runs/visual-acceptance-20261004` | Observed coordinates | Result |
| --- | --- | --- |
| `elmhurst-f3-01.local.png` | −26330.544, 129.505, −224.794 | Saved Elmhurst location renders in region −52,−1. |
| `elmhurst-walk-02.local.png` | −26269.033, 129.505, −283.524 | Player input moves within the same coordinate frame; 29 FPS with the 30 FPS test cap. |
| `downtown-walk-03.local.png` | 70.5, 90, 40.5 | Downtown region 0,0 loads after teleport; 30 FPS at that capture. |

This is functional acceptance of a frozen sample. It does **not** pass visual
fidelity acceptance: plain walls, fragmented/floating scan geometry, holes and
bright roof patches are visible. The test used vanilla Minecraft; the user's
installed scan upgrades and mods were preserved. The samples do not establish
metro-wide spatial accuracy or performance.

The isolated client subsequently closed normally and logged all dimensions
saved. A final check confirmed that all 797 protected original level/player/entity
file hashes still matched; evidence is `client-acceptance.local.json` alongside
the captures. This check does not cover every block in the full metro world.

The original save's 386 photo display entities versus 193 declared panels remain
covered by the prior reversible deduplication preparation. That patch was not
applied to the original world and no original world entities were deleted.

## Real inputs for the frozen twenty-building pilot

Selection remains 10 Elmhurst plus 10 downtown buildings, with selection hash
`d5a612e0637d10445f2e0de244a2b1dccb81a331c8f8ef7d1761ca58535f144e`.
`pilot_photo_inputs.py` retains original image bytes, calibration bytes and
checksums from the existing discovery metadata. It decodes the source's zlib SfM
streams rather than treating them as missing UTF-8 JSON.

The current acquisition retains 10 real Mapillary photos near the 10 Elmhurst
pilot buildings and three reconstructions containing 206 shots / 23,812 points.
Nearby cameras are candidate inputs, not measured wall coverage. The existing
nearby-photo cache has no candidates near the ten downtown pilot buildings; this
does not establish that downtown has no publicly available photos.

The existing projection code ran once in an isolated directory on the free mini:
`runs/pilot-projection-20261004`. Ten photos produced 42,962 projected RGB/UV
observations. They remain in `unregistered_reconstruction` coordinates with
**zero admitted world samples and zero applied real-photo face colors**.

The shared importer remains gated on a metric transform into the fixed world
frame with independent held-out controls, static wall ownership, depth/occlusion
checks and complete creator attribution. Reconstruction poses and proximity
alone do not satisfy those inputs. These are concrete admission blockers; no
photo-colored Minecraft result was tested. Original photos were not uploaded to
HF. Public KartaView probes returned timeouts/502 responses; those are service
failures, not evidence of absent coverage.

Local acquisition evidence lives at
`runs/street-photo-pilot-acquisition-20261004/pilot-inputs.local.json`.

## Storage and continuous archival

The original whole-metro plan remains 95,399 tiles / 24,809.61 km², using the
existing TM frame, one metre per block and vertical offset −116 m. Plan hash:
`8f5ef6708cf51d2e3ea17f4036eb337e7689fd8a7a96b96fb34fb4bf767f5121`.

The new checkpointed archive route uploads generator-only shards to
`generated-tiles/v1/` in the existing owned private HF dataset. It verifies
receipts, frame, chunk positions and region bytes, excludes personal state, and
keeps higher-quality scan results. Transient transport failures are retried
within bounds; generation is not restarted. A first independent Hub download
matched complete archive SHA-256
`290dddeb47a00d162e8ed07202642d794bbb4708909b3cf68880b4da74875644`.
Later uploads retain individual remote checksum proofs. Live counters are in
`runs/chicagoland-scans-20260929/hf-generated/status.local.json`, not this dated
report. Shards alone are not a complete playable save.

The tested exporter now skips receipts already identified in a verified immutable
archive checkpoint, avoiding a growing reread of old receipt files each batch.
New and upgraded receipts still undergo the complete audit. The exact prior
exporter hash was checked before an atomic update on the mini, and its backup
was verified; original generation workers were not restarted.

All 111 retained LaCie snapshot archives and all 6,769 contained file hashes were
verified before releasing the redundant local staging clone. The local package,
retained LaCie package and prior private HF snapshot remain. Hash checks on 797
original level/player/entity files matched before and after release. APFS shared
blocks meant the clone release freed almost no physical space: about 46.54 GiB
remained, below the original publisher's 50 GiB reserve. Its storage boundary
was preserved. LaCie still had about 531 GiB free at a later check. Original
source caches and archive-retrieval partials were retained.

The interrupted 19.79 GB AWS source archive has not been fully retrieved or
verified. Authentication renewal does not justify duplicating its jobs or
expanding its existing cap. No new retrieval attempt was started here.

## Host access settings

The specific `computer-use` MCP server was disabled and is now enabled through
the normal settings UI. Any App control and Locked use were already enabled.
Native Accessibility, keyboard/mouse control and scoped window screenshots work.
This chat's old CUA REPL transport still reports `Transport closed`; enabling a
setting is not evidence that its current connection recovered.

The server's relative command and working directory were also replaced with the
existing installed helper's absolute path and `/Users/eric/.codex/computer-use`.
The private configuration backup was byte-verified before the change. Arguments,
environment and unrelated servers were preserved. The subsequent CUA call still
returned `Transport closed`; the current connection remains unverified.
Receipt: `runs/visual-acceptance-20261004/codex-access/absolute-command.local.json`.
The specific server was subsequently refreshed through its own off/on switch
and confirmed enabled with both installed paths present. The existing CUA
connection still returned `Transport closed`, including its reset call.

The coordinator guard previously selected idle, live app-owned CUA MCP workers
for termination after ten minutes. A verified backup and fixture regression
preceded a narrow repair that preserves those workers while their app owner is
alive. Standalone stale-helper cleanup and compute protections remain intact.
Receipt: `runs/visual-acceptance-20261004/codex-access/guard-repair.local.json`.
This host configuration is outside a Git repository.

An AC-only `caffeinate` guard is active for the existing app PID, bounded to 24
hours and app lifetime. It does not change screen locking, battery settings or
lid-sleep behavior. The permanent `pmset` change was not applied because it
requires administrator authentication. A sleeping Mac cannot execute local
Codex/CUA work; supported closed-lid use needs power and an external display,
keyboard and mouse. Existing mini generation can continue independently.

## Validation

The archive/export, retention and pilot-input regressions run without network
access or generation. All 31 tests pass, along with Python compilation, the
archive controller's JavaScript syntax check and the Git whitespace check.
They cover receipt/frame/coordinate changes, private
contents, remote-index concurrency, edited snapshots (including same-size edits
with restored timestamps), bounded source decoding and finite transport retries.
These tests verify preparation and transport behavior, not facade realism.

Sources: [Computer Use setup and locked use](https://learn.chatgpt.com/docs/computer-use),
[Apple sleep settings](https://support.apple.com/guide/mac-help/set-sleep-and-wake-settings-mchle41a6ccd/mac),
[Apple closed-lid display requirements](https://support.apple.com/en-us/102501),
[Mapillary photo license](https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data).
