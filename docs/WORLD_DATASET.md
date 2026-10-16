# Earthcraft world dataset

The one durable store is the public dataset
[EricSpencer00/earthcraft](https://huggingface.co/datasets/EricSpencer00/earthcraft).
Generated tiles, the canonical save and their receipts are written there
directly, in bounded and verified commits; nothing is staged on an external
drive. `catalog.json` is the entry point: fetch it at a pinned commit and
resolve every other path at that same commit.

| Path | Contents |
| --- | --- |
| `world/` | The canonical save: raw `world/region/<bx>/r.X.Z.mca` (`bx = floor(X / 16)`), `world/index/regions.json` (bytes, SHA-256 and a chunk-presence bitmap per region), `world/manifest.json`, and `world/meta/` without player data. |
| `tiles/v2/<rx>_<rz>/` | One generated 512 m tile: `tile.json`, the delivered region as raw Anvil, its `earthcraft.json`, receipts per stage, and for scan upgrades the immutable LQ baseline under `lq/`. |
| `tiles/v1/` | The earlier tar shards (`index.json` still names their old `generated-tiles/v1/` prefix). |
| `sources/indexes/` | Derived source indexes: Illinois, Indiana and Wisconsin OSM way indexes and the Overture footprint index. |
| `archive/install-backups/<attempt>/` | Pre-install copies of every save file an installation changed (gzip), plus its report. |
| `archive/mini-worker-20260929/` | Control state of the retired worker (plan, journal, catalogs, index receipts). |

Coverage is partial Chicagoland at one block per metre. A region or tile that
is not listed was not generated; it is not empty terrain.

## Generated tiles (tiles/v2)

`scripts/regional_generate.py` runs on the MacBook. One tile is one unit of
work: sources, LQ geometry and the measured appearance run in one pass in
`runs/scratch/gen/<tile>/`, the result is packaged as `tiles/v2/<tile>/`,
several tiles share one commit through `scripts/hf_store.py push-batch`, the
remote SHA-256 is verified, the journal (`runs/regional/control/jobs.sqlite`)
records the dataset receipt path, hash and commit, and the scratch tile is
deleted. Raw `.mca` files can be read one chunk at a time with HTTP Range, as
`world/` regions can. Receipts carry dataset paths (`tiles/v2/...`), never
local paths; `gen:` marks a per-tile intermediate that was not published,
`cache:` a source-cache entry whose URL and SHA-256 sit beside it. See
[the regional scan pipeline](CHICAGOLAND_SCANS.md) for running it.

## Installing into the save

Save and Quit to Title first. `scripts/hf_install.py` refuses while Minecraft
holds `session.lock` (the same POSIX record lock the game takes), before it
downloads or writes anything:

```sh
EARTHCRAFT_HF_PYTHON=/path/to/python-with-huggingface_hub \
  .venv/bin/python scripts/hf_install.py --save runtime/traversal/saves/Earthcraft --dry-run
EARTHCRAFT_HF_PYTHON=... .venv/bin/python scripts/hf_install.py --save runtime/traversal/saves/Earthcraft
```

It selects tiles whose journal evidence is in `tiles/v2/` and whose delivered
receipt is not yet in the save's `regional-quality.json` (or `--tiles`),
downloads each package and the LQ baselines a scan upgrade needs (`lq/` and
the tile's `tiles/v1` shard member) into capped scratch, and verifies them.
Under one session-lock fence it adds absent chunks and upgrades a chunk only
while it still equals an LQ baseline or has the same block volume; player
edits, entities and changed blocks keep the existing chunk. The original bytes
of every touched file are gzip-backed-up first and uploaded to
`archive/install-backups/<attempt>/`, then deleted locally. `level.dat` is
not uploaded because it holds the player; its border fields are recorded in
the attempt's `installation.json`. Finally `hf_store.py push-world
--only-changed` syncs the changed regions to `world/`. It takes the lock again
and refuses if the game reopened the world; rerun it later. With no
`world/manifest.json` on the Hub yet, the sync is skipped instead of becoming a
full upload. Scratch (`runs/scratch/install/`, 4 GiB cap, 40 GiB free floor)
is empty afterwards.

## Syncing the whole save

`hf_store.py push-world` uploads a closed save to `world/`: region files as
they are, metadata after a privacy transform (no `playerdata/`, `stats/`,
`advancements/`, `level.dat` `Data.Player` or scoreboard player scores), and
`world/index/regions.json`, `world/manifest.json` and `catalog.json` last.
While the game is open, stage an APFS clone of the save under the lock first
(`world_snapshot.py stage`), push the clone, and delete it.

## The 2026-10-01 snapshot

`snapshots/chicagoland-20261001-closed-save/` holds the closed-save snapshot
made before the direct store: 114 verified files (111 shards of up to 8 x 8
regions and the manifest), 4,126,030 serialized chunks and 1,755 installed
512 m tiles. The public copy has no player data; its geographic shards are
byte-identical to the private original. Restore it into a new save with the
bundled standard-library script:

```sh
python restore_world_snapshot.py --package /path/to/downloaded/snapshot \
  --destination /path/to/minecraft/saves/Earthcraft
```

Repeat `--shard` with manifest keys to restore selected areas; metadata is
always included. Use Minecraft Java 1.21.10. Read the retained source and photo
attribution before redistributing a world; upstream rights are separate from
the code license.

## Street imagery adapter

`scripts/orthofacade_adapter.py` reads Orthofacade's per-wall JSON/RGBA output
from revision `a24e4ca349a1ceecec5c64517e62b6828f4aebac`. This is the offline
bridge for the Arnis street-imagery approach. It produces **candidates only**;
there is no world writer or completed Elmhurst/downtown imagery pilot yet.

Supply the existing save's `city-coverage.json` and an anchor JSON shaped like:

```json
{
  "walls": {
    "<upstream-wall-key>": {
      "world_frame_sha256": "<canonical frame SHA-256>",
      "source_sha256": "<existing wall/ground evidence SHA-256>",
      "a_world_xz": [32, 33],
      "b_world_xz": [36, 33],
      "base_world_y": 65
    }
  }
}
```

These example coordinates describe a fixture, not a real building. Obtain
anchors from the existing matched wall and measured ground, not the panorama
cluster's local height. `frame_digest(frame)` computes the canonical hash.

```sh
python scripts/orthofacade_adapter.py --blocks /path/to/texture/blocks \
  --anchors /path/to/anchors.json \
  --coverage runtime/traversal/saves/Earthcraft/city-coverage.json \
  --output runs/appearance-candidates/<new-id>
```

The adapter checks WGS84 endpoints against the anchored wall, retains the
inherited metre scale and east/south axes, and uses the explicit `observed`
mask. PNG alpha is a semantic class, not visibility. Filled colors, unknown
classes, and out-of-wall extensions do not become observations. Row zero maps
to the top; ground stays in the existing world's height frame.

Upstream A/B confidence tiers alone do not prove registration. Before any
candidate changes visible appearance, the pilot must verify held-out alignment,
retain source-image attribution, and match existing exposed faces. It must not
create wall blocks, extrapolate missing windows, or overwrite LiDAR geometry.
