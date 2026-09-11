# Chicago draft: real source data, local execution

Chicago is the current large case study for Earthcraft. All geographic
processing stays local. Downloads supply public map data and software; they do
not send imagery to a hosted model. No AI model is needed for the geographic
draft.

## What is automatic, and what was hand-authored

The original 64 m baseline was produced by Arnis from real OSM and elevation data. Its **refinement was a hand-authored, landmark-specific experiment**. The narrow tower, material hint and part-height assignment in that refinement do not demonstrate general automatic reconstruction.

The Chicago runner does not import `tower_overlay.py`, contain building IDs, inject material tags or alter building heights. It selects source features spatially and passes their tags and reference-complete geometry to the general Arnis renderer. The city boundary and downtown test bounding box are configuration choices, not fabricated building geometry. Arnis still supplies procedural heights, windows, roof details and vegetation where observations are absent. This is a map-derived draft, not an accurate reconstruction of every façade.

## Local pipeline

1. Verify the regional Geofabrik OSM PBF against its published checksum; preserve timestamp and compute SHA-256.
2. Extract features intersecting the City of Chicago boundary plus 100 m context. Keep complete referenced nodes/ways for crossings and building relations. Count source height/material/roof coverage independently of generated appearance.
3. Write a frozen local OSM XML input for the pinned Arnis executable.
4. Generate a downtown test containing tall buildings. Stop if generation fails or the resource gate is exceeded.
5. Generate one enclosing-city rectangle in a single Arnis invocation. Arnis's existing region-based streaming uses one coordinate frame and one vertical normalization. This avoids stitching independently rebased small worlds.

The rectangular draft includes terrain outside city limits; it does not claim a precise city-shaped edge. City feature counts use the polygon. Complete source geometries can extend beyond it. No area is excluded merely for lacking building detail.

Use native Python/Rust on the M1 Max. Store the source PBF, derived XML, Arnis caches and output worlds on LaCie; keep process state and logs internal. Original small cache directories were retained as internal backups when their active paths were redirected to LaCie. The disk monitor guards both volumes.

## Scale and height limitations

Arnis 3.1.0's local projection uses a spherical affine approximation; the nominal setting is one block/metre, but that is **not a surveyed 1:1 guarantee**. The inventory reports east-west scale discrepancy against WGS84 geodesics at the north, centre and south of the city. Continuous geometry, faithful building validation and a lower-distortion metric exporter remain later work. No accuracy gate has been relaxed to call this draft faithful.

Do not use the current `web_mercator` option as a stitching fix. Inspection found both per-bbox origins and unequal axis scaling. The single-run local draft avoids stitching without claiming to correct all projection distortion.

The city uses Arnis's extended-height datapack because the vanilla height range cannot contain every skyscraper at nominal scale. This is experimental and still needs a Java 1.21.10 load test. Terrain smoothing and source-derived guesses are also not independently validated. No game file is called playable until game loading succeeds.

## Resource limits and stopping

Four local worker threads; force region streaming. Stop at 32 GiB process-tree RSS, 2 GiB system swap growth, 100 GiB external project data, 20 GiB internal free reserve or 100 GiB external free reserve. The downtown pilot has a 30-minute hard runtime limit; promotion to the full run requires ≤20 minutes and ≤16 GiB observed peak RSS. The city run stops after 24 hours. These are engineering ceilings, not performance predictions.

The supervisor writes status every five seconds and checks project bytes approximately once per minute. Short spikes or downloads already in flight can exceed a threshold between checks. Incomplete output remains explicitly unverified; no existing save is overwritten. The local pipeline stops on error and does not ask a remote model to repair itself. Interrupted city generation is not yet resumable; retain its logs and partial output for diagnosis.

## Reproduction and status

Install `requirements-chicago.txt` into the project environment and provide an
external workspace with the verified source files. The scripts require
`--root` explicitly; use your own path for the workspace:

```sh
.venv/bin/python scripts/chicago.py prepare --root "$EARTHCRAFT_BULK_ROOT/chicago"
.venv/bin/python scripts/chicago.py pilot --root "$EARTHCRAFT_BULK_ROOT/chicago"
.venv/bin/python scripts/chicago.py generate --root "$EARTHCRAFT_BULK_ROOT/chicago"
```

Set `EARTHCRAFT_BULK_ROOT` to a directory on a volume with enough space
before running these commands. This city draft is an opt-in acquisition job,
not part of the offline pull-request check.

Each stage refuses an existing output directory. The pipeline helper is the
local continuation process; it waits for the active preparation process and
runs the two generation stages only after preceding stages succeed.

Inspect ignored `runs/chicago/pipeline-status.json`, `inventory.json`, `prepare.log`, and each stage's `status.json`/`generation.log`. Outputs are under the external workspace's `worlds/`. Treat `draft_generated_unverified` as generation completed, not game loading or building accuracy verified.

The fixed settings are documented in `configs/chicago.json`; this file is not a second runnable configuration interface.

## Sources

- [City boundary layer](https://gisapps.cityofchicago.org/arcgis/rest/services/CachedMaps/AerialCache/MapServer/0): municipal geometry; downloaded as GeoJSON in longitude/latitude. Dataset capture date remains unknown.
- [Geofabrik Illinois](https://download.geofabrik.de/north-america/us/illinois.html): OSM regional snapshot; source replication timestamp is recorded in the inventory.
- [OSM attribution and license](https://www.openstreetmap.org/copyright): preserve with derived worlds; no public redistribution is performed here.
- [Pinned Arnis source](https://github.com/louis-e/arnis/tree/v3.1.0): existing geographic generation, height datapack and region streaming. Local results must be measured separately from upstream claims.

## Measured start of the city run

Local extraction completed: 822,026 building-outline ways and 1,456 building-part ways intersect the city boundary (28 ways carry both tags; these are feature counts, not a count of unique physical buildings). The broader building/part denominator is 823,454. Explicit tag counts are 764 height, 425,432 levels, 857 material, 343 colour, 1,432 roof shape and 41 roof height. Most façade appearance therefore cannot be asserted from these map tags.

The downtown test generated in 45.29 seconds with an observed peak process RSS of 5,497,700,352 bytes (about 5.12 GiB). These values include reading the frozen city extract and the test's data access; they are not a full-city forecast. The level metadata was read successfully and the extended-height datapack is present. No in-game load has been verified.

The full run was started after this resource gate passed. Its output rectangle is 34,663 × 42,288 blocks around a 598.81 km² municipal boundary. Source inventory checks found east-west scale discrepancy from about −0.557% in the south to +0.033% in the north; do not advertise this draft as exact 1:1. The city elevation request is sampled at approximately 3.44 m per pixel before conversion to the block grid. Runtime completion must be read from the status files, not inferred from this start record.

## Unattended runs and editor overhead

On macOS the runner attaches `caffeinate -i -w` to the generator PID, preventing idle system sleep only for that process lifetime. The current city job also has this assertion attached. Manual sleep and closing the lid are not prevented. Project-size scans are scheduled sixty seconds after the preceding scan completes, avoiding skipped checks from modulo timing. These runner changes apply to subsequent runs; the active process is not restarted.

The ignored `earthcraft.local.code-workspace` opens the repository and external Chicago directory together. Bulk sources, caches, worlds, the vendor tree and the Python environment are excluded from editor search and file watching; they remain browsable in Explorer. Download retries remain the current bottleneck; no throughput improvement has been measured.
