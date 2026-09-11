# Smallest building-first MVP

Implemented 2026-09-09: a fixed **64 × 64 block** square around Chicago Water Tower, at Arnis's 1 block/metre setting. This is a local experimental baseline and a building-specific refinement. **Minecraft game loading and geographic accuracy remain unverified.**

## What exists

- `scripts/mvp.py`: runs pinned Arnis 3.1.0 on the fixed square, preserves OSM responses, writes a new world/log/manifest and checks initial storage headroom.
- `scripts/tower_overlay.py`: creates a separate derived input, normalizes the landmark's outline/part roles, uses warm sandstone as a visual approximation of documented buff limestone, and proposes assigning the parent's height to the highest part. Original observations remain untouched. This is a building-specific experiment, not a generalized adapter.
- `scripts/inspect_world.py`: reads generated NBT and creates a compact playtest copy containing only the selected chunks. It never overwrites an existing destination. Retained chunk data is verified unchanged after compaction.
- Three focused tests cover source preservation, unrelated-feature preservation, relation roles, idempotence, chunk boundaries and retained compressed payloads.

No local model, neural depth, GUI, fusion database or external-drive dependency is needed for this tiny proof. It uses a bounded internal-storage exception to the larger pilot profile: less than 1 GiB project data before generation, 1 GiB working allowance and 20 GiB free reserve. These are preflight checks, not enforced per-download limits. LaCie remains the bulk-storage plan for later work.

## Run the fixed baseline

Requires macOS Apple Silicon, Python 3.11, `uv`, and the official Arnis Mac binary. From the repository root:

```sh
mkdir -p vendor
curl -fL https://github.com/louis-e/arnis/releases/download/v3.1.0/arnis-mac-universal.tar.gz -o vendor/arnis-mac-universal.tar.gz
tar -xzf vendor/arnis-mac-universal.tar.gz -C vendor
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-mvp.txt
.venv/bin/python scripts/mvp.py
.venv/bin/python -m unittest discover -s tests -v
```

The runner prints the run manifest path. Its `argv` records the world output directory. Arnis creates an `Arnis World 1` subdirectory there. Elevation and land-cover data may still require network access; full offline replay is not implemented. Review the log, since defaults and terrain repairs can introduce errors.

To make a compact copy, choose a **new** destination whose parent exists:

```sh
.venv/bin/python scripts/inspect_world.py "worlds/RUN/Arnis World 1" --compact-copy "worlds/NEW-PLAYTEST"
```

`RUN` and `NEW-PLAYTEST` are placeholders. The inspector requires the generated square to start at zero and align to 16-block chunks. It is not a general-world converter. Copy the completed world into the target game's saves directory while that world is closed. Keep the original generated artifacts intact.

## Local result and critique

| Check | Result |
|---|---|
| First area | 128 × 128 blocks generated |
| Revised area | 64 × 64 blocks; 16 retained chunks |
| Playtest bytes | Baseline 99,030; refinement 99,187 before inspection sidecar/game saves |
| Initial export layout | Arnis wrote all 1,024 chunks of a region; compact copy keeps 16 |
| Format | DataVersion 4189 / Java 1.21.4; later-game conversion is not yet tested |
| Reproduction command | Ran successfully using saved OSM and cached supporting data |
| Unit checks | 3 tests passed |
| Source fidelity | Failed visual baseline gate: parent outline becomes broad upper extrusion |
| Refinement | Narrower mapped shaft and warm material approximation; improvement only, not validated reconstruction |
| Remaining defects | Procedural façade bands; roof/total-height handling; unwanted water classification; dense procedural planting |
| AI | None |
| Minecraft | Signed in; official Java 1.21.10 installed and client rendering initialized. CUA could not attach to Java; world load/save/reload remain unverified |

The standalone block diagnostic is a simplified render of exported blocks, not a game screenshot. It exposed the silhouette defect before game loading. No terrain accuracy, façade accuracy, exact global scale or playability claim is justified yet. The source total height can interact with inferred roof height; test the final highest occupied block before approving the refined building.

## Next gate, in the smallest possible scope

1. Load the existing refined playtest world using the prepared Earthcraft Playtest profile (Java 1.21.10). Inspect, save and reload. Sign-in is complete; automated control of the Java client is currently blocked.
2. Fix this tower's total height, silhouette and roof using independent public documentation; verify one known dimension.
3. Check one façade against a permitted reference and remove unsupported repetitive detail. Review entrance and pavement at walking height.
4. Resolve the false water/terrain behavior in this square before calling it geographically faithful.
5. Only then try two to four façade images with a small local model and compare against the hand-checked observation record.

The user subsequently requested a city-wide draft; that explicitly expands scope before this fidelity gate passes. The Chicago draft remains unverified and is tracked separately in [CHICAGO.md](CHICAGO.md). [Building fidelity](BUILDING_FIDELITY.md) explains the evidence and appearance choices.

## Signed-in launch attempt (2026-09-09)

The official launcher installed Java Edition 1.21.10 (about 524 MB download) into a new **Earthcraft Playtest** profile. The new profile inherited `-XX:+UseCompactObjectHeaders`, which its bundled Microsoft OpenJDK 21.0.7 rejects. Removing that flag resolved the pre-client crash; the corrected profile keeps a 4 GiB maximum heap. `logs/latest.log` records completed texture-atlas initialization at 13:43:47. This establishes client startup, not successful world loading.

Computer control could operate the launcher but did not list the Java client. Selecting the runtime bundle then stalled despite an explicit 20-second tool timeout and was interrupted. The refined world was not opened, and no in-game screenshot, building inspection, save or reload was completed. All previous geometry and appearance findings remain diagnostic-render findings only. No source data or generated world was changed.
