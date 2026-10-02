# Earthcraft world snapshots

The destination is the private dataset
[EricSpencer00/earthcraft-minecraft-world](https://huggingface.co/datasets/EricSpencer00/earthcraft-minecraft-world).
It holds the entire **generated save**, with partial Chicagoland coverage, rather
than a generated planet. Creating the repository alone does not mean the world
has been uploaded. A snapshot is ready only when its checked `manifest.json`
appears under `snapshots/<id>/`.

The save retains its region files, dimensions, player inventories/positions,
datapacks, custom resource pack, previous level backup, and source receipts.
Minecraft client/mod jars and launcher authentication are not uploaded. The
save lock, derived Distant Horizons caches, Finder metadata, and diagnostic
`pre-expansion-evidence` copies are excluded and listed in the manifest.

## Verified snapshot

[`chicagoland-20261001-closed-save`](https://huggingface.co/datasets/EricSpencer00/earthcraft-minecraft-world/tree/main/snapshots/chicagoland-20261001-closed-save)
is uploaded to the private dataset. All 114 snapshot files were verified against
their checksums, including 111 geographic/metadata archives and the completion
manifest. The archives contain 6,769 files: 16.95 GB before compression and
2.74 GB compressed.

The snapshot records 4,126,030 serialized Anvil chunks and 1,755 installed 512 m
tiles: 1,714 base-quality tiles and 41 scan/roof upgrades. Serialized chunk
counts describe stored world files and do not measure observed regional coverage.
Chicagoland remains partially generated.

A selected restore covering metadata, downtown, and Elmhurst verified 303 files.
An independent Hub download also matched the manifest and all 77 metadata-file
hashes. The restored save has not been launched in Minecraft; those checks
establish archive integrity and coordinate-preserving restoration, not visual
accuracy or game compatibility. The private dataset requires authorized access.

## Freeze before packaging

Save and Quit to Title in Minecraft. The staging command takes the same POSIX
record lock as Java; it refuses to copy a running world. On APFS it clones files
quickly while holding that lock. The clone is independent of future edits.
Generation/publishing must also respect the lock. A changed file list, size or
timestamp invalidates the stage.

Run from the repository on the MacBook, using a fresh ID/destination:

```sh
python scripts/world_snapshot.py stage \
  --world runtime/traversal/saves/Earthcraft \
  --destination runs/world-snapshots/<id>/world
```

Transfer only this immutable staged save and `scripts/world_snapshot.py` to
the Mac mini's mounted LaCie scratch volume. Before each SSH/rsync operation,
verify the configured `mac-mini` alias on the MacBook with `ssh -G mac-mini`,
the actual hostname, and the mounted volume/free space. Do not copy Hub tokens
to the worker. Packaging runs on the worker, not the coordinating MacBook:

```sh
python3 world_snapshot.py pack --world /path/to/staged/world \
  --output /path/to/package --snapshot-id <id> --region-span 8
```

Each shard groups up to 8 × 8 regions, or 4096 × 4096 blocks. Region, entity,
POI, and oversized external chunk files are kept together by dimension and
geographic position. Negative coordinates use floor division. Global metadata
has its own shard. This packaging does not transform NBT, change block palettes,
move the spawn, or regenerate terrain.

Archives use streaming gzip level 1. An interrupted packaging run verifies and
reuses finished archives; changing its staged input requires a new package.
The manifest records archive/file SHA-256 values, actual Anvil chunk counts,
the world frame, and installed quality counts. Completed shard identities are
not replaced silently.

## Upload and restore

Return the checked package to the MacBook and use the Python environment with
the existing authenticated `huggingface_hub` installation:

```sh
python scripts/upload_world_snapshot.py --package /path/to/package \
  --repo-id EricSpencer00/earthcraft-minecraft-world
```

The uploader checks destination ownership and private visibility before every
write, verifies each uploaded checksum, resumes already verified files, and
publishes the completion manifest last. Only manifest-listed archives, the
portable restore script, and the dataset card are uploaded. Local progress
files stay local. A completed snapshot ID cannot be reused for changed content.

Download the whole snapshot directory and restore into a **new** save:

```sh
python restore_world_snapshot.py --package /path/to/downloaded/snapshot \
  --destination /path/to/minecraft/saves/Earthcraft
```

The restore script needs only Python's standard library. It checks archive and
file hashes and rejects unsafe paths/links. Repeat `--shard` with manifest keys
to restore selected geographic areas; metadata is included automatically.
Selected restores retain the original coordinates and player position, which
may fall outside the selected area. The full restore is the default.

Use Minecraft Java 1.21.10 with compatible renderer mods installed separately.
Distant Horizons rebuilds its cache from restored blocks. Read the retained
source/photo attribution before redistributing the save; upstream imagery
rights are separate from the code license.

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
