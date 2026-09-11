# Earthcraft adaptation layer

Decision and implementation record, 2026-09-10.

Current run: finished local research and adapter diagnostic; no acquisition,
reconstruction or inference job remains active. No agents or hosted inference.
88 tests and Python compilation pass. Existing game remains untouched.
Internal free space checked at 28 GiB; no bulk data downloaded. Full adaptation
acceptance remains open; this record is the next-stage decision, not a claim that
the global Earthcraft goal is complete.

## Decision

Build a deterministic surface compiler, not a Minecraft LLM. Preserve original
LiDAR in metres; reconstruct only supported surface patches; attach dated,
registered image observations; compile separate appearance, visual geometry and
collision outputs. An address is a lookup key, not a camera calibration.

The immediate scope is Chicago Water Tower. The user now explicitly wants LiDAR
as input to adaptation; the older photo-only benchmark's prohibition on supplied
scan geometry does not apply to this new LiDAR-based product mode. Never describe
LiDAR geometry as recovered from photographs.

## End-to-end behavior

1. **Locate once.** Resolve an address or map pin to a geographic area, then match
   the actual building footprint/parts using geometry and a stable provider ID.
   Addresses may identify an entrance, parcel or business, not a unique structure.
   Preserve ambiguous matches. For an area build, query all features spatially
   once; do not perform one web search per building. Overture's GERS IDs can link
   building records, but its roofprints and heights retain their source limitations.
   [Overture building model](https://docs.overturemaps.org/guides/buildings/).
2. **Choose an epoch.** Use the structural survey's capture interval by default.
   Keep acquisition, publication and retrieval dates separate. Each source gets
   a date interval, geographic extent, resolution, rights record and content hash.
   Prefer verified comparable dates, not whichever image was uploaded most recently.
   Same-year or overlapping intervals do not establish simultaneous capture.
3. **Acquire by tile.** Fetch bounded LiDAR, elevation, orthophoto and permitted
   street/oblique imagery around the area. Clip after a context halo so a building
   crossing a tile edge is not truncated during fitting. Cache shared assets once.
4. **Construct metric surfaces.** Normalize datum/units, exclude withheld/noise
   returns, separate terrain and above-ground clusters, then associate those with
   footprints. Estimate normals; fit finite roof/wall patches using local plane
   fitting and supported triangulation. Do not snap every structure to axis-aligned
   boxes or extend an infinite fitted plane through an unobserved opening.
5. **Register photographs.** Undistort with calibrated lens parameters. GPS and
   heading initialize a pose search. Use classical feature matching, geometric
   outlier rejection, multiview triangulation and pose refinement against supported
   structural features. PnP requires 2D–3D correspondences; silhouette overlap alone
   is insufficient, especially for symmetric towers. Retain spatially separated
   holdout controls/views. [OpenCV PnP](https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html),
   [COLMAP workflow](https://colmap.github.io/tutorial.html).
6. **Paint visible surfaces.** For each surface sample X, project with
   `p_camera = R X + t`, then `u,v = K p_camera / p_camera.z`. Check positive depth,
   image bounds, facing angle, projected resolution, static-object masks and a
   depth-buffer agreement test. Missing occlusion evidence is unknown, not proof
   that the surface is visible. Keep source image ID and pixel coordinates with
   each painted patch. Windows are painted only where visible, not inferred from
   darkness or repeated architectural patterns.
7. **Compile Minecraft assets.** Produce textures/block materials, fine visual
   geometry, collision geometry, geographic metadata and an uncertainty report.
   Build a new save and verify it before installation. Existing player edits live
   in a separate overlay; regeneration does not overwrite an open or edited save.

## What each source can contribute

| Source | Appropriate use | Not evidence for |
|---|---|---|
| Airborne/terrestrial LiDAR | Measured terrain, roofs, visible wall points, surface constraints | Complete facades, glass, colours or unseen interiors |
| Orthorectified aerial RGB/NIR | Ground appearance, visible road/vegetation boundaries, some roofs after alignment checks | Vertical facade textures or guaranteed true roof position |
| Street/oblique photographs | Visible wall colours, windows, surface detail; geometry from suitable overlapping calibrated views | Rear faces, unseen openings, arbitrary depth from one photograph |
| OSM/municipal vectors | Building identity, road topology, explicit widths/heights, bridges/layers | Uniform accuracy, inferred floor heights as measured metres |
| Global elevation | Terrain/mountains at source resolution | Buildings or metre-detail road surfaces |
| Broad satellite land cover | Landscape context and coarse land-cover zones | Window patterns, kerbs, lanes, small trees |

NAIP is aerial photography, not satellite imagery. USGS describes the change to
0.6 m resolution in 2018, with 0.3 m options; inspect each asset, not a universal
assumption. Our cached Chicago source is 0.6 m, captured 2019-08-02.
[USGS NAIP](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-aerial-photography-national-agriculture-imagery-program-naip).

Sentinel-2's bands have 10/20/60 m sampling. It helps broad landscapes, not a
one-metre street layout or facade. Upsampling to one metre does not add evidence.
[Copernicus Sentinel-2](https://dataspace.copernicus.eu/data-collections/copernicus-sentinel-missions/sentinel-2).
AWS Terrain Tiles provide global bare-earth heights, not a global building scan.
[AWS terrain](https://registry.opendata.aws/terrain-tiles/).

There is no single source in this plan providing a current dense facade scan of
every building on Earth. USGS provides regional point-cloud coverage; query its
availability. Spaceborne GEDI has roughly 25 m footprints, not a continuous
centimetre-detail city surface. [USGS LiDAR access](https://www.usgs.gov/faqs/what-lidar-data-and-where-can-i-download-it),
[NASA GEDI product](https://doi.org/10.5067/GEDI/GEDI02_A.002).

Google Map Tiles is not our extraction backend: its published policy explicitly
restricts image analysis, machine interpretation, geodata extraction and offline
uses. A browser view is not authorization to turn those assets into an offline
Minecraft world. [Google policy](https://developers.google.com/maps/documentation/tile/policies).
Use permitted municipal/open assets, appropriately licensed street imagery, or
user-supplied photographs. Mapillary describes image reuse under CC-BY-SA; API
access, exact asset terms, attribution and derivative obligations still need to
be retained. No credential or paid acquisition was assumed this turn.
[Mapillary licensing](https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data).

## Source router: hard compatibility before ranking

Reject wrong building/surface, incompatible rights, unsupported datum, changed
structure, unusable camera registration, occlusion or insufficient resolution
before ranking appearance candidates. Then prefer date compatibility, lower
registration uncertainty, better projected resolution, stronger view angle and
less shadow/obstruction. These are deterministic rules, not LLM confidence.

Choose a source per coherent wall/roof patch, rather than independently per pixel,
to reduce seams. Merge overlapping images only after agreement checks; do not
average contradictory epochs into one facade. Retain photographic lighting unless
a measured cross-view exposure adjustment supports correction. A dark region is
not automatically asphalt, glass or a window.

Date proximity is a candidate-ranking signal, not automatic approval. A facade
can change during a short interval; an old photo may depict unchanged stone.
Cross-epoch use requires explicit change checks and retains both dates. Unknown
dates remain unknown. A source with only a year has up to a year's uncertainty.

## Ground and roads

Use ground-classified points/DTM for heights. Use road polygons or the existing
provider-verified road classifications for ownership and extent. Preserve bridges,
tunnels and grade separation. Centre lines need a supplied width or an explicitly
inferred width, never a hidden default advertised as measured.

Sample orthophoto colour only on the visible ground surface after registration,
occlusion and temporal checks. A pixel over a road can show a car, tree canopy or
building shadow. Overhead raster draping is an established operation, but does
not by itself solve these errors. [PDAL colorization](https://pdal.io/en/latest/stages/filters.colorization.html).
The prior Water Tower NAIP alignment/mask diagnostic failed; this design does not
silently re-admit it. Preserve 1,485 observed road cells in Explorer-v4 until a
new appearance experiment clears its own validation.

## Rendering decision: same metre scale, finer local shape

One block per metre constrains coordinates, not texture resolution or every
visible surface to full cubes. Two export modes should share the metric scene:

- **Vanilla-compatible:** choose blocks/slabs/stairs from supported geometry,
  with a bounded resource-pack/detail layer. Explicitly report approximation and
  collision limitations. Item displays are useful for a small prototype but are
  not the worldwide rendering architecture.
- **Detailed explorer:** a small custom client/server mod renders chunk-batched
  surface meshes or microvoxel geometry and supplies matching local collision
  shapes. Retain fine supported features nearby, coarser silhouettes farther away.
  Ground still behaves as ordinary Minecraft terrain. No player/world rescaling
  is needed. Travel controls remain independent.

The existing experiment put finer surfaces inside opaque metre cubes, so the
detail could not be seen. The detailed renderer must suppress the replaced
coarse visual faces and own collision explicitly. Keeping invisible full-cube
walls would obstruct real-looking doorways; rendering-only decoration would let
the player walk through walls. Neither is an acceptable hidden compromise.

Start with quarter-metre candidate geometry where support permits; do not force
one-sixteenth-metre geometry everywhere. Texture sampling can be finer than
geometry, but must not claim finer photographic evidence than available pixels.
Plane patches and mesh interpolation are labelled derived, not new laser returns.

## Scaling and replay

Use a content-addressed tile job graph: acquisition → normalized observations →
surfaces → registration → appearance → Minecraft assets. Hash the exact source
bytes, transforms, capture intervals, parameters and compiler version. Recompute
only affected downstream tiles. Assign cross-boundary buildings one owner and
shared seams; retain a halo. Deduplicate shared photographs and skip near-identical
video frames. Batch meshes and atlas textures per chunk/region, not one entity or
network request per texel. Stream cached detail around the player with a bounded
memory/disk working set. Fast travel must request ahead and expose loading, not
pretend unscanned areas are reconstructed.

A flat Minecraft chart cannot preserve all global spherical distances at once.
Use an atlas of local metre charts with explicit geodetic transitions/origin
rebasing, or disclose distortion for a single global projection. Earth-scale
navigation is separate from a one-block-per-metre local reconstruction.

## Implemented and exercised this turn

- `appearance_adapter.py`: capture intervals and uncertainty-aware ranking;
  vectorized calibrated projection, facing/occlusion tests, masks, transparent
  unknowns and source-pixel coordinates. No LLM, pixel invention or geometry edit.
- `naip_imagery.py`: optional actual-capture-date selection, rejecting missing
  acquisition timestamps in that mode; keeps the legacy year option compatible.
- `facade_photos.py`: explicit Commons date normalization while retaining raw
  source strings. Does not substitute upload time for capture time.
- `water_tower_adaptation_probe.py`: verifies cached assets, ranks all six photos,
  measures four geometry resolutions and exercises painting on the real existing
  experimental patch. Outputs to `runs/water-tower-adaptation-002`.

The diagnostic uses 24,644 points from the existing building selector, not every
point in the tile. At fixed world-grid origin, point-to-cell-centre p95 falls from
0.702 m at 1 m cells to 0.173 m at 0.25 m cells and 0.085 m at 0.125 m cells.
These are quantization measurements against input points, NOT geographic accuracy
or proof that a finer sparse cloud is a complete building.

The closest dated cached photograph is 2022-03-21, between 15 and 100 days before
the 2022-04-05…2022-06-29 survey interval. The current west photo is 2025-08-19,
between 1,147 and 1,232 days after the survey. Both remain unverified camera poses.

The new painter accepted 333 of 1,894 candidate samples in the first real-input
run with a 0.25 m depth tolerance, about 4 ms for sampling alone. This deliberately
does not imply the acquisition/registration pipeline runs in milliseconds.
The historical point-depth splat and silhouette mask are experimental inputs;
these results are not admitted to the game as a more accurate facade.

## Next implementation and acceptance

Next substantive branch: a chunk-batched fine-surface/collision prototype on the
same Water Tower, compared to Explorer-v4 at fixed street and aerial viewpoints.
Parallel conceptually, but not delegated: solve the closest-date photo using
independent correspondences or obtain a permitted overlapping capture sequence.
Repeating the same silhouette fit or repainting the old patch is not progress.

Promotion requires actual game screenshots, supported-surface coverage and error,
spatially held-out geometry/photo checks, no hidden scaled coordinates, correct
doorway/wall collision, unchanged ground/road geometry, repeatable assets and
measured frame time/memory. Source disagreement and missing surfaces remain in
the denominator. No claim of 100% accuracy or all-Earth completion from this pilot.

Existing playable Explorer-v4 was not modified. The integrated detailed renderer,
global address discovery and general automatic facade registration are still
implementation work, not features delivered by this research/core-adapter turn.

## Chicago expansion follow-up

The user subsequently approved expansion to the whole Chicago municipal area,
using the accepted Water Tower appearance as reference. Current implementation,
the shared metre grid, exact tile-join tests, real-scan game regression and bulk
storage dependency are recorded in `docs/CHICAGO_ADAPTATION_GOAL.md`. The 9,639
planned tiles are not generated or facade-painted city coverage.
