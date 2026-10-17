# Dataset

Everything Earthcraft generates lives in one public Hugging Face dataset:
[EricSpencer00/earthcraft](https://huggingface.co/datasets/EricSpencer00/earthcraft).
Nothing bulky is kept anywhere else.

Start at `catalog.json`. It lists the main files with their size and SHA-256.
Pin a commit when you read it, and read every other file at that same commit.

| Folder | What is in it |
| --- | --- |
| `catalog.json` | Entry point: what exists and where. |
| `world/` | The playable save. |
| `tiles/` | Every generated 512 m tile, one folder per tile. |
| `detail/` | Detail layers (trees, streets, buildings, water) as block edits. |
| `qa/` | Visual QA runs: rendered samples and scores. |
| `sources/` | Indexes built from source data (OSM roads, building footprints) and the player-free world templates (`sources/templates/v1/`). |
| `contributions/` | One manifest per merged compute contribution (who, git SHA, tiles, SHA-256s). |
| `archive/` | Install backups and old worker state. |
| `snapshots/`, `releases/` | Older full-save snapshot and earlier releases. |

## world/

- `world/region/<bx>/r.X.Z.mca` — raw Minecraft region files. `bx` is
  `floor(X / 16)`, which keeps each folder under 10,000 files.
- `world/index/regions.json` — every region with its size, SHA-256, and a map
  of which chunks it holds.
- `world/manifest.json` — every file in the save, with hashes and the
  coordinate frame.
- `world/meta/` — `level.dat` and other save files, with all player data removed.

Region files can be read one chunk at a time with HTTP Range requests. The 3D
viewer does this.

## tiles/

`tiles/v2/<rx>_<rz>/` holds one tile: `tile.json`, the tile's region data, and
how it was made. Scan upgrades also keep the lower-quality version under `lq/`.
`tiles/v1/` holds older tiles as tar files.

## detail/

`detail/<layer>/v1/` holds one layer: a `manifest.json` and one gzip JSON-lines
file per region. Each line lists block edits for one chunk. See
[STREET_DETAIL.md](STREET_DETAIL.md).

## Coverage

Coverage is partial Chicagoland. A region or tile that is not listed has not
been generated yet; it is not empty land.

## Rules

- No player data: no `playerdata/`, `stats/`, `advancements/`, or player in `level.dat`.
- Every derived layer keeps its source and licence.
- The world includes OpenStreetMap data, so it is under the ODbL 1.0.
