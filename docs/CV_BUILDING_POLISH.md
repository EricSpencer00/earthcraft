# Polishing buildings with deterministic computer vision

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
