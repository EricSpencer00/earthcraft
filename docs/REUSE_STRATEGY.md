# Earthcraft reuse strategy

**Review date:** 2026-09-11
**Question:** Which existing projects should Earthcraft reuse, and where is a distinct contribution justified?
**Decision:** Reuse mature geographic acquisition/export pieces; do not rebuild a Google-Earth-to-Minecraft voxel streamer. Put Earthcraft's effort into evidence-preserving, deterministic building updates and a Minecraft-native policy layer.

## Evidence and reuse candidates

| Candidate (primary source) | What it already solves | License, maintenance, and runtime fit | Recommendation |
|---|---|---|---|
| [Arnis](https://github.com/louis-e/arnis) | OSM buildings/roads/objects plus elevation; exports Java, Bedrock, and Luanti worlds; CLI and GUI. | Official README currently documents Apache-2.0 for the source, with a separately licensed Luanti mapping. The repository's Rust/Tauri executable is a practical baseline, but its OSM/DEM building model is not LiDAR roof reconstruction. | Keep the pinned vendor binary/source as the no-AI geographic baseline and comparison writer. Verify the exact vendored license/notices before redistribution. |
| [Voxel Earth](https://github.com/ryanhlewis/VoxelEarth) and its [CPU voxelizer](https://github.com/voxelearth/java-cpu-voxelizer) | Download/decode/voxelize 3D Tiles, including the Google Photorealistic 3D Tiles demo; streaming Paper/Bukkit plugin and web client. | The repositories expose explicit license files; inspect the relevant subproject and data-provider terms for each integration. Current docs cover Paper/FAWE and Java 21. Google tiles remain provider-terms constrained. | Reuse/adapt its separate CPU voxelizer and FAWE live-placement path where terms permit; benchmark it against Earthcraft's durable/exported path rather than assuming either is superior. |
| [Terra++](https://github.com/BuildTheEarth/terraplusplus) / [TerraPlusMinus](https://github.com/BTE-Germany/TerraPlusMinus) | Minecraft world generation at roughly 1:1 scale: terrain, OSM roads/water/building data, elevation, trees; TerraPlusMinus adds server bounds and LiDAR support. | Both repositories state MIT. Terra++ is a Forge/CubicChunks-era mod; TerraPlusMinus is a Bukkit plugin with version-specific releases/configuration. OSM is ODbL and requires attribution. | Reuse as an interoperability/reference target for projection, terrain and server-world constraints; do not couple Earthcraft's canonical scene to legacy mod internals. |
| [3DBAG / Roofer](https://github.com/3DBAG/roofer) | Deterministic large-scale LoD2.2 reconstruction from classified aerial LiDAR plus building **roofprints**; emits CityJSON/OBJ and has release binaries. | Roofer docs provide Linux/macOS install bundles, Docker, C++ API and Python API; releases include ARM64 assets. The repository license is GPL-3.0; record the exact release and dependency licenses before redistribution. | **Best candidate to benchmark for LiDAR → coherent shell/roof geometry**, not a proven winner. Run as a pinned external stage, retain CityJSON as evidence geometry, and convert downstream only after validation. |
| [Geoflow bundle](https://github.com/geoflow3d/geoflow-bundle) | Fully automated LoD1.2/1.3/2.2 reconstruction from footprint + LAS/LAZ; parameterized JSON flowcharts; CityJSON/OBJ/GeoPackage/PostGIS outputs. | Open-source, binary/Docker route available; macOS is explicitly untested in its README. Input expectations include classified aerial data and roughly 8–10 pts/m² for good 3DBAG-like results. | Use as the fallback/diagnostic reconstruction route when its flowchart and license inventory fit. Prefer Roofer's current release for the first experiment; preserve Geoflow as a reproducible alternative. |
| [3DBAG pipeline](https://innovation.3dbag.nl/3dbag-pipeline/) | Production workflow around reconstruction, floors, party walls, GDAL/PDAL and related geospatial processing; installable packages at pinned releases. | Pipeline packages are dual Apache-2.0/MIT. Deployment is explicitly complex and invokes tools (including Roofer) as subprocesses. | Reuse schemas/workflow ideas and isolated packages, not the whole production deployment. Treat subprocess licenses and generated data licenses separately. |
| [ObjToSchematic](https://github.com/LucasDower/ObjToSchematic) | Voxelizes OBJ and exports `.litematic`, `.schem`, `.schematic`, and `.nbt`; useful palette/voxel conversion reference. | BSD-3-Clause, but the repository says its desktop editor is legacy/unmaintained and current work is the website. Older `.schematic` is lossy for modern blocks. | Reuse conversion semantics or test fixtures, not the legacy editor as a runtime dependency. Prefer a canonical intermediate geometry plus Earthcraft's own deterministic exporter. |

The BuildTheEarth organization describes its mission as recreating Earth at 1:1 scale and reports a large builder community; that establishes the project's scope, not a measurable completion percentage. Terra++'s own README supports the narrower, verifiable claim that it generates public-dataset terrain, roads, water and structures and is used as a BTE-oriented mod. The linked Reddit post is a useful historical demonstration, but is not evidence of current coverage, accuracy, or rights to persist Google data.

## What Earthcraft should contribute

Existing tools cover map-to-world generation, provider-specific 3D-Tile voxelization, GIS building meshes, and Minecraft placement. Earthcraft should test whether the following contributions improve the combined benchmark; they are candidate differentiators, not claims that no upstream project has them:

1. **Lineage per feature and surface.** Preserve provider, immutable source ID, capture/retrieval date, CRS/datum, resolution, license, source hash, and the exact transform/parameters used. Keep footprint, ground, roof, imagery observation, and Minecraft approximation distinguishable.
2. **Deterministic appearance policy.** Given the same frozen inputs and configuration, produce the same shell, roof, palette, and block placement. Unknown/occluded surfaces may receive explicit stylized derived infill, but it must be labeled separately from observed geometry and must not be presented as measurement.
3. **User-edit-safe updates.** Store stable feature IDs and ownership masks so a new source revision can update only affected blocks, preserve user-owned edits, and emit a review diff rather than replacing a whole neighborhood.
4. **Minecraft-native quality gates.** Validate watertight/coherent shells, local-ground-relative heights, footprint alignment, block quantization, chunk/region boundaries, target-version compatibility, and a known-building holdout before admitting a generated world.
5. **Evidence-to-style separation.** Let LiDAR/footprints determine measured geometry; let a small explicit palette and style rules map that geometry to a readable Minecraft exterior. Derived infill and window patterns are allowed, but must be recorded separately from observations.

## Ordinary-building versus landmark routing

Apple publicly distinguishes a detailed city experience (elevation, neighborhoods, buildings, trees and road features) from “custom-designed landmarks,” while its user documentation describes ordinary buildings as something revealed by zooming. That supports a product routing hypothesis, not knowledge of Apple's private capture or rendering pipeline: use a scalable footprint/LiDAR route with deterministic stylized infill for ordinary buildings, and reserve a higher-evidence asset route for named landmarks such as the Water Tower.

For landmarks, first look for an obtainable authoritative mesh or survey: a city/agency CityGML or CityJSON model, an explicitly licensed photogrammetry survey, or user-owned multi-view imagery reconstructed with [COLMAP](https://colmap.github.io/license.html) (BSD) and, where its license fits, [OpenMVS](https://github.com/cdcseacave/openMVS) (AGPL). CityJSON is an open standard encoding for semantic 3D city objects, making it a useful interchange boundary. These routes require source rights, scale/control checks, and human acceptance of the named asset; they do not require an LLM. A public US fallback such as [Open City Model](https://github.com/opencitymodel/opencitymodel) is useful for coverage but currently advertises LoD1 and estimated heights, so it is an ordinary-building fallback rather than landmark evidence. The [NYC open-data metadata](https://github.com/CityOfNewYork/nyc-geo-metadata) documents a hybrid CityGML LOD1/LOD2 model with roughly 100 iconic buildings, a useful precedent to search for comparable municipal releases.

**Decision:** benchmark ordinary buildings through Roofer/Arnis plus Earthcraft style policy; benchmark a separately sourced landmark mesh through the same lineage, validation, voxelization, and edit-safe update gates. Do not infer Apple internals or treat a landmark model as representative city-wide coverage.

## Recommended first architecture

`roofprint + classified LAS/LAZ → Roofer (pinned release) → CityJSON → Earthcraft validator/lineage record → deterministic Minecraft shell/roof exporter → Arnis-compatible or direct world writer`

Use Geoflow as a cross-check on a small fixture. Keep Arnis for the existing terrain/OSM baseline. Use Roofer and Voxel Earth/FAWE as separate benchmark candidates. This keeps upstream capabilities reusable while testing whether Earthcraft's lineage, style labeling, and edit-safe output improve the combined result.

## Ticket-sized next moves

### 1. Roofer integration spike (one known building)

**Acceptance checks:** pinned release/container digest and license inventory recorded; classified LAS/LAZ + roofprint input and CityJSON output saved with hashes; output passes a geometry validator; local-ground and roof heights agree with control measurements within a stated tolerance; repeat run is byte-identical or differences are explained.

### 2. Canonical building evidence/lineage schema

**Acceptance checks:** one fixture records source IDs, CRS/datum, capture date, retrieval date, license, hashes, reconstruction parameters, uncertainty, and inferred-vs-observed fields; schema distinguishes ground footprint from roofprint and shell from styling; a replay command regenerates the same record.

### 3. Deterministic CityJSON-to-Minecraft shell exporter

**Acceptance checks:** exports a coherent shell and roof with no self-intersections/floating blocks on the fixture; quantization and orientation are documented; target Java world loads; a second run has identical block output; derived infill/window patterns are labeled separately from measured geometry.

### 4. User-edit-safe delta update

**Acceptance checks:** changing one source feature updates only its owned block set plus explicitly affected seam blocks; a user-owned edit survives regeneration; output includes added/changed/removed/uncertain feature IDs and a reviewable diff.

### 5. Baseline comparison report

**Acceptance checks:** same AOI and frozen inputs are run through Arnis, Roofer-derived Earthcraft output, and (where legally permitted) a Voxel Earth/3D-Tiles experiment; report footprint error, ground/roof height error, watertightness, unknown coverage, runtime, output size, and source lineage; no aggregate score hides per-building failures.

## Risks and gates

- **Data rights:** 3DBAG data is CC BY 4.0; OSM is ODbL; Google Photorealistic 3D Tiles have provider terms. Record each dependency's license and notices before redistribution; do not archive/re-distribute restricted tiles.
- **Input coverage:** Roofer/Geoflow require suitably classified aerial point clouds and aligned footprints; missing or sparse LiDAR must trigger a documented fallback, not a fabricated roof.
- **License boundary:** Roofer GPL-3.0, pipeline Apache/MIT, and vendored/optional tools have separate terms. Record the exact release, dependency licenses, and notices before redistribution.
- **Promotion gate:** promote only after the one-building fixture beats the Arnis baseline on geometry and provenance without breaking world-load or edit-preservation checks. Stop the integration if the provider lacks stable IDs/rights or the reconstruction cannot produce repeatable, valid geometry on the fixture.

## Sources checked (2026-09-11)

- [3DBAG copyright/license and citation](https://docs.3dbag.nl/en/copyright/) and [3DBAG pipeline docs](https://innovation.3dbag.nl/3dbag-pipeline/)
- [Roofer releases](https://github.com/3DBAG/roofer/releases) and [Roofer license](https://raw.githubusercontent.com/3DBAG/roofer/main/LICENSE)
- [Geoflow bundle README](https://github.com/geoflow3d/geoflow-bundle/blob/master/README.md)
- [Arnis README](https://github.com/louis-e/arnis)
- [Voxel Earth README](https://github.com/ryanhlewis/VoxelEarth)
- [BuildTheEarth Terra++ README](https://github.com/BuildTheEarth/terraplusplus) and [TerraPlusMinus README](https://github.com/BTE-Germany/TerraPlusMinus)
- [ObjToSchematic README](https://github.com/LucasDower/ObjToSchematic)
- [Voxel Earth release notes and component links](https://github.com/ryanhlewis/VoxelEarth/releases)
- [Roofer getting started (macOS/Linux bundles, C++/Python APIs)](https://innovation.3dbag.nl/roofer/getting_started.html)
- [Apple Maps detailed city experiences](https://www.apple.com/maps/) and [Apple's detailed-city announcement](https://www.apple.com/newsroom/2021/09/apple-maps-introduces-new-ways-to-explore-major-cities-in-3d/)
- [COLMAP license](https://colmap.github.io/license.html), [OpenMVS reconstruction scope/license](https://github.com/cdcseacave/openMVS), and [CityJSON overview](https://www.cityjson.org/about/)
- [Open City Model](https://github.com/opencitymodel/opencitymodel) and [NYC 3D model metadata](https://github.com/CityOfNewYork/nyc-geo-metadata)
- Peters et al., “Automated 3D reconstruction of LoD2 and LoD1 models for all 10 million buildings of the Netherlands,” [paper record](https://arxiv.org/abs/2201.01191)
