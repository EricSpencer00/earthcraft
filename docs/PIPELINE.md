# Pipeline

```
regional_supervisor.py ──► regional_generate.py ──► hf_store.py ──► HF tiles/v2/
                                                                        │
                       Minecraft save ◄── hf_install.py ◄───────────────┘
                             │
                             └──► hf_store.py push-world ──► HF world/
detail_*.py ──► HF detail/<layer>/   (applied to copies of region files)
```

All commands run from the repository root with the repo venv
(`.venv/bin/python`, Python 3.11). Uploads also need a Python that has
`huggingface_hub` and a write token; pass it with `--hf-python PATH`.

## 1. Generate tiles

A tile is 512 m × 512 m. One worker builds one tile at a time in
`runs/scratch/gen/<tile>/`, packages it, uploads a batch of tiles to
`tiles/v2/` in one commit, checks the remote hash, and deletes the local copy.
The job list lives in `runs/regional/control/jobs.sqlite`.

First time on a new machine:

```sh
.venv/bin/python scripts/regional_generate.py --frame runs/chicago-adaptation-city-001/frame.json --seed-control
```

This copies the plan and job list from the dataset's
`archive/mini-worker-20260929/`. The world templates go in
`runs/regional/control/templates/`: fetch the public, player-free copy with
`scripts/clean_templates.py fetch --out runs/regional/control/templates`
(it produces byte-identical tiles).

Anyone can also contribute compute through dataset pull requests:
`scripts/contribute.py` (see [CONTRIBUTING.md](../CONTRIBUTING.md)). A
validator on the AWS host (`earthcraft-validate.timer`, every 30 min) checks
each open pull request and comments PASS or FAIL; it never merges.

Run, check, stop:

```sh
# one tile, as a smoke test
.venv/bin/python scripts/regional_generate.py --frame FRAME --hf-python "$HF_PYTHON" --tiles 17_54 --limit 1
# keep workers running (unattended)
.venv/bin/python scripts/regional_supervisor.py --frame FRAME --hf-python "$HF_PYTHON" --footprints
# status
.venv/bin/python scripts/regional_generate.py --frame FRAME --status
```

Stop the supervisor with Ctrl-C or `kill` (its workers stop with it).
`runs/regional/control/supervisor-status.json` and `control/logs/` show
progress. Failed tiles retry with backoff; `--requeue-failed` puts them back in
the queue. `configs/earthcraft-regional-generator.plist` is a launchd template
for running the supervisor at login.

Source downloads. Will and Kendall terrain comes from county ArcGrid ZIPs
on the ISGS clearinghouse; one ZIP member is a 31 km east-west strip
(~1.2 GB inflated). Members are fetched once with parallel 16 MiB range
requests, kept in the LRU cache and shared by every worker. All workers on a
machine share at most `EARTHCRAFT_HOST_CONNECTIONS` (default 4) connections
per source host, with exponential backoff on 429/5xx. At start the supervisor
ranks pending tiles so tiles reading the same strip run back to back
(`--route-locality`, once per plan), and each worker prefetches the next
tiles' members while its current tile computes.

### Scaling

- **More workers on one box:** set `EARTHCRAFT_WORKERS=N` in
  `/etc/earthcraft/pipeline.env` (or pass `--workers N`, 1-8) and restart. On
  AWS the footprint cap grows with it (cache + 5 GiB per worker + 1 GiB).
- **More boxes:** `scripts/aws_pipeline_setup.sh launch --count N` starts N
  more identical spot boxes that bootstrap themselves. Add `--dry-run` to
  print the AWS calls without launching anything.
- **No collisions without a shared database:** every box is tagged
  `EarthcraftShard=i/T` and claims only its share (`--shard i/T`, or
  `EARTHCRAFT_SHARD`): whole 4-tile (2 km) east-west bands of the plan grid,
  band mod T, so source strips stay on one box. Existing boxes pick up a new
  tag at their next update tick. A tile that is already on the Hub is adopted,
  not regenerated, so re-sharding never duplicates work.

## 2. Install into the save

Quit Minecraft to the title screen first. The installer refuses while the game
holds the save's `session.lock`.

```sh
.venv/bin/python scripts/hf_install.py --save runtime/traversal/saves/Earthcraft --hf-python "$HF_PYTHON" --dry-run
.venv/bin/python scripts/hf_install.py --save runtime/traversal/saves/Earthcraft --hf-python "$HF_PYTHON"
```

It downloads tiles that are not yet in the save, adds missing chunks, and
upgrades a chunk only if nobody changed it in the game. Before it touches a
file it backs up the original to `archive/install-backups/` in the dataset.
At the end it runs `hf_store.py push-world --only-changed` to update `world/`.

## 3. Detail passes

`detail_build.py` builds layers (buildings, trees, street, streetlights,
facades), `detail_water.py` builds water, and `detail_apply.py` writes them
into **copies** of region files, never into the open save. See
[STREET_DETAIL.md](STREET_DETAIL.md).

## 4. Visual QA

```sh
node scripts/qa_sample.mjs --count 24 --out runs/scratch/qa/<run-id>
```

It renders random chunks from the dataset through the 3D viewer. Score them
with `.claude/commands/qa-chunks.md`, then upload the run with
`hf_store.py push-files`.

## Disk rules

- Every write goes through a guard with a byte cap and a free-space floor,
  checked before each write. Defaults: source cache 12 GiB, total footprint
  25 GiB, one tile 4 GiB, free-space floor 40 GiB.
- When the guard refuses, workers wait; they do not fail.
- Scratch lives in `runs/scratch/<task>/` and is empty when a task ends.
- Nothing bulky is kept locally: the dataset is the only durable store.
- Keep Hugging Face caches inside scratch, never in `~/.cache`.

## `hf_store.py` commands

| Command | Use |
| --- | --- |
| `push-batch` | Upload a batch of packaged tiles (used by the generator). |
| `push-world` | Upload a closed save to `world/` (`--only-changed` for a sync). |
| `push-files` | Upload arbitrary files, such as QA runs or detail layers. |
| `verify` | Check remote files against their recorded hashes. |
| `migrate` | Move older private datasets into this one. |

## Health monitoring

A GitHub Actions workflow (`monitor.yml`) runs every 4 hours to check pipeline freshness, dataset integrity, CI health, and site availability. See `scripts/monitor.py`.
