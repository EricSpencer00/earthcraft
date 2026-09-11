# Sparse Earth architecture

## Decision

Earthcraft should not eagerly generate every square metre of Earth or produce
one monolithic Minecraft save. It should build a globally addressable,
deterministic world whose fidelity follows the information content of each
place.

Arnis is the default generator for ordinary inhabited areas. Earthcraft adds
the global tiling, source indexing, provenance, resumability, streaming, and
landmark-override layers needed to operate Arnis at regional and planetary
scale.

The intended result is still a continuous 1:1 coordinate space. Sparse storage
and variable generation effort must not change the location or scale of a
feature.

## Fidelity classes

Every tile receives an explicit fidelity class from source data and stable
rules. The class is recorded in its manifest; it is not guessed by an LLM.

| Class | Typical areas | Representation |
|---|---|---|
| Landmark | Named monuments and unusually complex structures | Validated municipal mesh, LiDAR reconstruction, or licensed photogrammetry override; voxelized at 1:1 |
| Urban | Buildings, streets, parks, and infrastructure | Arnis from local OSM/Overture extracts plus elevation, deterministic materials and source-backed refinements |
| Rural | Farms, small settlements, roads, and managed land | Elevation, waterways, roads, land-cover classes, and simplified structures; omit invisible subsurface volume |
| Natural | Mountains, forests, deserts, wetlands | Elevation and land cover at source-supported resolution; deterministic vegetation and surface rules |
| Low-information | Open fields, tundra, or source-sparse land | Coarser source sampling with deterministic interpolation and biome-constrained detail |
| Ocean | Open water away from coasts and islands | Analytic sea surface and compact bathymetry/noise seed; no stored per-block ocean volume |

Coasts, rivers, islands, bridges, ports, and lakefronts are never treated as
generic ocean because small positional errors there are conspicuous and affect
navigation.

## What “noise” means

Noise is a compact deterministic representation, not random fabrication. Its
inputs are a fixed algorithm version, global coordinates, a published seed,
and observed constraints such as elevation, biome, coastline, hydrography, and
land cover. Regenerating a tile from the same manifest must produce the same
blocks byte for byte.

Procedural detail may add texture below the source resolution, but it must not:

- move mapped coastlines, waterways, roads, or structures;
- alter measured control-point elevations;
- be labeled as observed geography;
- depend on generation order or neighboring job timing.

## Global coordinate and tile model

Use one fixed Earth-to-Minecraft projection and metre grid. Divide it into
globally named tiles aligned to Minecraft regions. Each tile is an immutable
build artifact with:

- geographic bounds and Minecraft bounds;
- source identifiers, dates, licenses, and content hashes;
- fidelity class and reconstruction recipe version;
- hashes for generated regions and optional landmark overlays;
- edge/halo hashes for seam validation;
- completion and verification state.

Tiles are generated independently from local source extracts. Shared halo data
ensures that terrain, roads, water, and buildings crossing tile edges agree.
The assembled world is a manifest plus content-addressed region objects, not a
directory that every worker edits concurrently.

## Generation pipeline

```text
planet-scale source archives
  -> spatial indexes and immutable local extracts
  -> fidelity classifier
  -> distributed tile queue
       -> Arnis urban/rural generation
       -> terrain/land-cover natural generation
       -> analytic ocean generation
       -> validated landmark overrides
  -> seam, provenance, and deterministic-replay checks
  -> content-addressed region store
  -> on-demand world delivery and local cache
```

Public Overpass instances must not be used as a planetary batch backend. Import
the OSM planet file once, build a spatial index, and hand each job a bounded
local extract. Elevation and land-cover inputs receive the same immutable-cache
treatment.

## ANL execution strategy

### Polaris: first production target

Start here. Unmodified Arnis is primarily CPU-, memory-, and storage-oriented.
Polaris nodes provide 32 CPU cores, 512 GiB RAM, and fast node-local NVMe, making
them suitable for independent tile-array jobs. Stage source extracts onto local
NVMe, generate a bounded batch, hash results, and copy only completed artifacts
to shared storage. The A100 GPUs are not required for ordinary Arnis tiles.

### Aurora: scale after profiling

Aurora can run much larger arrays and has substantially more CPU cores and
memory per node, but its main value is its GPU capacity. Do not claim exascale
performance for unchanged Arnis. First measure CPU utilization, memory,
serialization, source reads, and output writes on one node. Use Aurora broadly
only after the workflow is demonstrably I/O-safe, or after raster/voxel kernels
are ported to its GPUs.

### Sophia: landmark and CV work

Use Sophia for bounded GPU-heavy photogrammetry, image matching, façade
registration, or other non-LLM computer-vision stages. It is not the preferred
machine for mass-running the current CPU-focused Arnis generator.

## Storage strategy

The chief saving comes from not materializing low-value blocks.

- Store populated and structurally complex regions normally.
- Generate open ocean from a sea-level rule plus compact bathymetry parameters.
- Store terrain surfaces and required geology, not enormous uniform solid
  volumes where the game cannot observe them.
- Deduplicate identical region templates and resources by content hash.
- Keep a bounded player-local cache and fetch or generate distant regions on
  demand.
- Preserve user-modified regions separately from reproducible base geography.
- Regenerate disposable derived tiles instead of treating all output as archival
  source data.

This converts the problem from “store every block on Earth” to “store source
indexes, populated detail, exceptional geography, player edits, and recipes for
everything reproducible.” A real storage forecast still requires measured
bytes per tile for each fidelity class.

## Ordinary buildings and landmarks

Ordinary buildings use Arnis plus explicit Earthcraft styling. This path should
be fast enough to cover cities and settlements in bulk. It must retain stable
building IDs so a later source update changes only the affected structure.

Landmarks use a separate admission route because unusual silhouettes, spires,
ornament, stadium roofs, and bridges are poorly represented by generic filling.
A landmark override is accepted only when it has documented rights, scale and
coordinate controls, source hashes, deterministic voxel output, and a visual
and geometric comparison. The Chicago Water Tower belongs in this class; it is
not a representative benchmark for ordinary buildings.

## Initial benchmark

Before estimating a continental allocation, run the same frozen tile set on a
workstation and one Polaris node:

1. dense downtown tile;
2. residential tile;
3. rural/field tile;
4. mountain/forest tile;
5. coast tile;
6. open-ocean tile;
7. one admitted landmark overlay.

For each class record source bytes, peak memory, CPU and GPU utilization,
elapsed time, output bytes, shared-filesystem traffic, region count, and output
hash. Replay each case twice. These measurements determine tile size, node
packing, allocation estimates, and whether a GPU port is justified.

## Promotion gates

The distributed pipeline is ready to broaden beyond Chicago only when:

- identical frozen inputs produce identical region hashes;
- neighboring jobs pass seam checks;
- failed or preempted jobs resume without corrupting completed output;
- no job depends on public interactive map services at batch scale;
- source-backed and procedural content remain distinguishable in manifests;
- player edits survive base-tile upgrades;
- a Minecraft client can traverse generated and procedural regions without a
  visible coordinate discontinuity;
- measured compute and storage forecasts fit the granted allocation and project
  storage policy.

## Upstream references

- [Arnis](https://github.com/louis-e/arnis)
- [Arnis ground-generation design](https://github.com/louis-e/arnis/wiki/Ground-Generation)
- [Polaris system overview](https://docs.alcf.anl.gov/polaris/)
- [Aurora system overview](https://docs.alcf.anl.gov/aurora/)
- [Sophia system overview](https://docs.alcf.anl.gov/sophia/)

