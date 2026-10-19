# Photoscan adapter (design, not wired in)

`~/photoscan` (local, MIT) turns USGS EPT LiDAR, NAIP and photos into per-column building
geometry. Loyola uses it already. This page describes how EarthCraft could use the same library
without losing its verification rules. Nothing here changes default behaviour.

Status 2026-10-18: design only. No prototype yet; the comparison plan is in §6.

## 1. What EarthCraft computes today

| Stage | File | Input | Output |
|---|---|---|---|
| EPT crop | `scripts/ept_buildings.py:crop` | USGS EPT project, tile `grid`, paired DTM + `valid` mask, optional `surface_context` | `points.npz` (`xyz` Nx3, east/north/elev in tile CRS), `manifest.json` (`earthcraft-classified-ept-building-crop-v1`) |
| Point admission | `scan_envelope.admit` (called from `crop`) | points + classes | class 6; class 1 only with a mapped footprint, paired DSM/DTM envelope and >2 m ground clearance |
| Datum check | `crop` | class-2 returns vs DTM | refuses if <20 ground points or median abs residual >2 m |
| Voxelize | `scripts/point_geometry.py:voxelize`, `ept_buildings.load` | `xyz`, `meta`, `offset` | unique int32 cells `(x, y, z)`, only above `ground[z, x]` |
| Shell | `scripts/building_layer.py` (`styled-shell-v1`) | point cells, footprints, ground, provider tops | closed styled shell; derived walls/roofs labelled as adaptation |
| LoD2 roofs | `scripts/cityjson_roof.py:rasterize_roof` | Roofer CityJSONSeq feature | `top: int32[z, x]` (EMPTY = int32 min), `supported`, report; never reprojects |
| Regularize | `scripts/detail_buildings.py` | save column tops + footprints | despiked roof per column (3×3 then 5×5 median), closed volumes, reversible edit layer |
| Materials | `scripts/material_router.py` | OSM tags (`facade:*`, `roof:*`, `building:*`) | block per surface with evidence record |
| Roof colour | `scripts/roof_color.py` | frozen orthophoto crop on the exact tile grid | recolours measured roof tops only; masks shadow/vegetation |
| Orchestration | `scripts/regional_generate.py` | tile plan from `metric_frame`, HF dataset | tiles written through `hf_store` |

### Grid, CRS and datum conventions

* **Horizontal:** a world-local transverse Mercator frame (`frame['crs']`, WKT). Tiles are
  `{'crs','west','north','size'}`, `size` 16..512 m, chunk aligned to the frame
  (`metric_frame.tile_layout`). 1 block = 1 m.
* **Axes:** `x = floor(east - west)`, `z = floor(north - northing)`, so +X east, +Z south.
  Rasters are `[z, x]`. Cells are `(x, y, z)` = Minecraft X/Y/Z.
* **Vertical:** source elevation in NAVD88 (Geoid2018 for Cook 2022). `y = floor(elev + offset)`,
  where `offset = frame['vertical_offset_m']` from `tile_layout`; it is shared by all tiles.
  The world range is checked against `dimension_min_y`/`dimension_height`.
* **Ground:** `ground[z, x]` is the Y of the top terrain block; building cells are kept only if
  `y > ground`.
* **Native sources:** EPT WKT (USGS) and EPSG:6455 (Cook County 2022 LAS) are reprojected into
  the tile CRS once, at crop time.

## 2. What photoscan already has

From `~/photoscan/src/photoscan` (read 2026-10-18):

* `grid.TargetGrid(crs, west, north, width, depth, cell=1.0, y_offset=0.0)`, with `to_cells`
  giving `(x, y, z)` using the **same formulas** as `point_geometry.voxelize`. It has a `y_round`
  option; EarthCraft needs `'floor'`.
* `lidar.ept_crop(grid, cache, project, classes=(1,2,6))` → `(enz, cls, manifest)`. It is a port of
  `ept_buildings.crop` without the DTM residual check, the index cache, or the class-1 admission.
* `lidar.voxelize`, `ground_raster` (median of class 2), `top_raster` (p98).
* `area.column_rasters(enz, cls, tg, labels)` → `[z, x]` float32 rasters: `ground`, `top`,
  `bld`, `roof` (p98, building, non-veg), `base` (min), `canopy`, `vegfrac`, counts.
* `footprints.subject_points`: footprint association that ignores provider classes (the
  Water Tower shaft is class 5 in 2017). EarthCraft's `point_geometry.load_tower_points` does the same.
* `naip.resample` / `building_colours`, `blocks.quantise` (CIELAB → block palette).
* `loyola_build.build` → `rasters.npz` (all `[z, x]`, `-1` = none): `roofY`, `baseY`,
  `roof_block` (index into `blocks`, 255 = none), `inferred`, `canopyY`, `groundY`, plus
  `buildings.json`. **This is Loyola-specific:** it hardcodes `GRID`, the worldgen labels, and
  `to_y = 64 + elev - 176.2`.
* `export.write/load`: `voxels.npz` (cells, block, confidence, views, rgb, inferred),
  `heights.npz`, `manifest.json`.

## 3. Share, replace, keep

**Can share (move into photoscan, EarthCraft calls it):**
* The EPT hierarchy walk and LAZ decoding (`ept_buildings.crop` ≈ `lidar.ept_crop`). Keep a single
  implementation in photoscan. EarthCraft passes its admission hook and keeps the DTM check (below).
* Voxelization: `TargetGrid.to_cells` matches `voxelize`. Use it with `y_offset=offset`, `y_round='floor'`.
* Per-column rasters (`column_rasters`): roof p98, base (overhangs/canopies), canopy. EarthCraft
  has no `baseY`/`canopyY` today. This is the largest gain (stepped massing, open ground floors).
* Roof colour from NAIP and CIELAB quantisation, *as evidence* feeding `roof_color.py`, which still
  enforces its own mask (owner, valid, no shadow, no vegetation).

**Could replace, after comparison:**
* `detail_buildings.roof_heights` (despiked roof from the save's own voxels) with photoscan's
  `roofY` from raw returns. This needs §6 numbers first. Today's path reads from the save, which is
  already the canonical placement.

**Must stay EarthCraft-specific:**
* `metric_frame` tile layout, chunk alignment, the vertical offset and world-range checks.
* The datum check (EPT ground vs paired DTM, ≤2 m median), point admission (`scan_envelope`),
  and the scan budgets (`≤2M` voxels, Y range).
* Frozen inputs and SHA receipts (`frozen_get`/`frozen_file`), the `regional_cache` byte caps,
  and `hf_store` writes. Photoscan's `~/.cache/photoscan/ept` must not be used; pass a guarded
  scratch cache under `runs/scratch/`.
* Verification and replay (`verify_metric_world`, layer preconditions, reversible receipts),
  install profiles, and the single save.
* The CityJSON LoD2 adapter and the OSM material evidence order.
* Labelling: anything photoscan marks `inferred` stays an adaptation, never "observed".

## 4. Proposed adapter interface

A new `scripts/photoscan_adapter.py` in EarthCraft. Photoscan is imported as a library, and only
that file imports it.

```python
from photoscan.grid import TargetGrid

def target_grid(meta: dict, offset: float) -> TargetGrid:
    """EarthCraft tile meta {'crs','west','north','size'} -> TargetGrid(width=depth=size,
    cell=1.0, y_offset=offset, y_round='floor'). Raises if crs is not metric."""

def crop_points(project: str, meta: dict, cache: Path, dtm: np.ndarray, valid: np.ndarray,
                classes=(1, 2, 6), admit=None) -> tuple[np.ndarray, np.ndarray, dict]:
    """photoscan.lidar.ept_crop on the tile, then EarthCraft's DTM residual check and optional
    scan_envelope admission. Returns (enz float64 Nx3 tile CRS, cls uint8 N, manifest)."""

def building_rasters(enz, cls, meta, offset, labels: np.ndarray, ground_y: np.ndarray) -> dict:
    """photoscan column_rasters converted to world Y. All arrays [z, x], shape (size, size):
      roof_y  int16, -1 none     floor(p98 non-veg building return + offset)
      base_y  int16, -1 none     underside Y where open below, else -1
      canopy_y int16, -1 none
      ground_y_lidar int16       for comparison only; EarthCraft ground stays authoritative
      inferred uint8             1 where filled, not observed
      points  uint16             returns per column
    Cells with roof_y <= ground_y are dropped."""

def roof_colours(meta, naip_rgb, naip_valid, labels) -> dict:
    """Evidence only: {'rgb': uint8[z,x,3], 'block': list[str], 'block_index': uint8[z,x]
    (255 none), 'source': NAIP item ids + licence}."""
```

**Data contract:** a sidecar per tile, `photoscan.npz` + `photoscan.json`, written once to
scratch, then committed to the HF dataset via `hf_store` like other derived layers. The JSON holds
the photoscan git commit, the TargetGrid dict, EPT asset SHAs, NAIP item ids, licence and
attribution, `independent_accuracy_verified: false`, and `llm_used: false`.

**Axis order:** rasters `[z, x]`, cells `(x, y, z)`, east/north/elev for points. These match both
codebases already. No transposes, except that Loyola's `static.npz` labels are stored `[x, z]` and
photoscan transposes them on load. EarthCraft labels are already `[z, x]`.

**Flag:** `regional_generate.py --geometry-source {save,photoscan}`, default `save`.
`photoscan` only adds the sidecar plus a comparison report. A separate later change would let
`detail_buildings.regularize` take `roof_y`/`base_y` instead of save column tops.

## 5. Photoscan API as implemented (photoscan d577656)

The photoscan session implemented EarthCraft's requests. Checked against the source on 2026-10-18:
* `buildings.build_buildings(grid, fp, out_dir, points=(enz, cls, manifest), overwrite=False)`
  together with `Footprints.from_labels(label[z,x], records)` makes no network or cache access.
  `rasters_from_points(grid, labels, enz, cls)` is also exposed.
* `lidar.ept_crop(grid, cache, ..., admit=fn(enz, cls)->mask, max_bytes=N)`: the cache is required.
  Only the CLI defaults to `~/.cache`. EarthCraft always passes `runs/scratch/...`.
* `rasters.npz` (`photoscan-buildings-v1`, all `[z, x]`, int16 world Y, -1 = none): `roofY`,
  `baseY`, `groundY`, `canopyY`, `roof_block` (255 none), `inferred` (0 measured, 1 same-building
  fill, 2 tags, 3 type default), `label`, `support` (uint16 non-ground returns per column).
* `manifest.json` holds the photoscan version + git SHA, the full grid, and the caller manifest.
  `read(dir, expect_grid=grid)` rejects any grid mismatch.
* EarthCraft keeps its own point admission and DTM ground check, applied before `points=`.

The adapter in §4 therefore reduces to: `target_grid` → EarthCraft crop + admission →
`build_buildings(points=...)` → `read(expect_grid=...)`. `inferred >= 2` is adaptation, never observed.

## 6. Comparison plan (Water Tower tile, 64 m, west −32, north 33)

Run the existing frozen crop and photoscan's crop on the same `meta`. Report:
* point counts by class and the overlap of voxel sets (IoU at 1 m);
* roof Y per footprint column: MAE / p90 between `roof_y` and the save's column top
  (`detail_buildings` input), and against `cityjson_roof` where Roofer covers it;
* the number of columns with `base_y` (overhangs) and with `canopy_y`;
* the photoscan Water Tower holdout result from the hand-over note, for context: median 1.18 m.

Keep the run under `runs/scratch/photoscan-compare/`, delete it afterwards, and never write to the save.
