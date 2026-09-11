# Pilot experiment protocol

This is a preregistered design for optional research. The no-AI MVP remains the
public release path; the source-quality and game-loading gates below have not
passed. Numeric gates are proposed engineering targets, to be frozen for a
selected dataset before running it. If a target is impossible given source
accuracy, record that limitation; do not silently loosen the target after
seeing a result.

## E0. Environment and export fixture

First execute the preflight in [RESOURCE_LIMITS.md](../docs/RESOURCE_LIMITS.md): verify both volumes, budget caches and overlapping copies, benchmark bounded external I/O, and record native architecture. Test missing-volume, low-space, partial-copy and simulated I/O failure behavior. Keep active state internal. These guards are proposed, not implemented. This stage does not require a selected real AOI.

Build a synthetic 64 × 64 metre scene containing known distances, a sloped ground surface, diagonal wall, pitched roof, bridge, water edge, and hollow building. Cross a chunk boundary and include negative coordinates in at least one fixture.

Predict: axis mapping and continuous-to-block transforms are exact by construction; representation loss occurs only at documented cell coverage/shape choices. A 20 metre axis-aligned wall occupies its specified grid interval, not 19 or 21 blocks from an off-by-one error.

Verify coordinate round trips within 1 mm on synthetic numeric inputs; verify intended occupied cells analytically for simple shapes. The 1 mm check tests transforms, not real-world data accuracy. Load the generated world in the exact target game version, inspect lighting/spawn, save/reload, and compare block states.

Gate: no wrong-axis, scale, range, corrupt-world, or boundary failures. Arnis can be a baseline only after its coordinate behaviour is measured; do not assume its documented scale setting passes this fixture.

## E1. Coverage and source-alone evaluation

Choose a 256 × 256 metre AOI with at least 10 buildings if the user's location permits. Add context halo. Inventory data before downloading: capture dates, area coverage, image positions, missing heights, elevation resolution, projected download sizes, and licenses.

Pick at least 20 distributed ground/footprint controls and at least 10 roof/height controls where independently available. Independence is per measurement: a LiDAR-derived roof cannot be evaluated against the same LiDAR roof as if it were ground truth. If adequate independent controls are unavailable, label the result a consistency test and leave accuracy unverified.

Create one report per available source. Missing sources remain listed as absent. Do not substitute fused quality metrics for a failed individual source. Record how controls were selected before viewing generated output.

Proposed acceptance for a high-quality pilot: median horizontal boundary/control error ≤1 metre and p95 ≤2 metres; terrain vertical median absolute error ≤1 metre; building height median absolute error ≤2 metres. Report source metadata accuracy beside these targets. Give per-building values, worst cases, and counts; small samples do not support broad accuracy claims.

## E2. No-AI baseline

Run pinned Arnis with frozen vectors/elevation and settings. Save the command, commit/binary hash, raw source hashes, target version, generated extent, and elapsed time. If it cannot consume frozen inputs directly, capture its exact fetched inputs or add a narrow fixture adapter before claiming replayability.

Produce an overhead view and a fixed ground route with 5–10 camera positions. Check streets, building positions, elevation, cardinal direction, and scale. Label any generated defaults as inferred/procedural. Measure disk space and memory use.

Gate: a playable world and honest error report. AI is not required to pass this stage. Retain this world as the fixed comparator.

## E3. Local vision inference

Start with 20 permitted, hand-labelled crops to test runtime and schema. Expand to 100 only after this succeeds. Limit duplicate views. Split by building into development and holdout sets; do not tune prompts on holdout output. Record material classes, visible roof type, window bands, occlusion, and labels that are genuinely unknowable from each image.

Candidate A: verified 4-bit Qwen3-VL-4B-Instruct with MLX-VLM. Candidate B: an 8B model only if A fails a useful quality gate and the current budget allows it. Record exact repo/revision, weight hash, license, library versions, image preprocessing, prompt, seed/temperature, and token limits.

Metrics: schema validity, precision among accepted material/roof predictions, coverage after abstention, per-class confusion, incorrect accepted details, seconds per image, cold model-load time, peak memory pressure, swap delta, and disk usage. Do not trust the model's own confidence without calibration against held-out labels.

Proposed gate: ≥90% precision among accepted material/roof predictions at ≥50% coverage of labelled visible instances, with no silent invalid-schema acceptance. Report counts and intervals; 20 crops establish feasibility only, not reliable population precision. Window counts are a separate metric and are not required to enable material inference.

Run with network disabled after model acquisition. Gate: no network dependency or cloud fallback. If it fails, preserve the no-AI pipeline and report why.

## E4. Depth independently

Start with one image and confirm backend correctness before scaling to 20 images. Test Depth Pro on MPS if supported by the installed runtime. Record unsupported operators, explicit CPU fallback, invalid depths, and runtime; do not hide slow fallback as GPU execution.

Use known dimensions and separate validation controls. Record median and p95 depth/height errors on observable surfaces and residuals after metric registration. If fitting a scale/bias using controls, reserve disjoint controls for scoring and disclose the fit.

Include reflective glass, trees, oblique views, and occlusions. Crops from one panorama are not independent stereo views. Depth estimates behind an occluder are not measured surfaces.

Gate: the added depth improves a preregistered geometry metric over the no-depth scene and does not worsen p95 geometry beyond the pilot tolerance. Otherwise leave it off. Do not force a neural-depth stage into the final system simply because it is AI.

## E5. Fusion and ablations

Freeze base scene B0. Evaluate B0 plus each additional available source individually. Then evaluate all admitted sources and remove one at a time. Keep source versions and the scoring set identical.

Example arms: B0; B0+LiDAR roof evidence; B0+aerial semantics; B0+street semantics; B0+depth; full accepted fusion; full minus each source. Skip absent sources explicitly. Distinguish no-data failures from bad inference.

Score geometry, visible semantic correctness, evidence coverage, resource cost, and unsupported accepted details separately. Show fixed-view image comparisons. A higher photorealistic similarity score cannot compensate for a misplaced road or invented roof.

Proposed gate: at least a 10 percentage-point improvement in held-out visible material/roof correctness versus baseline defaults, with no more than 0.25 metre increase in continuous-scene median geometry error and no scale/orientation regressions. If baseline is already too accurate for that improvement, compare reduction in remaining errors and explicitly preregister the revised criterion before rerunning. Do not change the gate on the same scored holdout.

## E6. Tiling and reproducibility

Generate a 1,024 × 1,024 metre scene both monolithically where feasible and tiled from identical frozen observations. Compare canonical block states within the selected area. Test four-tile corners, buildings crossing boundaries, bridges, and water levels.

Gate: zero unexplained occupancy differences at seams; complete world loads; interrupted run resumes without reacquiring unchanged assets; manual overlay survives regeneration; out-of-date inputs invalidate affected outputs. A feature crossing tiles is owned by one stable object record.

Test replay from saved inference output rather than requiring identical neural inference runs. Canonical scene/block hashes must agree. Exclude world timestamps and serialization noise from this equality definition.

## E7. Resource and second-location gate

Measure the pilot before extrapolating. For a grid-aligned square, 256 metres is 16 × 16 chunks and 1,024 metres is 64 × 64 chunks: 16 times the area. These counts exclude halos and extra boundary chunks from arbitrary alignment. Imagery density and scene complexity can make runtime scale differently.

At a 384-block vertical envelope, 1,024² × 384 is 402,653,184 possible cells, about 1.5 GiB at four bytes per dense cell before overhead. This is capacity arithmetic, not predicted compressed world size. Stream sparse/chunk-local output instead of materializing several dense copies.

Proposed pilot limits are in `configs/pilot.json`: 12 GiB internal project cap with 20 GiB free reserve; 100 GiB external cap with 100 GiB reserve; 24 GiB process-tree memory target, 32 GiB stop threshold, 2 GiB swap-growth pause threshold; 60 minute warm-generation target. Each volume is checked independently. See [RESOURCE_LIMITS.md](../docs/RESOURCE_LIMITS.md) for allocation and disconnect handling. Download and model-load times are reported separately from generation, and total end-to-end time is also reported. Dependency caches count toward project storage consumption even when located outside the repository.

Gate: a second location runs from configuration alone with a truthful quality report and bounded resources. Do not claim city-scale throughput or general geographic accuracy from one successful street.

## Result record

Every experiment saves: date, hypothesis, input hashes, source permissions/lineage, code/model versions, command/configuration, control split, predicted clean result, measured metrics, failures, resource costs, example outputs, and admit/reject decision. Failed experiments remain useful records. No values in this protocol are benchmark results.
