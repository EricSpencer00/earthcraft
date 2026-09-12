# Fast deterministic generation

Chicago now uses two independent geometry lanes. Raw LiDAR is not part of the
normal city-tile critical path.

## Base lane

The base lane is the default `chicago_worker.py` mode. It uses:

- one-metre USGS elevation for topology;
- Cook County 2022 footprints and measured `Height` for building shells;
- locally indexed OSM geometry and ESA land cover;
- a two-block topology support slab and sparse building cells;
- aligned 1,024 m source supertiles, cropped without raster resampling into
  sixteen 256 m Minecraft tiles.

Cook County `Ground_Z` is retained as an audit value. Because its vertical
product can disagree with the USGS terrain datum, the building shell base is
anchored to the median topology elevation under the footprint while the
county's measured building height is preserved. This is a fixed source rule,
not a learned estimate.

Dense Cook County responses are acquired from a complete object-ID inventory
in bounded, ordered pages. This avoids service transfer-limit holes. Every
child source directory records its parent supertile and exact crop window.

Shared source builds use bounded work stealing. A worker waits at most one
second for a 1 km source-cache lock. If another worker still owns it, the tile
is released to the journal with a 30-second cooldown and that worker claims
unrelated work while the source owner continues in parallel. Geometry remains
single-owner per tile. Interrupted unpublished staging is preserved, rebuilt,
and verified before promotion.

```sh
EARTHCRAFT_BULK_ROOT=/Volumes/LaCie/Earthcraft PYTHONPATH=scripts \
  .venv/bin/python scripts/chicago_worker.py \
  --plan runs/chicago-adaptation-city-001 \
  --catalog runs/chicago-source-index-003/city-sources.json \
  --output /Volumes/LaCie/Earthcraft/chicago/city-tiles-001 \
  --bulk /Volumes/LaCie/Earthcraft/chicago/lidar-2022 \
  --source-cache /Volumes/LaCie/Earthcraft/chicago/cache/metric-source-supertiles-v1 \
  --way-index /Volumes/LaCie/Earthcraft/chicago/cache/metric-ways-v1.sqlite \
  --lidar-mode deferred --workers 8 --limit 10000
```

`--lidar-mode inline` remains only as a compatibility and comparison mode.

## Detail lane

`chicago_lidar_refinement.py` is a separate resumable queue. It never blocks a
base tile. For each requested Cook scan it:

1. verifies and preserves the publisher LAS;
2. decodes that member into a persistent 128-foot spatial index once;
3. retains only provider classes used by Earthcraft: 1, 6, 11, 15, and 19,
   with withheld returns excluded;
4. reads only intersecting bins for a tile;
5. builds and verifies `world.refined` beside the base `world`;
6. releases the reproducible per-tile point container after verification.

The live publisher prefers a valid refinement receipt when it exists before a
chunk is first published. It never overwrites an already claimed player chunk.

```sh
EARTHCRAFT_BULK_ROOT=/Volumes/LaCie/Earthcraft PYTHONPATH=scripts \
  .venv/bin/python scripts/chicago_lidar_refinement.py \
  --output /Volumes/LaCie/Earthcraft/chicago/refinement-ops \
  --index-root /Volumes/LaCie/Earthcraft/chicago/cache/lidar-spatial-v1 \
  --workers 1 --limit 10000
```

## Initial local measurement

On September 11, 2026, a five-minute live sample of 107 new fast-lane tiles
had median source time 1.445 seconds (p90 7.277 seconds) and median geometry
time 2.493 seconds (p90 5.183 seconds) while eight workers and the live
publisher were active. The earlier raw-LiDAR source path had measured about
145 seconds median on cache misses and substantially longer tail latency.

The first real persistent-index proof processed 5,821,567 source returns. It
retained 3,640,656 relevant returns in a 60 MiB index; the 256 m tile query
visited 692,282 candidates and retained 475,922 exact in-bounds points. The
resulting sibling world and receipt passed the independent Anvil verifier.

All addressing, acquisition, cropping, height placement, indexing, scheduling,
generation, and verification rules in both lanes report `inference_used: false`
or `llm_used: false`.
