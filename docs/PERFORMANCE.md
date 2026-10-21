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
