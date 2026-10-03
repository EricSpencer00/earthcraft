# Street-level fidelity plan: Elmhurst and Chicago

Reviewed **2026-10-01**. The goal is to make a familiar street recognizable
from Minecraft's walking view: the right building colors, window spacing,
entrances, storefronts, roof materials, and street furniture, in their current
locations. “+1 Street View” is a proposed product milestone, not a measured
claim that metre-sized blocks outperform photographs.

## Decision

Keep the inherited Chicago frame and current terrain/building geometry. Add
registered, source-backed appearance in two forms: color/material/window bands
in ordinary blocks, and higher-resolution façade textures close to the player.
Use new reconstruction only for specific geometry defects with independent
evidence. Do not rerun the whole region's geometry merely to color its walls.

The full generated save is being snapshotted separately under the
[private world dataset workflow](WORLD_DATASET.md). The photo-quality pilot
uses a new staging copy and does not alter that frozen upload.

## Current evidence and gaps

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Existing fixed frame and coordinate tests | Water Tower remains X=32, Z=33; Elmhurst shares the same metre frame | Independent accuracy of every building |
| Installed `scan-points-and-roof` receipts | Scan geometry/roof upgrades exist | Complete measured façades; `facade_measured` remains false |
| `orthofacade_adapter.py` and its offline tests | Observed pixels can map into the inherited frame using existing wall/ground anchors | Real Chicago imagery coverage, held-out registration, or a live importer |
| Existing Water Tower photo-skin experiment | A bounded photo overlay can preserve region bytes | A validated, scalable city-wide skin renderer |
| [Current Arnis façade arguments](https://github.com/louis-e/arnis/blob/main/src/args.rs) | Street-photo sampling, block colors, photo panels, and inspection outputs already have an upstream implementation | Compatibility with our pinned 3.1.0 binary or current save |
| [Orthofacade README](https://github.com/louis-e/orthofacade) | CPU-only metric rectification and cached per-wall output | Its own reported registration failures and weak confidence tiers mean A/B scores alone cannot approve a wall |

The old idea of filling every LiDAR gap is not the next step. Aerial scans are
useful for roofs and height but cannot supply missing wall colors or all ground
floor detail. Visible spikes, floating patches, or a ground-relative height
failure must be diagnosed before appearance is applied to that building.

## Source choice

Start with Mapillary and permitted municipal/user-owned street photographs.
Mapillary publishes its imagery under CC BY-SA; retain the image URL, creator,
license/version, capture date, source hash, and transformation record for every
derived texture. [Mapillary license documentation](https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data)

Google Street View sets the desired visual quality; it is not the ingestion
source for this plan. Google's standard Platform terms restrict extraction and
creating content from Maps content. A different explicit license would need
its own integration decision. The benchmark photos used for fitting and
held-out evaluation will also come from permitted sources.
[Google Maps Platform terms, section 3.2.3](https://cloud.google.com/maps-platform/terms)

An October 2 bounded discovery probe found 358 nearby Elmhurst images and 250
downtown images. The Elmhurst sample used ordinary perspective cameras;
downtown included 67 panoramas. These counts are discovery samples, not a
measurement of visible facade coverage. Two actual source photos and their
calibrated multi-view reconstructions were projected on a private CPU worker:
11,827 Elmhurst point/color candidates and 41,735 downtown candidates. None
has been admitted into the world. Sparse reconstruction observations do not
prove facade ownership, occlusion, or the inherited world datum.

`street_photo_projection.py` supports the measured OpenSfM perspective lens
and spherical panorama models. `street_photo_candidates.py` retains source
hashes, capture time and unregistered observations. Its outputs require an
independent LiDAR alignment, static-surface masks, depth checks and source
attribution before a color delta can be generated. This also permits ordinary
street photos to contribute where panoramas are unavailable.

Before any large download, map *usable street-facing wall coverage*, not just
the number of panoramas. Usable wall coverage remains unmeasured. If a façade
has no usable photograph, retain its current material and
report the gap. Do not copy another building's façade onto it. Standard Arnis
`--building-facades` presets are a stylistic fallback, not real-building evidence.

## First experiment: two neighborhoods, twenty buildings

Use two 256 m pilot charts in the existing frame, with source context outside
the scored boundary where needed:

| Pilot | Centre (longitude, latitude) | Features to test |
|---|---|---|
| Elmhurst planning anchor | -87.9403, 41.8995 | Brick, pitched roofs, low-rise storefronts, entrances, tree/car occlusion |
| Chicago Water Tower/Michigan Avenue | -87.62443, 41.8972 | Historic stone, glass towers, setbacks, repetitive bays, urban street furniture |

Select ten buildings per chart after the coverage probe, including failures
and occluded examples. Freeze their IDs and selection before tuning. Buildings
without photos stay in the coverage denominator. Reserve distinct photos from
different positions for validation; splitting one panorama into crops does not
create independent observations. Keep a 100 m walking route and fixed cameras
in each chart so before/after comparisons use the same position, yaw and FOV.

Deliver an inspection sheet for every building: source photos, camera/wall
alignment, masks, colors, window candidates, unknown area, and a matching
Minecraft screenshot. Keep actual capture dates visible; conflicting dates
must not silently become a single supposedly current façade.

## Implementation order

| Milestone | Work | Concrete deliverable |
|---|---|---|
| 1. Coverage and geometry audit | Probe photo metadata; match stable OSM/footprint IDs to existing walls; inspect spikes, roof/base heights and missing shells | Twenty-building frozen fixture plus usable/unknown façade coverage map |
| 2. Broad colors | Reuse the pinned Orthofacade/Arnis sampling approach; reject foreground objects; normalize exposure across overlapping views; robustly estimate wall/roof colors | Color-only staged world and per-wall source receipts |
| 3. Architectural identity | Register multiple translated viewpoints; admit observed window/floor/entrance patterns; quantize to appropriate full-cube materials | Block façade layer that remains recognizable without a photo pack |
| 4. Close detail | Bake observed sign, masonry and window detail into bounded façade panels; merge the atlas/models into the existing pack | Small pilot photo-skin pack plus identical geometry and collision |
| 5. Street context | Improve measured sidewalk/curb, crosswalk, lamp, tree and fence placement/appearance from licensed GIS/photos | Both walking routes show the correct surroundings, with gaps reported |
| 6. Existing-world import | Build a source/coordinate-checked appearance delta; preserve player edits and pre-existing photo anchors; stage and verify before promotion | Repeatable importer, rollback bundle, and successful Java 1.21.10 game load |
| 7. Regional rollout | Process neighboring source-covered tiles first, then expand by measured coverage and cost | Incremental appearance receipts, chunk updates and HF snapshot versions |

First implement color-only appearance. It is inexpensive and helps the view at
every distance. Photo panels cannot conceal a misplaced building. Door imagery
does not automatically become a functional door or remove a wall block.

### Reuse boundary

The adapter is pinned to Orthofacade revision
`a24e4ca349a1ceecec5c64517e62b6828f4aebac`. Inspect and pin the Arnis sampler/atlas
components used by the pilot rather than upgrading the entire world generator.
Keep their license notices. Compare the Python candidate output to Arnis's
block/photo rendering on the same fixture before choosing an exporter.

Current Arnis OneWorld explicitly disables façade panels because its per-run
resource pack would replace the prior pack. Earthcraft must merge texture/model
namespaces and preserve the existing Water Tower assets; calling that mode
directly on this save is not the integration.
[Arnis OneWorld limitations](https://github.com/louis-e/arnis/blob/main/docs/one_world.md)

Building colors select per-cell materials; custom photo models use their own
namespace. Replacing shared vanilla block textures would recolor unrelated
buildings and inventory icons, so it is not the appearance update mechanism.

The existing Water Tower prototype also changes its isolated experiment's
initial camera. Reuse its face/UV and visibility logic, not its world-copy/spawn
setup, when building the live importer.

### Coordinates and support

WGS84 endpoints project into the existing Transverse Mercator frame. World X
increases east and Z increases south. Use the existing measured ground for
each façade's base Y; photo-cloud local Z is not NAVD88. Never recenter a tile,
scale a tall building, or move a wall to make its photograph fit.

The upstream PNG's alpha channel encodes wall/window/door classes. Visibility
comes from its separate `observed` mask. Filled pixels and hidden walls remain
unknown. Validate the source plane, wall ownership, endpoints and heights
before generating a texture. Missing independent registration leaves the
candidate unadmitted, as the current adapter does.

## What qualifies as “+1”

These are **proposed pilot acceptance targets**, not current results:

| Check | Target |
|---|---|
| Geographical placement and collision | No change from accepted baseline; geometry fixes require a separate receipt |
| Unsupported appearance writes | Zero outside the visibility/ownership mask; no copied rear façades |
| Registration controls on held-out views | Median error ≤0.5 m, 95th percentile ≤1 m; report each wall rather than hiding failures in an average |
| Held-out facade color | Median CIEDE2000 error ≤10 after a fixed illumination normalization; retain raw and normalized comparisons |
| Window/entrance pattern | ≥85% precision and recall on visible, resolvable held-out features, using a stated metre-grid tolerance |
| Street-facing coverage | ≥80% of visible wall area on each pilot route; report occluded area and missing-source buildings separately |
| Visual identity | At least 8/10 buildings in each pilot identifiable from a blinded Minecraft/photo matching sheet; no worse shape/placement failures |
| World integrity | Frame, spawn, inventory and unrelated chunk state unchanged; protected edits survive; replay is idempotent |
| Client performance | ≤10% increase in 95th-percentile frame time and a proposed ≤128 MiB extra texture budget on the actual client |

Evaluate the **color/block version** and the **block-plus-photo version**
separately. A photo facade passing one front-view comparison is insufficient;
also check oblique walking views, seams, incorrect occlusion and night lighting.
The target is a coherent playable street, not a screenshot that works from one
camera. If a target fails, publish the failure in the pilot report and keep that
wall's previous appearance.

## Fast generation and distance rendering

Run the pilot on the Mac mini/LaCie. Keep the MacBook for coordination and
client inspection. Begin with two CPU workers and a measured memory limit,
not an unbounded city-wide CV job. Use metadata/low-resolution imagery for
selection and fetch larger images only for the best views of a wall. Reuse
camera, geometry, source-image and texture results keyed by content hashes.
Shared panoramas are fetched once; an appearance change need not rebuild terrain.

Skip dense city-wide photogrammetry initially. Multi-view geometry is useful
for a specific defective landmark, not a prerequisite for broad colorization.
Monocular depth is at most a locally checked secondary cue, not LiDAR evidence.

Use this proposed client detail policy:

| Distance from player | Representation |
|---|---|
| 0–64 m | Actual colored blocks plus supported 8–16 px/m photo detail where available |
| 64–256 m | Colored blocks, major window bands and roof material; fade photo panels |
| Beyond ordinary chunks | Distant Horizons geometry with coarse colors derived from the real block layer |

Measure and tune these distances on the actual client. Use panels per wall or
small wall segment, not one display entity per block. A vanilla resource pack
loads its texture assets globally: geographic atlas files alone do not provide
lazy loading. Keep the initial pack small. City-wide high-resolution skins
require a separate client renderer with distance-based texture-page loading,
an LRU memory cap, eviction and asynchronous IO, or must remain limited to
selected landmarks. Prototype that renderer only after the basic pilot passes.

Clip every panel to the existing exposed-face/occupancy mask. Flat billboards
must not bridge missing geometry, courtyards or setbacks. Nonplanar walls need
segmented panels or a batched face mesh in the later renderer.

Cache near textures separately from blocks; the far view must still look right
when the photo renderer is absent. Source, texture and import jobs should be
interruptible and resume without reprocessing completed walls. Record cold
fetch time, cached processing time, bytes per building, peak memory, FPS and
admitted coverage before setting a region-wide throughput target. Upstream
timings from Munich are not a Chicago throughput guarantee.

## Next implementation step

Complete the two-area coverage probe and freeze the twenty-building fixture.
Then ship **only the color-only pilot** through the existing-world appearance
delta path. Validate the unchanged frame and both walking routes before adding
windows, photo skins or broadening the area. No account action is required to
approve this plan; authenticated imagery access is resolved securely when the
probe actually needs it.

## Existing deterministic methods

Earthcraft can make buildings look more deliberate without asking a language
model to invent façades. The reliable route is to treat every visible detail as
an observation with a footprint, a source, and an uncertainty mask. Classical
computer vision then turns those observations into bounded Minecraft changes.

This is a polish layer on top of the existing metric world. It must never
replace measured terrain, move a mapped footprint, or fill a surface that the
source did not observe.

## 1. Keep the source lanes separate

Use three aligned lanes for each building:

1. **Geometry:** county/LiDAR returns, explicit building footprints, roof
   planes, and surveyed heights.
2. **Appearance:** permitted orthophotos or user-owned photographs with a
   camera record, date, and image hash.
3. **Context:** roads, parcels, water, vegetation, and address/landmark
   metadata.

The lanes share a coordinate frame but not assumptions. A colour observation
cannot create a wall, and a LiDAR gap cannot be filled with a plausible roof.
The output receipt records the input hashes, coordinate transform, supported
pixels/cells, and the reason for every abstention.

## 2. Register images before classifying them

For each image, solve a bounded planar registration against the measured
footprint and orthophoto:

- undistort with the supplied camera calibration;
- identify stable control points or line intersections;
- solve a homography only when the residual stays below the configured metre
  threshold;
- reject images with too few controls, unstable scale, or a vertical/horizontal
  epipolar mismatch;
- keep an explicit visibility polygon and an occlusion mask.

The registration result is evidence, not a license to extrapolate. A failed
image remains in the record as unavailable rather than becoming synthetic
texture.

## 3. Recover roof and wall structure from measured signals

Use deterministic geometric operators in this order:

- crop LiDAR to the admitted footprint;
- remove ground with the existing terrain surface;
- cluster returns by connected support and height;
- fit roof planes with robust RANSAC, retaining residuals and inlier masks;
- derive wall edges from the footprint and vertical return density;
- intersect roof planes with the measured wall envelope;
- preserve courtyards, setbacks, and holes as empty support.

The roof worker can emit a candidate plane, but `building_layer.py` should admit
it only when the footprint, provider height, and support mask all agree. When
they do not, retain the original observed maxima and mark the roof unknown.

## 4. Add façade polish from classical image features

Appearance can be improved without semantic guessing:

- convert registered pixels to Lab and HSV;
- estimate robust wall colour from trimmed medians, not single pixels;
- use local variance, gradient magnitude, and Gabor/edge responses to separate
  smooth masonry, repetitive windows, roof material, and vegetation-like noise;
- detect repeated vertical/horizontal bays with autocorrelation or a Hough
  transform;
- accept a repeated feature only when it persists across independent images or
  across a sufficient run of scanlines;
- quantize the result to a small, documented Minecraft palette with an
  uncertainty/unknown class.

This produces broad material fields and supported window/door bands. It should
not draw individual windows into an occluded wall, infer interiors, or paint a
generic pattern merely because a building type usually has one.

## 5. Voxelize conservatively

Compile the accepted observations into the existing one-block-per-metre frame:

- geometry writes are clipped to the measured footprint and roof support;
- appearance writes may change block choice or a bounded photo/detail layer,
  but may not add or remove structural cells;
- unsupported cells retain the base material or stay unknown;
- every changed cell points to its source receipt and region hash;
- protected/player-edited chunks are excluded by the live publisher.

The result should continue to pass the existing world readback and replay
checks. A polished building with lower coverage is better than a complete
looking building made from invented detail.

## 6. Measure polish independently

Track separate metrics rather than one subjective score:

- roof-plane residual and height error;
- footprint boundary error and occupied-cell intersection-over-union;
- registration residual in metres;
- material classification agreement on held-out pixels;
- supported façade coverage and unknown/occluded coverage;
- changed-block count outside the evidence mask (must be zero);
- replay hash and live-import receipt integrity.

Keep a small held-out set of buildings and images. A change is promotable only
when it improves the intended metric without increasing unsupported geometry or
breaking replay determinism.

## 7. Fit it into Earthcraft

The current code already provides the useful boundaries:

- `chicago_lidar_refinement.py` and `building_layer.py` for measured structure;
- `metric_source_crop.py` and `metric_world.py` for aligned terrain and voxel
  output;
- `appearance_adapter.py` for bounded appearance records;
- `live_city.py` for immutable, content-addressed chunk publication.

A future polish stage should consume immutable source/geometry receipts, emit a
new appearance receipt, and run before live publication. It should carry
`llm_used: false`, preserve provenance hashes, and fail closed on missing
calibration, unsupported geometry, or changed source bytes. The self-hosted
LaCie CI workflow can run these CPU-bound stages in bounded batches while the
live Minecraft handoff remains a separate, edit-safe process.
