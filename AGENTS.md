# Earthcraft agent rules

These apply to every agent (Codex, Claude, scripts) working on Earthcraft.

## One of everything

- **One checkout:** `~/earthcraft` on the MacBook. Do not create clones, worktrees, or copies of this
  repository anywhere under a home directory (including `~/Documents/Codex`, `~/.codex/worktrees`,
  `~/GitHub`, `~/earthcraft-worktrees`). Work on a branch in this checkout. If a tool insists on an
  isolated copy, put it under a temporary directory and delete it before you finish.
- **One save:** `runtime/traversal/saves/Earthcraft`, opened by the launcher profile
  "Earthcraft — Geographic Explorer". It is the canonical placement of terrain and buildings. Do not
  create other saves, playtest profiles, or `worlds/` copies. Never write to the save while Minecraft
  holds `session.lock`.
- **One dataset:** [`EricSpencer00/earthcraft`](https://huggingface.co/datasets/EricSpencer00/earthcraft)
  on Hugging Face is the only durable store for generated data. Do not create new datasets.

## Commits

- No `Co-authored-by` trailers: `scripts/public_tree_check.py` fails CI if any commit message in
  history contains one.

## Storage

- The LaCie is retired. Never write to or depend on external drives.
- Write generated data straight to Hugging Face with `scripts/hf_store.py`: small batch into capped
  local scratch → verify → commit → confirm remote hash → delete local. Never stage bulk data locally.
- Every write path needs a byte cap and a free-space floor, checked before writing. Write each file
  once. Keep Hugging Face client caches bounded and inside guarded scratch.
- Scratch lives under `runs/scratch/<task>/` (gitignored) and is deleted when the task ends.

## Data

- Public data must not contain player data (`playerdata/`, `stats/`, `advancements/`, `level.dat`
  `Data.Player`).
- Street-level imagery: Mapillary, Panoramax, and municipal open data only. Never Google Street View
  or Google map tiles; their terms forbid extraction.
- Keep source attribution and licence with every derived layer.
