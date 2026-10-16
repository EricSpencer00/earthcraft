# Street-detail layers: trees, streets, facades, clean volumes and water

Prototype recorded **2026-10-16**. The canonical save is the reference for
terrain, ground height and building placement. Detail is added as separate,
reversible **layers** of block edits with per-edit provenance, applied only to
copies of region files. Nothing here reads or writes the open save directly.
Region files were APFS-cloned out of it first.

## Prototype area

The tile is the original Water Tower tile, a 512 m × 512 m square. Its block
window is X −256…255, Z −256…255, which is the frame's `west=-288, north=289`
tile. It spans the quadrants of `r.-1.-1`, `r.0.-1`, `r.-1.0` and `r.0.0`.

- **Bounds (WGS84):** −87.62790…−87.62173 longitude, 41.89519…41.89980 latitude.
- **Coverage:** Michigan Ave from Huron to Delaware, plus the Water Tower, Pumping
  Station, John Hancock Center, Water Tower Place, Olympia Centre and Seneca Park.
- **Halo:** sources are cut with a 24 m halo, so trees and curbs at the tile edge
  see their neighbours. Only edits inside the tile are emitted.
- **Water test:** `r.1.0` (X 512…1023, Z 0…511) was used separately for the
  water pass, because the lake lies outside the tile.

## Sources (measured 2026-10-16)

| Source | Licence | Measured in tile | Used for |
|---|---|---|---|
| USGS 3DEP EPT `USGS_LPC_IL_4County_Cook_2017_LAS_2019` | Public domain | 472 octree nodes and 116 MB of LAZ, streamed with nothing written to disk. 13.46 M returns in tile + halo. Classes: 1 2.77 M, 2 1.37 M, 3 0.52 M, 4 0.76 M, 5 6.31 M, 6 1.73 M. Every return is a single return. | Canopy height model, ground DTM, curbs, paint intensity, pole checks |
| USGS NAIP ImageServer 2019-08-02, 0.6 m | Public domain | 4-band crop. The NIR band is unusable for NDVI here (median NIR 11 against red 87) | Not used for trees; only an observation |
| OpenStreetMap (Overpass, base 2026-10-16T17:05Z) | ODbL 1.0 | 133 sidewalk ways (7,974 m); 85 crossing ways; 138 crossing nodes (zebra 77, ladder 7); 163 roads (48 with `lanes`, 0 with `width`); 21 lamps; 21 signal nodes; 14 bus stops; 84 trees; 2 hydrants; 126 buildings + 39 parts (colour on 14 parts, material on 18) | Roads, crossings, furniture, facade tags, footprints |
| City of Chicago Building Footprints `syp8-uezg` | City Data Terms of Use | 92 in bbox. 67 of 78 have `stories`, 49 have `year_built`. There is no height field. | Footprints, stories |
| Chicago street centre lines `pr57-gg9e` | Terms of Use | 59 in bbox (no width or lanes fields) | Not needed (OSM was richer) |
| CTA bus stops `qs84-j7wh` / Divvy `bbyy-e7gq` / bike racks `hgdw-64h3` | Terms of Use | 12 / 4 / 30 in bbox | Furniture positions |
| City ArcGIS `ExternalApps/STREETLIGHTDEMO/MapServer/0` (light structures) | **Unstated** (public service) | 263 in bbox, 554 in halo. Attributes mostly blank. | Separate `streetlights` layer |
| Panoramax (api.panoramax.xyz plus 3 instances) | Per picture; nearby ones are CC-BY-SA-4.0 | **0 pictures in bbox, halo, or within 1 km.** The nearest is 1.33 km away. 37 within 10 km, all flat phone photos. The two nearest are interior shots. | Nothing usable here |
| Mapillary Graph API v4 | Imagery CC BY-SA 4.0; derived data under Mapillary Terms | No token. `graph.mapillary.com/images` returned HTTP 500 (code 190, invalid OAuth). Tiles returned 403. | Designed for (see below) |
| Chicago tree, hydrant, crosswalk, sidewalk and signal inventories | — | Not on the portal. Sidewalk and curb data exist only as citywide 2010–2011 zips (77cn-6x4c, 5gv8-ktcg). | — |
| OSM Lake Michigan relation 1205149 (`ele=176`) | ODbL 1.0 | Shoreline member ways near downtown | Water pass |

**Licence handling:**
- Every layer manifest carries a source row per feature, with dataset, licence,
  evidence class and feature IDs.
- The City streetlight inventory has no stated licence. Its lamps are in their
  own `streetlights` layer so they can be left out of a public release.
- Chicago portal data needs the City disclaimer in the release notes.

## Pipeline

```
region COPIES ──► buildings (regularize) ──► staged copies ──► trees, street, streetlights, facades
                                                                 (built from the staged copies)
apply order onto the original copies: buildings → trees → street → streetlights → facades   (+ water)
```

A later layer's preconditions were computed on the world after the earlier
layers. So applying the layers in this order onto the original copies
reproduces the staged result exactly.

### Layer format (`detail/<layer>/v1/`)

```
manifest.json                 schema earthcraft.detail-layer.v1, frame, sources[], regions{r.X.Z:{path,bytes,sha256,edits,chunks}}, report
regions/r.<rx>.<rz>.jsonl.gz  line 1: {"schema":"earthcraft.detail-region.v1","region":[rx,rz],"palette":[...]}
                              then one line per chunk: {"c":[cx,cz],"x":[..],"y":[..],"z":[..],"p":[..],"q":[..],"s":[..]}
```

- **Coordinates:** `x` and `z` are chunk-local (0–15); `y` is absolute.
- **`p`:** the new state, as an index into the region palette.
- **`q`:** the precondition. `-1` means the cell must currently be air.
  Otherwise it is the palette index of the block the edit was computed against.
  Only the block name is compared, so a property change in-game does not block the edit.
- **`s`:** an index into `manifest.sources`.
- **Uniqueness:** edits are unique per cell inside a layer. Conflicts are
  resolved by priority at build time; for example, a trunk beats a leaf.
- **Reversibility:** `q` records what was there, so a layer can be inverted.
- **Encoding:** gzip JSON-lines streams well and a browser can read it with
  `DecompressionStream`.
- **Sizes for the whole tile:** buildings 796 KB, trees 500 KB, street 188 KB,
  streetlights 104 KB, facades 60 KB; the water layer for one lake region is 84 KB.

### `scripts/detail_apply.py`

- **Input and output:** it reads the copies and the layers, and writes new
  region files only for regions that have edits.
- **Safety:**
  - It refuses to write into the input directory, or into any directory whose
    parents hold `level.dat` or `session.lock`.
  - All writes go through `detail_guard.WriteGuard`. That means a byte cap, a
    free-space floor checked before every write, no overwrites, and a
    temporary file followed by an atomic rename.
- **Chunk handling:**
  - Untouched chunks keep their original compressed bytes.
  - Edited chunks have only their touched sections re-encoded.
  - Heightmaps are recomputed. Light arrays are dropped and `isLightOn=0`, so
    the game relights the chunk.
  - Every other chunk tag is preserved: DataVersion 4556, biomes, ticks, block
    entities, `PostProcessing` and `blending_data`.
- **Preconditions:** edits whose precondition fails are skipped and counted.
  This is what lets player edits made since the layer was built survive.
- **Verification:** each output is re-read and checked by `verify_region`:
  - the NBT parses, and the chunk set equals the input's;
  - palettes are non-empty with string properties;
  - packed long arrays have length `ceil(4096 / (64 // bits))`, with
    `bits = max(4, ceil(log2 n))`, and every index is within the palette;
  - heightmaps have 11-bit width for the 1,024-block dimension.
- **Receipt:** input and output SHA-256, plus counts by kind.

### 1. Building regularization (`detail_buildings.py`, layer `buildings`)

This pass is for the defects the viewer shows: thin slabs, holey shells and
floating fragments. It keeps the scan's placement and heights and closes the
volume.

- **Footprints:** OSM building ways, plus any City footprint that OSM lacks
  (less than 30% overlap).
- **Roof heights:** the roof height of each column is the highest solid voxel
  already in the save. It is de-spiked with a 3×3 then a 5×5 median, so
  setbacks and tiers survive. Unobserved columns take the nearest observed
  height.
- **What it emits:**
  - outline walls from the ground to the roof;
  - step walls down to lower tiers;
  - a one-block roof plate, with interiors left as air;
  - walls in the building's dominant opaque material;
  - roof cells that keep their existing colour, which was routed from the
    orthophoto.
- **What it removes:**
  - interior clutter below the roof;
  - scan returns above street level in a 2-cell band outside a closed outline;
  - floating 26-connected components outside all footprints that are not
    grounded or attached and have at most 2,000 voxels.
- **What it never removes:** anything above the roof, such as antennas, spires
  and penthouses.
- **Skips:** footprints with less than 30% of columns observed are left
  untouched, as are protected landmarks. The Water Tower, which carries the
  photo-skin anchors, is protected with a 3 m buffer.

Result: 102 buildings regularized, with a median 98.9% of columns observed.
That came to 502,635 cells added, 249,515 interior cells removed, 138,294
band cells removed and 5,528 cells in 976 floating fragments removed. Heights
did not change.

### 2. Trees (`detail_trees.py`, layer `trees`)

**Why classes 3, 4 and 5 are not trusted directly.** In Cook 2017 they are
height bands: 3 is under 3 m, 4 is 3–6 m, 5 is over 6 m, and class 5 includes
building walls up to 145 m. Every return is single. Admission therefore
relies on geometry.

**Detection:**
- The CHM is built from classes 1/3/4/5 between 0.5 and 32 m above the
  ground DTM (class 2), excluding mapped and scanned buildings.
- The CHM is smoothed with a Gaussian of σ = 0.8 m.
- Tree tops are local maxima with a suppression radius of 1.5 m + 10% of
  height. Crowns come from a marker watershed, capped at 0.4·h + 2 m radius.

**Admission.** A tree is admitted only if all of these hold:
- crown area of at least 5 m² and height of at least 4 m;
- the crown is volumetric: height spread of at least 0.6 m, and the top
  minus the 10th-percentile height of at least 1.5 m;
- the top is not within 2 m of a building;
- the trunk lands on a column that is not road, building or water. The trunk
  may shift up to 3 m inside its own crown to find one.

**Blocks:**
- **Leaves:** the upper canopy surface is the observed CHM. The underside is a
  mirrored ellipsoid; conical crowns instead go down to the crown base.
- **Trunk and branches:** oak or spruce logs. The trunk runs from the ground to
  35% of crown depth, and broad crowns get 2–4 branches.
- **Leaf type:** chosen from crown shape: spruce for conical crowns, birch
  tint for narrow ones, oak otherwise. Real Chicago street trees are
  broadleaf, so all broadleaf trees get oak bark.
- **Leaf state:** `persistent=true`, with `distance` computed as the game does.
- **Thinning:** about 18% of exposed edge leaves are thinned deterministically
  so crowns do not read as cubes.
- **Tree pits:** where a trunk stands on paving, the surface becomes
  `coarse_dirt`.
- **Placement:** leaves and logs go only into air, never onto building columns.

**Evidence:**
- Observed: canopy surface, position, height and footprint.
- Estimated: trunk, branches, underside and leaf type.

**Result:**
- 878 candidate tops, of which 461 trees were admitted: 283 oak-shaped, 165
  narrow, 13 conical. There were 391 tree pits.
- Heights at P10 / P50 / P90 are 5.7 / 9.5 / 16.9 m.
- Rejections: 153 near a building, 89 crown too small, 37 planar, 27 too few
  returns, 13 with no admissible trunk cell, and 98 in the halo.
- 44 of the 84 OSM `natural=tree` nodes lie within 3 m of a LiDAR top. The
  rest are mostly young trees planted after 2017, or crowns merged into a
  neighbour.

### 3. Street (`detail_street.py`, layers `street` and `streetlights`)

**Curbs (measured).** For every OSM drivable way, the 0.5 m LiDAR ground is
profiled perpendicular to the way every 2 m. The road crown falls toward the
gutter, so a rise of at least 0.08 m within 1 m marks the curb. The median
offset per side becomes the carriageway edge.
- 127 of 238 road sides were measured.
- The rest, mostly outside the LiDAR halo, use an OSM-lanes estimate:
  lanes × 3.2 m plus 2.4 m of parking, labelled estimated.
- Michigan Avenue's medians come out as measured curbs.

**Sidewalks.** A sidewalk cell is paved world ground that is:
- not carriageway, built, a footprint, a service road or alley, or parking;
- within 14 m of a curb, unless OSM maps it as a plaza.

Sidewalks become `smooth_stone`. The curb row becomes `polished_andesite` and
stays flush, so there is no geometry change. Green orthophoto cells and OSM
grass become `grass_block`.

**Crosswalks.** These come from OSM `footway=crossing` ways inside the
measured carriageway, as 1 m bars alternating along the walking direction,
or as edge lines for `lines`.
- A crossing counts as observed when it has a markings tag and at least 15% of
  its bar cells carry LiDAR paint returns.
- The median paint support was 0.32.

**Lane lines.** Paint is detected where ground intensity exceeds the local
asphalt median by more than 9,000 and is above 30,000 in absolute terms.
Isolated single blocks are dropped.
- Lines are drawn at the offsets where paint concentrates along each way.
- Those positions are observed, while dash pattern and colour are estimated:
  yellow for the centre of two-way roads, otherwise white dashed at 3 m on and
  9 m off.
- 69 lines were observed and 61 estimated from `lanes`.

**Furniture.** Positions come from mapped points; the block shapes are
representative.

| Item | Placed | Blocks |
|---|---|---|
| Traffic-signal poles | 42 | Corner curbs nearest each signalised OSM node cluster |
| CTA bus stops | 12 | 9 with glass shelters and benches, using the OSM `shelter` / `bench` tags |
| Bike racks | 21 | — |
| Divvy stations | 3 | Kiosk plus docks |
| Post boxes | 9 | — |
| Bollards | 6 | — |
| Hydrants | 2 | — |
| OSM lamps | 9 | — |

**`streetlights` layer.** Of 261 inventory points in the tile, 196 were placed;
65 had no sidewalk cell within 3 m. 149 of the 196 have LiDAR pole returns
3–12 m high within 1 m. Each lamp is a polished-blackstone wall pole with a
hanging lantern on an arm over the road.

### 4. Facades (`detail_facade.py`, layer `facades`, five buildings)

This pass is appearance only. It recolours existing shell blocks that face
exterior air, and never adds or removes cells. It runs after regularization,
so the walls are closed.

| Building | Evidence for material | Rows | Cells (windows) |
|---|---|---|---|
| 875 N Michigan (Hancock) | OSM parts `building:colour=black`, `material=glass` → `black_concrete` frame, grey glass | 100 levels / 337 m | 16,121 (6,779) |
| Water Tower Place | OSM `#A4A49C`, `glass` → `smooth_stone`, black glass | 74 levels / 261 m | 25,928 (11,408) |
| Olympia Centre | OSM `#9C8679` → `light_gray_terracotta` (reads as red-brown granite) | 63 levels / 218 m | 18,979 (4,681) |
| Park Tower | Material unknown; existing block kept | 70 levels | 4,017 (1,106) |
| Lewis Towers | Material unknown; existing block kept | 15 levels | 2,241 (632) |

- **Colour:** mapped to the nearest block in CIELAB from mean texels measured
  in the client jar.
- **Window rows:** derived from floor height (height ÷ levels).
- **Window columns:** a fixed module of 3 m for curtain walls and 2 m
  otherwise. Columns are always marked **estimated**.
- **Glass colour:** windows use darker glass than the wall, because the shells
  are hollow and pale glass would show the back wall.
- **Material evidence:** the material rows are "observed" only in the sense of
  being *mapper-tagged*. No street image was registered to any wall.

OSM says Water Tower Place is glass, but it is mostly marble. The tag is
followed and the conflict is noted here. A registered photograph should
overrule it.

`observe_from_image()` is the street-image path. Given a rectified crop of a
wall, it returns a trimmed-median wall colour and the window rhythm by
autocorrelation. Registration comes from `street_photo_projection.py`. The
function is unit-tested on synthetic facades, but no real imagery exists in
this tile.

### 5. Water (`detail_water.py`, layer `water`)

- **Extent:** the tile is split along the Lake Michigan relation's shoreline
  member ways. The side containing a lake seed point is kept, and the build
  fails if any land seed falls on the water side. Piers and breakwaters stay
  land.
- **Surface:** OSM `ele=176` (m) maps to Y 60 in the frame.
- **Bed:** an estimated shelf, 2 m deep at the shore and 1 m deeper every
  40 m, down to 10 m. The bed block is sand.
- **Result:** 109,327 water columns were placed on `r.1.0`. Ohio Street Beach
  and the breakwater stay land.
- **Planned refinements:**
  - Use NOAA Great Lakes bathymetry (public domain) for the bed.
  - Take the surface from LiDAR class 9 at survey date.
  - Rivers and harbour basins follow the same pattern with OSM `natural=water`
    polygons.

## Verification

- **Tests:** 20 offline tests in `tests/test_detail_layer.py` and
  `tests/test_detail_passes.py`. They cover:
  - codec round trips and packed-long widths;
  - byte-identical untouched chunks;
  - heightmap and light reset;
  - guard caps and refusal to write into a save;
  - precondition semantics;
  - tree detection and voxels, curb profiles, zebra crossings, the lamp shape
    and facade rhythm;
  - regularization (hole closed, clutter and fragment removed, no height
    change);
  - the shoreline split with a land-seed check.
- **Structure:** all four edited regions plus the water region pass
  `verify_region`.
- **Vanilla game:**
  - **Setup:** an isolated world containing the copied `level.dat`, the world
    datapacks (the 1,024-block `earthcraft_height` dimension is required) and
    the edited regions. It ran on the hash-checked Mojang 1.21.10 server
    (`vendor/minecraft-server-1.21.10.jar`, sha1 95495a7f…).
  - **Run:** the server force-loaded all 1,024 chunks of the tile, saved, and
    reloaded, twice, with no errors.
  - **Persistence:** every detail block persisted through the game's own save.
    Counts before and after were identical for leaves, smooth stone, lanterns,
    paint, glass and tree pits. The only change was 187 grass cells under new
    trunks and walls decaying to dirt.
  - **Water region:** the edited `r.1.0` was loaded the same way, and its
    471,222 water cells persisted.
- **Integrity of the first apply:** the composed layers applied 1,123,534
  edits with 510 failed preconditions and changed 1,025 chunks. The failures
  are leaves or furniture blocked by objects placed earlier.

## What worked and what did not

**Worked:**
- **Ground LiDAR does most of the street.** It gives curbs and the
  sidewalk/road split from the profile, paint from intensity, trees from the
  CHM, and pole confirmation for lamps.
- **OSM is unusually rich downtown.** Separately mapped sidewalks, crossings
  with markings, bus stops with shelter and bench tags, and building-part
  colours and levels.
- **Composable layers are safe.** Layers with preconditions apply in order,
  survive a real server, and keep untouched chunks byte-identical.

**Did not work:**
- **Street imagery.** Panoramax has no pictures in the area. Mapillary needs a
  token. Facades therefore rest on tags and floor counts. The image path is
  built and tested, but not exercised on real photos.
- **NAIP NDVI.** It is unusable from this service crop.
- **LiDAR vegetation classes.** In this survey they are height bands.
- **2017 LiDAR is old.** Newer trees, missing from it, account for most of the
  40 OSM-tree misses.
- **Furniture rendering.** It is representative: signal heads are one black
  block, and Minecraft cannot show bar or pole widths below 0.5 m except
  through walls, bars and panes.
- **Lane widths.** A 1 m grid paints 10 cm lines as 1 m stripes.

## Mapillary, once a token exists

1. Keep the token out of git and logs: use an environment variable
   `MAPILLARY_TOKEN`, sent as `Authorization: OAuth …`. Respect the documented
   limits (10k search calls per minute; tiles 50k per day) and the bbox limit
   of 0.01 deg².
2. Call `GET https://graph.mapillary.com/map_features?bbox=W,S,E,N&fields=id,object_value,geometry,first_seen_at,last_seen_at,images`
   per tile.
3. Map `object_value` to the existing furniture kinds, as derived edits with
   `source=Mapillary` and the feature and image IDs in the source row:

   | `object_value` | Furniture kind |
   |---|---|
   | `object--street-light` | `street_lamp` |
   | `object--support--utility-pole` | pole |
   | `object--traffic-light--*` | `traffic_signal` |
   | `object--fire-hydrant` | `fire_hydrant` |
   | `object--bench` | `bench` |
   | `object--bike-rack` | `bike_rack` |
   | `object--trash-can` | `waste_basket` |
   | `marking--discrete--crosswalk-zebra` | crosswalk confirmation |
   | `marking--discrete--stop-line` | stop bars |

   Deduplicate against OSM and the City within 2.5 m. Prefer the newer
   `last_seen_at`.
4. For facades, call `GET /images?bbox=…&fields=id,computed_geometry,computed_compass_angle,camera_parameters,camera_type,captured_at,thumb_2048_url`.
   Then:
   - Keep views whose ray hits a target wall's exterior face within 40 m, at
     an incidence angle under 60°.
   - Project wall cells with `street_photo_projection.project_camera`.
   - Rectify, mask transients, and call `observe_from_image`.
   - Admit colour or rhythm only when two or more independent positions
     agree.
   - Record image IDs, creator and the CC BY-SA 4.0 notice in the source row,
     with evidence `observed`.
5. Derived map features carry Mapillary's terms rather than CC BY-SA. Keep
   them in their own layer, as with the City streetlights, until the release
   licence is decided.

## Next steps

1. Run the passes per tile across the frame:
   - buildings and trees wherever EPT exists;
   - street wherever OSM roads exist;
   - the water pass for every OSM lake, river or harbour polygon, with NOAA
     bathymetry.
2. Seam check: build adjacent tiles with halos and confirm curbs and crowns
   agree on shared edges.
3. Window grids for all buildings with City `stories` or OSM levels. This is a
   policy decision, since the columns are generic. Today they are limited to
   the five prototypes.
4. Get a Mapillary token (account action by the owner). Then run step 4 of the
   Mapillary section on the 20-building fixture from `CV_BUILDING_POLISH.md`.
5. Replace the 2017 canopy with Cook 2022 originals once they are reachable.
   The EPT only has 2017.

## Reproduce

```sh
# 0. copies only (APFS clones on the save's host), then on a worker:
PYTHONPATH=scripts python scripts/detail_points.py --project USGS_LPC_IL_4County_Cook_2017_LAS_2019 \
  --frame frame.json --west -288 --north 289 --size 512 --halo 24 --out work/ept/cook2017
PYTHONPATH=scripts python scripts/detail_build.py --regions copy/region --frame frame.json \
  --points work/ept/cook2017 --sources work/sources --tile -256 -256 512 --out work/layers-b --layers buildings
PYTHONPATH=scripts python scripts/detail_apply.py --regions copy/region --layer work/layers-b/buildings --out work/staged
PYTHONPATH=scripts python scripts/detail_build.py --regions work/staged --frame frame.json --points work/ept/cook2017 \
  --sources work/sources --tile -256 -256 512 --out work/layers --layers trees,street,streetlights,facades \
  --facades facades.json
PYTHONPATH=scripts python scripts/detail_apply.py --regions copy/region --layer work/layers-b/buildings \
  --layer work/layers/trees --layer work/layers/street --layer work/layers/streetlights --layer work/layers/facades \
  --out work/after
PYTHONPATH=scripts python scripts/detail_water.py --regions copy/lake --frame frame.json \
  --osm lake_members.json --tile 512 0 512 --seed 1020 30 --land-seed 520 256 --out work/layers
PYTHONPATH=scripts python scripts/detail_render.py --region-dir work/after --jar 1.21.10.jar \
  --bounds -256 -256 256 256 --out work/after --views top --eye 50 90 230 0 -6
```

`sources/` holds the OSM halo extract (`osm_halo.json`) and the City GeoJSON
extracts named by dataset ID. Renders use the owned client jar's textures
only on the local machine; no textures are committed.
