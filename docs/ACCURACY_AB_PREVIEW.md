# Improving accuracy without changing the accepted world

Keep the current save, source receipts and block hashes as the baseline. A
cleaner-looking result is not automatically a more accurate reconstruction.
The offline A/B command below proposes roof materials in memory and renders
the same occupied blocks. It writes no world and changes no live worker.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts .venv/bin/python -B scripts/appearance_ab.py \
  --world "$FROZEN_TILE_WORLD" --imagery "$FROZEN_RGBN_IMAGERY" \
  --points "$FROZEN_CLASSIFIED_POINTS_NPZ" --point-manifest "$POINT_MANIFEST_JSON" \
  --output runs/appearance-ab
```

Point inputs are optional and must be supplied together. Inputs remain local;
the command makes no provider, model or game-client requests. Use a complete
16–128 m fixed generated tile, its roof ownership/top-height evidence, and
fully covering georeferenced imagery. Output includes matched baseline and
candidate cube renders, `report.json`, `proposal.npz`, and a canopy overlay.
These are headless data renders, not Minecraft screenshots or a playable
candidate world. No output-space or worker-memory gate is lowered.

With verified project-specific point labels, `--canopy-preview` adds a third
in-memory candidate from observed class-5 returns 2–60 m above checked ground.
Only air is eligible, outside building footprints plus 2 m, classified roads
plus 1 m, and accepted photo columns. Original blocks are unchanged; gaps,
trunks and species are not inferred. Green `oak_leaves` cubes are explicitly a
display proxy. This mode writes only additional data renders and receipts,
not a save or live vegetation layer. It requires local frozen footprint and
classified-road evidence, and still needs held-out placement validation.

The command checks frozen image assets, explicit RGB/NIR order, integral grid
alignment and stored normalization. Older NAIP records without a normalized
hash are reproduced from their hash-checked served raster. NIR is used for
vegetation diagnostics and never mapped to visible RGB. Actual point-class
histograms and withheld flags are checked against the project manifest;
building and vegetation labels are not assumed to exist in arbitrary 3DEP data.

Roof proposals use a same-owner, same-level 3×3 RGB median. Sparse, mixed,
high-chroma, vegetated, shadowed and protected-photo samples abstain. Existing
non-neutral materials stay intact. Every proposed replacement must lower its
pixel/installed-palette error. Occupancy, native heightmaps, source hashes,
ground/roads and all blocks below the selected roofs must remain unchanged.
This tests presentation consistency, not physical colour accuracy. Registration,
capture-date agreement and held-out accuracy remain separate promotion gates.
The preview cannot promote itself to the live pipeline.

Prioritize these small experiments, one factor at a time:

1. **Trace the shown region to its source receipts.** Record exact coordinates,
   source dates, class histograms, footprint ownership, roof supports, imagery
   raster IDs and block palettes. A screenshot alone does not identify which
   tile/profile produced an artifact.
2. **Roofs and buildings:** retain every admitted return and known peak; inspect
   class-1 association and DSM/footprint overlap before changing reconstruction.
   Treat unsupported holes as unknown. Test bounded roof-plane fitting only on
   missing cells within one owned footprint; preserve courtyards and all sampled
   heights. Verify serialized upper sections as well as the in-memory shell.
3. **Colours:** replay RGBN normalization and check roof-image registration and
   parallax before changing the palette. Compare per-pixel and coherent sampling
   on one fixed block. Measure held-out colour error and abstention coverage;
   reducing bright speckles alone is not a correctness gate. Aerial images
   cannot supply unseen facade colour. Keep registered facade observations.
4. **Streets:** distinguish explicit OSM widths, provider-specific classified
   pavement, and recorded road-class presentation defaults. Validate curb/width
   boundaries against aligned imagery; preserve grade and intersections. Report
   width error on fixed cross-sections rather than widening all roads globally.
5. **Trees:** begin with the actual project vegetation classes, non-withheld
   returns and height above the checked terrain. Examine canopy/roof overlap.
   An observed canopy is not an observed stem or species. Validate canopy extent
   and height before an opt-in leaves/wood candidate; do not add decorative trees
   as an accuracy improvement.

Use cheap CPU operations first: one-metre masks, small histograms, robust local
statistics, connected components and bounded plane fits. Cache decoded points
by source hash plus class/CRS/filter recipe. Cache imagery normalization by
asset/grid/band-order/resampler identity. Rebuild only changed tile/layer IDs;
keep source/geometry/appearance/vegetation receipts separate. Benchmark actual
RSS and I/O before scaling. GPU segmentation, dense photogrammetry, new imagery
downloads and paid services are separate experiments, not dependencies here.

USGS describes a minimum classification scheme; additional class accuracy is
the requesting partner's responsibility, so generic building/vegetation label
assumptions are insufficient. See the [USGS classification requirements](https://www.usgs.gov/ngp-standards-and-specifications/lidar-base-specification-data-processing-and-handling-requirements).
Source rights remain dataset-specific: retain acquisition dates, attribution,
terms and distribution decisions. Do not publish raw imagery or point crops
merely because a local adapter can read them.
