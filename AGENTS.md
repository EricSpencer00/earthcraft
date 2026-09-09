# Project instructions

Read `docs/PLAN.md`, the relevant section of `docs/SOURCE_LOSSINESS.md`, and `experiments/PROTOCOL.md` before substantial implementation.

- Keep inference local on the Mac by default. Codex can develop the software; its hosted model is not the local runtime.
- Preserve one block per metre in all axes. Never silently stretch a tile, flatten terrain, compress altitude, or enlarge a road for appearance.
- Retain source observations and their timestamps. Distinguish observed, inferred, procedural, and unknown attributes.
- Validate each source alone before fusion. Do not score against the same observations used to generate a feature.
- Treat model self-reported confidence as an uncalibrated score. Preserve abstention.
- Every source adapter records access terms, license, source identifiers, checksums, capture dates, coordinate systems, and transformations.
- Implement geometry deterministically from frozen scene artifacts. Do not promise bitwise reproducible model inference.
- Bound downloads, image counts, model caches, memory, and output storage. Read current available space before work. Never remove unrelated user data to make space.
- Write new worlds to the project output directory. Never overwrite an existing Minecraft save. Preserve manual edits through a separate overlay.
- Keep source data, model weights, caches, generated worlds, and credentials out of Git.
- Use native Apple Silicon processes for inference. Do not assume CUDA libraries or a Linux container can access Metal.
- Prefer extending an existing tested world writer over inventing a file format implementation. Verify export by loading the exact target game version.
- No public benchmark claims until measured here. No public release is implied by creation of this local repository.
- Do not create board tickets or spawn agents unless the user requests that workflow.

The current turn established planning artifacts only. Proposed interfaces in these documents do not exist yet.
