# Pipeline performance

Measured 2026-10-18 on the AWS host (m6a.large spot, 2 vCPU, 8 GB, 4 workers) from
`control/logs/base-*.log` and `jobs.sqlite`, and from the archived Mac mini runs
(`archive/mini-worker-20260929/runs/`).

## Baseline vs now

| Run | Tile | Work per tile | Where | Workers | Rate |
| --- | --- | --- | --- | --- | --- |
| Chicago city run (`chicago-adaptation-city-001`, 9,639 tiles) | 256 m | sources + LQ geometry only | Mac mini, local LaCie, no upload | 9 (`parallel-1..8` + local) | ~3 tiles/s remembered = ~0.75 512 m tiles/s (~2,700/h equivalent) |
| AWS before (main @ 77a3839) | 512 m | sources + geometry + scan upgrade in one lease | m6a.large, network sources, per-batch HF commit | 4 | 116 tiles / 24 h (2–13/h) |
| AWS base-first (PR #37), 48 min window | 512 m | sources + LQ geometry, then scans | same | 4 | 699 tiles / 48 min = **~870/h** |

The "3 tiles/s" was the LQ base lane only, on quarter-size tiles, with 9 workers
on local disk and no upload. Like for like (512 m LQ), it is ~2,700/h on a
10-core Mac versus ~870/h on 2 vCPU: about the same CPU cost per tile (~7 CPU s).

## Where the time went (before)

1. Scan upgrade inline in every base lease: median 2,008 s (`scan-points-and-roof`,
   n=105, p90 6,346 s) and 1,022 s (`scan-roof`, n=39) vs 8.8 s for LQ (n=76).
2. Cache-cap churn: 2,952 `tile_start` for 220 packaged; 1,998 `tile_paused_cache`
   (ArcGrid `z001001.adf` 1.1 GiB members refused at the 12 GiB effective cap).
3. 730 `tile_failed`, ~600 of them `CacheCapacityError`; 547 tiles' LQ lost with them
   (requeued after the fix). ~10 worker-hours spent on attempts that were discarded.
4. 43,251 `cache_evict` events (EPT nodes and indexes thrashing).
5. `regional-footprints` index (2.9 GB) fetched 5 times (24–60 s each) after evictions.
6. ArcGrid members 0.8–1.2 GB each at ~24 MB/s (34 s) per strip.
7. LQ passes still reserved and prefetched ArcGrid (`tile_waiting_cache`) — fixed.
8. Small commits: with slow tiles, the 1,200 s timer fired at 2 tiles/commit.
9. Upload is cheap: commit + verify median 3–6 s, LQ package ~4.2 MB / 5 files.
10. Process overhead is not the problem: workers are already long-lived and loop
    over tiles, opening indexes once.

Now: LQ tile median 14.5 s wall (p90 24 s) with 4 workers on 2 vCPU, CPU idle ~0 %:
the LQ lane is CPU-bound; network and upload are minor.

## Plan

* Done (PR #37): lane `auto` = LQ for the whole plan first, scan upgrades after;
  LQ reserves no ArcGrid; 24-tile commits (~36 commits/h at 870 tiles/h, < 128/h).
* LQ coverage: ~71,000 tiles left -> ~82 h on one m6a.large. It scales with vCPU:
  2 boxes (`launch --count 2`, sharded) ~41 h; a c6a.2xlarge-class box (8 vCPU)
  ~20 h. Tiles per dollar is about constant per vCPU; prefer the cheapest spot vCPU.
* Scan upgrades (~94,700 tiles at 15–35 min each) are infeasible at today's cost
  (years on one box). Next: per-county ArcGrid strips pinned once and tiles routed by
  strip (route_locality already exists), a larger cache (disk is 33 GiB free), and
  fewer scan workers (2) than LQ workers so strips are not refused.

## EPT scan surfaces (2026-10-23)

The county scan read 0.8–1.2 GB ISGS ArcGrid strips (and 3 GB county LAS members) per tile from a
single host, `clearinghouse.isgs.illinois.edu`, which also went down for hours. Those rasters are
derived from the same airborne LiDAR that USGS publishes as EPT in the runner's own region. A scan
now crops the newest classified EPT survey once (~30 MB of LAZ, cached per node) and derives both
surfaces from it (`ept_surfaces.py`: DTM from ground returns, DSM from non-vegetation returns), takes
building points from the same survey, and plants trees from the same crop. County rasters remain
the fallback where no EPT survey exists or its ground disagrees with the LQ terrain.

Measured on the m5a.large spot (2 vCPU), Elmhurst tiles, 2 scan workers running alongside:

| Step | Before | Now |
| --- | --- | --- |
| Scan tile, `scan-points-and-roof` | median 2,008 s (county) | 54–92 s warm, 158 s first tile |
| Trees | 50–57 s (own crop, region read block by block) | 14–22 s (shared crop, palette-mapped read) |
| Roof colour (NAIP) | 39–51 s, after the build | 10–39 s, prefetched during the build |
| Index hashing per worker start | ~37 s (3.4 GB + 2.9 GB) | once per file identity (`sha_memo`) |

Also: dispatch follows plan priority (nearest the origin first; route v2) instead of putting every
Kendall then Will ArcGrid tile first, and the ArcGrid prefetcher only runs with county surfaces.

## Corner cuts (2026-10-23)

* **Lane `quality`.** Claims the next tile by plan priority whose appearance is unfinished, leasing its
  open LQ stages with it. The scans lane only takes tiles whose LQ is done, so the 16,606 tiles never
  started would have waited for a lane change; now every tile goes straight to its final quality,
  nearest the origin first.
* **Sparse tiles.** A tile with at most 25 mapped footprints (`--sparse-buildings`) skips the scan:
  LQ shells plus LiDAR trees (measured: 49 s vs ~150 s scanned).
* **Empty tiles get trees.** With `--trees`, tiles with no building are no longer retired from the
  appearance stage; those retired before (21,437) are reopened once and take the sparse path.
* **Published LQ reused.** A scan-only lease downloads the tile's published LQ (region, manifest,
  geometry receipt; hash-verified) as the upgrade baseline instead of rebuilding it: 1.8 s vs ~6 s,
  and the baseline is exactly what saves installed.
* **One crop per tile.** The tile's survey is selected and cropped once (`ept_surfaces.select`): the newest
  survey with returns in the tile (a bounding box can cover tiles it never flew), then surfaces, building
  points (`ept_surfaces.building_points`, no second decode of every node) and trees all read it. Live
  tiles took 225 s (2022 survey) and 455 s (Cook 2017) with building points at 88-201 s; now 63-156 s
  with building points at 3-12 s.
* **Sparse uses the LiDAR too.** A tile is sparse only when its LiDAR building cells are few as well
  (`SPARSE_ROOF_CELLS` 4,000 m2): a new subdivision with 21 mapped footprints but 207,622 building
  returns is scanned, not left as empty streets.
* Next: trees on heavy-canopy tiles (123 s for 1,114 trees on a rural tile) are the slowest step left.

## Work that is never queued

- **Open water.** Plan tiles listed in `lake/v1/index.json` are built from bathymetry, so workers
  mark all their stages complete at start-up (`Worker.retire_lake`).
- **Tiles with no buildings.** The scan upgrade adds roofs and building points; a tile that no
  Overture footprint comes within 16 m of has none, so its LQ terrain is final and workers mark
  its appearance stage complete with the footprint index as evidence (`Worker.retire_empty_scans`).
  On the 2026-10-22 plan that is 21,728 of 73,784 land tiles (29%); a scan pass took ~15 minutes
  per tile.

Every packaged tile logs `phase_seconds` (indexes, sources, lq_build, scan_select, scan_surfaces,
point_crops, scan_build, roof_colour, package) and records it in the manifest's `generator`, so
the next slowest step shows up in the logs rather than in a one-off profile.
