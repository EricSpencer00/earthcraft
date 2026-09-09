# Earthcraft implementation plan

Prepared 2026-09-09. Status: design complete enough to start a bounded prototype; no implementation or performance results yet.

## 1. Outcome and scope

Select a real-world area, acquire its available geographic observations, infer useful missing exterior attributes locally on a Mac, and export a playable Minecraft world at one block per metre.

The initial scope is terrain, roads, water, building exteriors, roofs, vegetation, and major visible street objects. The map covers the complete selected bounding area. Interiors, underground infrastructure, hidden courtyards, and occluded façades can remain unknown. Later procedural interiors must be explicitly marked as invented.

This is a reconstruction system, not a screenshot-to-block painting system. The deliverable includes a quality report so a visually convincing result cannot hide misplaced geometry.

The first pilot is 256 × 256 metres, followed by 1,024 × 1,024 metres and then larger tiled areas. The location is the one pending user input. Until it is known, US public elevation is a candidate, not an assumption about actual coverage.

## 2. Decisions made now

| Decision | Choice | Reason |
|---|---|---|
| Repository | Local `/Users/eric/earthcraft`, branch `main` | Separate from HorneSci; no remote publishing implied |
| First edition | Minecraft Java | Installed locally; direct world files are useful for repeatable inspection |
| First version | Installed release 1.21.10, subject to writer smoke test | Avoid claiming arbitrary version compatibility |
| Scale | One block per metre in X, Y, and Z | User requirement; no adaptive stretching |
| Runtime | Native Python orchestration, MLX vision inference; optional PyTorch MPS depth | Work on Apple Silicon without assuming NVIDIA |
| Baseline | Pinned Arnis revision | Existing geographic generation and world export provide an early playable comparison |
| Reconstruction representation | Metric terrain plus semantic objects and optional local meshes | Preserve measurements until final voxelization |
| Default detail | Faithful exteriors; unknown interiors | Avoid presenting generative completion as recovery |
| Source policy | Independent adapters and independent evaluation | Each source loses different information |
| UI | CLI and generated review report first | Prove geometry and data quality before building an application |
| Distribution | Local artifacts | Dataset obligations and code license can be settled before a future release |

## 3. What 1:1 means

Use a projected coordinate system suitable for the selected area, in metres. Choose one origin and grid for the whole project. Store original longitude/latitude alongside projected coordinates. Use explicit axis order in transformations.

Map east to Minecraft X, south to Minecraft Z, and elevation to Y. For a point expressed as easting E, northing N, and height H, continuous block coordinates are:

```text
x = E - E_origin
z = N_origin - N
y = H - H_reference + Y_reference
```

The vertical reference is one constant for the whole world. Normalize source vertical datums and units before applying it. A constant offset preserves elevation differences; per-tile offsets would introduce discontinuities. If a datum transform is unavailable, record unresolved alignment and do not claim surveyed height accuracy.

Rasterize surfaces and volumes by cell intersection/coverage. Use nearest-point snapping only where appropriate for point objects. Nearest-grid-point rounding alone introduces at most half a metre per axis, but this is not a bound on total reconstruction error or on a coverage rasterizer. Thin objects, diagonals, stairs, fences, and slabs require separate representational tests.

A block grid cannot preserve every window mullion or curved surface at metre scale. Keep the continuous scene so a later exporter can use slabs, stairs, or textures without regenerating geometry. Default vanilla materials first; resource packs are an optional presentation layer.

For the vanilla height envelope, preflight the full ground-to-rooftop range and desired substrate. If it cannot fit, fail with a clear extent report. A later custom-height dimension is a distinct export profile; validate it separately. Do not flatten mountains or shrink skyscrapers to make an export succeed.

Check projection distortion at the area corners and representative control pairs. For the pilot, propose less than 0.1 metre of projection-induced distance discrepancy across its extent. A continent or globe needs a separate atlas/projection design: a flat Minecraft world cannot be a globally undistorted Earth surface.

## 4. Roles of AI and deterministic code

Codex/Astra is the development agent for writing, inspecting, and improving this software. The project does not assume that Astra weights run on this Mac. Production inference is a separately downloaded local model with a pinned revision.

Use deterministic geometry for coordinate transforms, building placement, terrain sampling, block occupancy, and world writing. Use local models for uncertain semantic observations: likely wall material, visible roof type, façade palette, window bands, and image quality/occlusion flags.

Model output is bounded structured data associated with an image region and object ID. It cannot directly execute tools, choose arbitrary source URLs, or emit unrestricted block edits. Validate the schema and allowed labels. A language model's stated confidence is not a calibrated probability.

Depth prediction is a secondary geometry cue. Even a metric-depth model needs local checks against known dimensions; do not let it overrule reliable LiDAR or surveyed geometry. One panorama cropped into many images provides angular coverage, not new camera translation for stereo triangulation.

Inference can abstain. More image crops of the same original are correlated evidence. Two providers may also share upstream data; repeated evidence is not independent confirmation.

## 5. Pipeline and interfaces

### A. Survey and acquisition

Input: a centre/bounding area, target date preference, source choices, and resource limits. Produce a coverage report before downloading large assets.

Each adapter implements discovery, bounded fetch, metadata extraction, and normalization. Save immutable source IDs, capture/acquisition dates, retrieval dates, licenses, checksums, resolution/accuracy metadata, original CRS and vertical datum, and any camera metadata. Downloads use limits, backoff, and resumable files. Downloaded HTML or image text is data, never instructions.

Fetch a 32 metre context halo around the pilot. Preserve full geometry for objects that cross the boundary, then clip only at export. Score the selected area, not the halo. Increase the halo only when a concrete object requires it and budget permits.

### B. Normalize and inspect sources separately

Keep vectors as vectors, rasters as georeferenced rasters, LiDAR as classified points, and imagery with its camera model. Do not collapse everything into a coloured point cloud immediately.

Validate polygon topology, units, transforms, no-data regions, timestamps, and bounding extent. Save source-specific preview artifacts and metrics before fusion. See the complete [source loss analysis](SOURCE_LOSSINESS.md).

### C. Build the measured base scene

Terrain is a tiled ground surface. Buildings are polygons/parts with explicit base height, roof height, and provenance. Roads preserve topology and elevation/layer relationships. Bridges are not painted onto ground; tunnels and road intersections need separate layer handling.

Water surfaces use constrained elevations appropriate to the source, with explicit treatment of banks and uncertain bathymetry. Vegetation uses observed canopy or mapped trees where available; generic landscaping is procedural.

An OSM `building:levels` value does not determine metres exactly. Any floor-height conversion is an inferred prior. A roof outline is not automatically a ground footprint.

### D. Attach imagery and local inference

Associate each image with candidate façades using camera pose, bearing, projected location, visibility, and distance. If pose is weak, require verification on the pilot rather than assigning the nearest building blindly.

Rectify façade crops when justified; retain the transform to original pixels. Perspective views derived from panoramas carry the original panorama ID. Mask sky, cars, people, reflective regions, and occlusions before estimating static materials or geometry.

Run a small local vision model on bounded crops. Output material classes, colour families, visible storey/window structure, roof observations, and an abstention reason. Save input crop hashes and exact prompt/model parameters.

Optional depth produces a depth map with validity masks and model metadata. Multi-view consistency and metric controls decide whether a depth-derived surface is admitted.

### E. Fuse with attribute-specific rules

There is no universal provider ranking. A recent image can correct a demolished building, while an old high-quality survey may still give better geometry for an unchanged façade.

For each attribute, retain alternatives and the reason for choosing one. Consider accuracy, recency, source lineage, occlusion, and alignment residual. Do not average incompatible geometry into a fictional building.

Use evidence states `observed`, `inferred`, `procedural`, and `unknown`. Store horizontal/vertical error estimates when defensible, plus coverage and conflict flags. Use missing values where uncertainty cannot be estimated. Persistent manual corrections are explicit overlays, separate from regenerated outputs.

### F. Voxelize and export

Compile the metric scene into chunk-local block commands with stable ordering. Palette selection considers surface type, brightness/colour family, block orientation, and nearby repeating patterns. Keep semantic materials rather than copying photographic shadows into walls.

Generate terrain, structures, façades, then supported details. Resolve intersections using explicit ownership and surface rules. Keep building shells hollow where interiors are unknown, and report that empty space as unknown rather than reconstructed interior.

Partition writes by region so two workers never mutate the same output file. Stage into a new world directory and atomically promote a completed export. Do not patch a world that Minecraft has open. Save attribution and reconstruction metadata beside the world.

### G. Validate and review

Read blocks back with an independent reader where practical. Check file structure, supported block states, chunk boundaries, heightmaps, lighting, spawn, and game load. Compare overhead views and fixed walking routes to source controls. A screenshot that looks right is not sufficient without geometry checks; file parsing alone is not sufficient without loading Minecraft.

## 6. Data model

Proposed objects, to become schemas during implementation:

| Record | Required content |
|---|---|
| `AreaSpec` | AOI geometry, project CRS, grid origin, vertical reference, scale, halo, target version |
| `SourceAsset` | Provider, upstream lineage, immutable ID, hash, capture/retrieval date, license, extent, resolution, CRS/datum, file path |
| `Observation` | Asset ID, object/surface ID, geometry or image region, attribute, value, method, evidence state, uncertainty |
| `SceneObject` | Stable project ID, source ID mappings, geometry, terrain relation, attributes, chosen observations, rejected alternatives |
| `InferenceRun` | Model repo/revision/hash, quantization, runtime versions, device, crop IDs, prompt hash, decode parameters, latency and memory |
| `TileManifest` | Global grid interval, halo inputs, dependency hashes, stage status, outputs, adjacent tile references |
| `WorldManifest` | Scene hash, exporter revision, game DataVersion, palette, generated extent, spawn, attribution bundle |
| `QualityReport` | Holdout definition, geometry metrics, evidence coverage, failure cases, visual comparisons, resource measurements |

Use GeoParquet for larger vector tables, GeoTIFF/COG for terrain and imagery, LAZ/COPC where supplied for points, JSON for manifests, and SQLite for stage/job indexing. These are proposed formats, not installed dependencies. Keep transforms and evidence accessible without loading a model.

## 7. Mac runtime and budgets

Read-only inspection on 2026-09-09 found Apple M1 Max, 64 GiB physical memory, about 39 GiB free on the data volume, and installed Minecraft versions 1.21.10 and 1.21.11-pre1. Python, uv, Rust/Cargo, and Git are present. Installation presence is not a runtime benchmark or a statement of package compatibility.

Start with Qwen3-VL-4B-Instruct through MLX-VLM, targeting a verified 4-bit conversion. Resolve the exact checkpoint and license at implementation time. Compare an 8B candidate only if the smaller model fails materially. MLX-VLM documents local Mac inference and Qwen3-VL support. [MLX-VLM](https://github.com/Blaizzy/mlx-vlm), [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL).

Depth Pro is an experimental depth adapter. Test PyTorch MPS availability, operator coverage, numerical validity, and latency before enabling it. Upstream GPU timing is not an M1 Max timing. [Depth Pro](https://github.com/apple/ml-depth-pro), [PyTorch MPS](https://docs.pytorch.org/docs/2.14/notes/mps.html).

Run one inference model at a time. Proposed initial project disk cap: 15 GiB, with at least 20 GiB remaining free. Allocate up to 6 GiB for a selected model/cache, 4 GiB for source assets, and 5 GiB for derived data and worlds. These are caps, not claims that every candidate fits. If a model/dependency footprint exceeds them, report before download and select a smaller viable candidate. Disable large persistent inference caches initially.

Target at most 24 GiB of process memory, including imagery and model allocations; measure actual memory pressure and swap growth too. Bound crops, tokens, raster windows, and LiDAR batches. Pause jobs when resource limits would be exceeded. City-scale work likely needs an external SSD or a revised storage budget; no automatic cleanup of unrelated files.

After source/model acquisition, test generation with network access disabled. No silent cloud inference fallback. There is no credible run-time estimate until the first measured pilot; the one-hour pilot budget is a proposed acceptance target.

## 8. Build versus reuse

Arnis provides existing map generation and Java/Bedrock support; its CLI is an immediate baseline. It does not by itself establish our façade accuracy, per-source uncertainty accounting, or chosen game-version compatibility. [Arnis](https://github.com/louis-e/arnis).

First test its exact scale, coordinate mapping, output version, and terrain behaviour. Wrap a pinned binary/revision for the baseline. Inspect whether its internals can accept a frozen scene and palette. For the enhanced path, prefer a narrow Rust extension or an existing maintained writer over a new implementation of Anvil/NBT. Do not let the baseline dictate inaccurate scene coordinates.

Keep Python for acquisition, geospatial operations, inference, and evaluation. Candidate libraries are pyproj, rasterio, shapely, pyarrow, and a bounded point-cloud reader. Pin versions only after an ARM64 installation smoke test. Move voxel generation to Rust only if profiling or integration with Arnis warrants it.

COLMAP is an optional camera-registration experiment, not a required Mac dense-reconstruction pipeline. Its official installation supports Mac, but hardware support of individual reconstruction stages must be tested. Do not assume NVIDIA/AMD acceleration applies to Apple Silicon. [COLMAP installation](https://colmap.github.io/install.html).

Reject for the first milestone: training a new model, building a globe, writing an entire game editor, inferring unseen interiors, and reconstructing a city from an unconstrained image-to-3D generator. These add large failure surfaces before the metric baseline exists.

## 9. Implementation milestones

| Stage | Concrete work | Deliverable and completion gate | Dependency |
|---|---|---|---|
| M0: environment and AOI | Resolve pilot, inspect coverage, install minimal dependencies, pin Arnis and game target | Coverage/rights manifest, measured disk forecast, native ARM64 smoke tests | Pilot location for live coverage |
| M1: no-AI playable baseline | Fetch bounded vectors and terrain, freeze assets, run Arnis | Fresh world loads in target Java; orientation and known distances checked | M0 |
| M2: evidence scene | Implement schemas, transforms, DEM/LiDAR and vector adapters, provenance, semantic geometry | Replayable scene; no datum/axis ambiguity; holdout geometry report | M1 and source-alone tests |
| M3: local façade inference | Benchmark 4B model on labelled crops; add structured outputs and cache | Local runtime measurements; calibrated acceptance/abstention; useful façade attributes | M2, permitted imagery |
| M4: optional depth | Smoke-test MPS; scale/occlusion controls; geometric registration | Keep only if it improves a named geometry metric within budget | M2; independent of M3's semantic utility |
| M5: fused world | Apply accepted observations; deterministic block export; attribution and evidence report | Improved holdout quality versus baseline without geometry regression | M3; M4 optional |
| M6: kilometre scale | Tiling, halo ownership, resumability, manual overlay persistence | 1,024 m world; zero tile seams; bounded resource use; interrupted run resumes | M5 |
| M7: repeatable product | CLI usability, source presets, local report, documented installation | Second different area succeeds without source-code changes | M6 |

Do not hold M3 hostage to depth: a depth model may fail while local semantic inference still improves the world. Do not hold M1 hostage to street imagery: a measured geographic baseline provides useful progress and a comparator.

## 10. Proposed CLI and file layout

These commands describe the intended interface; they are not runnable yet:

```text
earthcraft doctor
earthcraft survey configs/pilot.json
earthcraft fetch configs/pilot.json
earthcraft inspect-sources RUN
earthcraft build-scene RUN
earthcraft infer RUN --source street
earthcraft fuse RUN
earthcraft export RUN
earthcraft evaluate RUN
earthcraft resume RUN
```

`survey` estimates coverage, downloads, and license requirements; it does not download a country. Stage outputs are immutable and keyed by inputs/configuration/code/model revision. `resume` verifies complete artifacts and recomputes only affected descendants. The CLI validates budgets before expensive operations.

Future implementation layout:

```text
src/earthcraft/{cli,config,acquisition,geometry,scene,inference,fusion,export,evaluation}/
tests/{fixtures,geometry,source_adapters,inference_contracts,world_roundtrip}/
data/{raw,normalized}/
runs/<run_id>/{manifest.json,observations,scene,inference,evaluation}/
worlds/<run_id>/
```

## 11. Verification and promotion

Use the preregistered [experiment protocol](../experiments/PROTOCOL.md). Proposed numeric targets are design gates, not current achieved accuracy.

The required controls are a synthetic coordinate fixture, a no-AI baseline, each source alone, each added source versus the same baseline, and leave-one-source-out fusion. Hold out distinct images/capture positions and control measurements. Split by building to avoid tuning and evaluating on neighbouring crops of the same façade.

Measure horizontal position, roof/ground height, road width, window/material correctness on visible regions, evidence coverage, scale fidelity, and output integrity separately. Report unknown regions and rejected predictions. Never average a large ground area with small building errors to make the geometry score look better.

For replay, compare canonical scene and block-state hashes. Raw world bytes can differ due to metadata timestamps/compression. Model reruns can vary; freeze their outputs to make export reproducible.

## 12. Risks, limits, and response

| Risk | Response |
|---|---|
| Google scraping/derivative use unavailable under standard terms | Keep the adapter disabled; use separately permitted source data; revisit if appropriate rights are obtained |
| Pilot has sparse imagery or poor elevation | Surface coverage report; choose a simpler measured baseline, request user-owned photos, or report unmeasured attributes |
| Images and vectors describe different years | Preserve source dates and select a target epoch; flag conflicts instead of silently blending |
| Depth gives plausible wrong metric scale | Known-distance controls and holdout height checks; discard failed depth rather than bend reliable geometry |
| Roofprint differs from footprint | Store the distinction and use LiDAR/ground evidence for the wall line |
| Mac model unsupported or too slow | Smaller verified model, fewer crops, or geometry-only output with explicit loss of inferred detail |
| Storage exhausted | Bounded range reads and staged exports; stop at reserve; move project data only to a user-selected suitable volume |
| Arnis/writer cannot preserve chosen transforms | Use it as comparison only; adapt a writer that can consume the canonical scene |
| Vanilla height or collision limitations | Fail clearly; separate optional export profile; never silently distort scale |
| Attractive but invented façades | Evidence overlays, holdout comparison, unknown state, and deterministic conservative fallback |

## 13. First implementation session

1. Resolve the selected location and produce the source coverage report.
2. Install/pin only dependencies needed for a small Arnis baseline and geospatial validation.
3. Generate a fresh baseline world and inspect its coordinates in the installed game.
4. Save one source-only report for each available pilot source; mark absent sources absent.
5. Benchmark the smallest local vision candidate on at most 20 labelled, permitted crops before fetching a larger model.
6. Implement the scene and inference schema based on observed integration needs, then execute M2/M3.

The fastest useful proof is a playable, dimension-checked street block plus a small set of façade predictions with measured errors. It is not a city-sized download.
