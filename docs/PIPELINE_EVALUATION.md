# Earthcraft pipeline evaluation

Measured locally, September 10, 2026 (America/Chicago). This evaluates the active
Cook County → metric scene → Minecraft route, not the abandoned procedural
Chicago baseline or the older unused language-model experiments.

## Verdict

| Requirement | Finding |
|---|---|
| Live | Implemented: a Fabric importer receives source-backed chunks and pavement deltas while the same world runs. No external writes to open Anvil files. |
| Fast | Interactive updates work, but full-city delivery is not fast enough yet. Geographic generation is much faster than the initial in-game block importer. |
| Free | No paid geographic API, AWS compute, or inference calls in the active pipeline. Minecraft, hardware, storage and bandwidth are not cost-free. Rights differ by source. |
| Programmatic | Acquisition, transforms, cropping, voxelization, replay, queueing and game insertion are local code/math. General façade registration is not solved. |
| Correct | Source preservation and deterministic coordinate/block conversion have executable evidence. Independent real-world accuracy and complete buildings do not. |
| Applicable and repeatable | Repeatable for frozen Chicago inputs; useful architecture for other areas. A globally complete, configuration-only, detailed Earth pipeline has not passed. |

The research and goal workflows kept software correctness, independent geographic
accuracy and visual fidelity as separate gates. A passing file/game test cannot
turn holes or a failed photo match into a finished building.

## What actually runs

1. An immutable Chicago boundary/frame and SQLite journal select 256 m jobs.
2. Cook County 2022 LAS archive ranges are downloaded anonymously, verified and
   retained losslessly. Original classifications, coordinates and timestamps stay
   available. A cached OSM spatial index avoids rereading the entire PBF per tile.
3. Native coordinate transforms and local point selection create a one-metre
   grid. Geography uses a fixed horizontal frame and a fixed vertical offset.
4. Local voxelization preserves admitted observed surfaces. Native world chunks
   are independently decoded and checked before their geometry job completes.
5. A bounded publisher reads those immutable, verified tile outputs—not the
   concurrently changing continuous-world files—and sends compressed sparse
   block runs to the game. The game validates hashes, frame, coordinates, block
   allowlists and bounds before applying changes on its server thread.
6. New geometry is admitted only into unclaimed, entirely empty chunks. The
   existing 4,352 accepted chunks are protected, including chunks cleared to air.
   Pavement painting is a compare-and-set from gray concrete, never an arbitrary
   replacement of other player blocks. Existing photo geometry is not changed.

The mod has no network endpoint, command executor, model or credentials. It is
pinned to Java/Fabric 1.21.10 and compiled against the installed intermediary
game libraries. It uses [Fabric lifecycle events](https://wiki.fabricmc.net/tutorial:callbacks)
and Minecraft's block API for game-thread updates.

## Measured performance

The first 69 completed jobs included both cold and warm acquisition:

| Measurement | Observed result | Scope |
|---|---:|---|
| Source acquisition/crop | median 22.91 s; p90 193.26 s | 69 tiles; mixed cache state, not whole-pipeline throughput |
| Geometry plus native readback | median 8.08 s; p90 9.95 s | Same 69 tile receipts |
| Frozen 7.43-million-point crop | 25.85 s → 8.85 s | Removed throwaway compression; all seven arrays and 256 output chunks identical |
| Indexed source preparation | 0.185 s | Six frozen tile replays identical; old profiled PBF path 43.32 s, including profiler overhead |
| Initial live client delivery | 0.92 chunks/s over 144 s | 134 new chunks, 4.53 million block writes; includes competing pavement work |
| Client display during that run | 30 FPS, configured 30-FPS/vsync cap | One debug screenshot, not a frame-time percentile benchmark |

The initial importer had a 2,048-cell cap even when it used less than its 3 ms
cooperative work budget. The final build raises that cap to 16,384 and retains
the time check. The budget is **not a hard latency guarantee**: chunk loading,
individual game calls, scheduling and garbage collection can exceed it. The
first client run saw a 99.4 ms worst batch despite a 1.24 ms median of per-chunk
maximum batches. The final build also processes oldest inputs first to prevent
hash-order starvation, defers nearby chunks without blocking the queue, and
finishes the active bounded patch during normal Save and Quit.

Do not extrapolate one street into an overnight completion promise. The plan
contains 9,639 tiles / 2,467,584 chunks. At one chunk/second, insertion alone is
about 28.6 days of uninterrupted running. That is illustrative arithmetic, not
an ETA: the revised importer, cache mix, storage limits and game pauses change
throughput. Full-city speed needs a tested bulk chunk-transfer path and
player-near prioritization, not repeated per-block work across an entire city.

The live machine-readable snapshot is `runs/chicago-live-001/pipeline-audit.json`.
It records its own timestamp; city counts in an earlier snapshot are not live
coverage claims. The city worker continues independently of whether Minecraft
is open. In-game insertion requires the world to be loaded and ticking.

## Correctness and visual fidelity

Passed evidence:

- Frozen point arrays, indexed map selection, imagery normalization and native
  world chunks replay deterministically in the documented comparison runs.
- The final importer passed two isolated real Minecraft load/save cycles,
  comparing all 524,288 cells in two real source chunks per cycle. One chunk
  was deliberately being inserted when Save and Quit was requested.
- Protected/nonempty chunks were retained, and a diamond-block edit survived
  a pavement recolor. Reopening did not replay completed changes.
- The installed-world checkpoint separately verifies every newly delivered
  scan cell and attempted pavement change against its input and pre-live backup.
  Its result is `runs/chicago-live-001/saved-checkpoint.json`.
- Original photo resource bytes, player position, rotation, inventory and
  abilities are checkpoint checks, not merely claims based on a screenshot.

Not established:

- **Independent accuracy:** no city-wide held-out survey/control set has passed.
  A one-metre output grid is not a one-metre source-accuracy certificate. A fixed
  projection and height offset do not resolve uncertain source vertical datums.
- **Complete buildings:** airborne returns miss walls, openings, glass and
  undersides. The current street view visibly has holes, irregular walls and
  generic gray materials. It is a useful scan scaffold, not a finished façade.
- **Paint correctness:** the 256 m county-aerial candidate has 6,792 explicit
  pavement recolors. It does not recover material under shadows/cars, paint
  buildings, or prove independent aerial/LiDAR registration. Existing non-gray
  pavement colors and user edits are intentionally retained.
- **Façades:** the 193 existing Water Tower photo panels contain 35,239 observed
  opaque texels. Their registration remains experimental. The façade agent's
  distinct 2013/2022 pair produced six mutual matches, zero fundamental inliers
  and four homography inliers; it failed the fixed admission gate. New accepted
  façade coverage is zero. The sparse patch catalog is reusable plumbing,
  not evidence of a newly reconstructed building.
- **World completeness:** bridges/tunnels, water/bathymetry, vegetation, temporal
  changes, source halos and roof/footprint differences still need separate
  checks. Surface absence stays unknown; it is not filled with guessed windows.

## Cost, rights and portability

[Cook County's open-data announcement](https://www.cookcountyil.gov/news/county-eliminates-charge-gis-data)
supports free access, including specified noncommercial uses. It is not a blanket
public-domain license. [County terms](https://www.cookcountyil.gov/terms-use)
and individual imagery metadata still apply; public distribution of the painted
world has not been cleared. [OSM is ODbL](https://www.openstreetmap.org/copyright),
with attribution and relevant share-alike obligations. Commons façade images
retain their individual CC BY / CC BY-SA author, source and license records.
[USGS 3DEP](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services)
is a free public elevation source, but product resolution and coverage vary.

The existing [AWS Terrain Tiles adapter](https://registry.opendata.aws/terrain-tiles/)
uses anonymous objects and numerical elevation decoding. It is a coarse terrain
fallback, not a global building scan. Requester-Pays LiDAR and paid cloud compute
are excluded. Google Maps/Earth rendered imagery is not being scraped or used as
an unrestricted bulk data source.

Portable components are the coordinate frame, provenance, per-layer admission,
voxel writer, appearance patch format, replay checks and live importer. Regional
source discovery, datum/classification adaptation, suitable street photography
and independent controls still vary by location. A flat Minecraft grid cannot
preserve all distances on a spherical Earth: global travel requires explicit
local-chart transitions, not silently scaling the map.

## Next engineering gates

1. Replace the live delivery bottleneck with verified bulk chunk transactions;
   measure latency under traversal, not just stationary FPS.
2. Make appearance a resumable per-tile stage; currently only the bounded Water
   Tower ground-color candidate is queued, not city-wide painting.
3. Register suitable licensed overlapping façade views against held-out controls
   and preserve unknown masks. The failed match is not solved by adding an LLM.
4. Extend independent terrain/building/bridge/water tests and per-building error
   reports. Keep source consistency scores separate from ground-truth accuracy.
5. Pass a second city's end-to-end configuration-only replay with explicit data
   rights, disk budgets and failure recovery before calling this globally general.

Live crash recovery deliberately quarantines partially applied patches instead
of overwriting possible edits. Automatic reconciliation of such patches, other
mods/remote command edits during import, future version upgrades, and seamless
global chart navigation are not yet verified. Internal free-space reserve is
22 GiB; the live archive cap is 1 GiB and inbox is 128 files. These are finite
operating limits, not proof that the full city fits this drive indefinitely.
