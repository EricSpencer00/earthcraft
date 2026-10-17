# Site

`dashboard/` is the website at
[ericspencer.us/earthcraft](https://ericspencer.us/earthcraft/). It is plain
HTML and ES modules with no build step. Libraries (MapLibre, three.js) and
fonts are vendored under `dashboard/assets/`.

## Pages

- `index.html` — the map. It shows which 256 m cells are built, from
  `progress/earth.json`. Pick a cell and choose **View blocks in 3D**.
- `blocks.html` — the 3D block viewer. It reads region files straight from the
  dataset with HTTP Range requests and decodes the chunks in the browser.
  Block colours come from `block-colors.mjs`; no Minecraft textures are used.

Viewer URL options:

| Option | Meaning |
| --- | --- |
| `chunk=cx,cz` (or `block=x,z`, `region=X,Z`) | Where to look. |
| `radius` | Chunks around the centre, 0–8 (default 3). |
| `view` | `orbit`, `iso`, `top` or `street`. |
| `revision` | Dataset commit to read (default: the pinned one; `main` for latest). |
| `base` | Another region folder URL, for local testing. |
| `qa=1` | Canvas only, for screenshots (used by `scripts/qa_sample.mjs`). |

## View cache

The Water Tower view is the most visited, so its chunks are packed into
`dashboard/cache/water-tower.bin` and `.json` and served from GitHub Pages.
That view then makes no requests to Hugging Face. To rebuild it:

```sh
node scripts/build_view_cache.mjs --revision <40-hex dataset commit>
```

Then set `PINNED_REVISION` in `dashboard/anvil.mjs` to the same commit.
`tests/view_cache.mjs` checks that they match.

## Deploy

`.github/workflows/progress-pages.yml` deploys on every push to `main` that
touches the site. It rebuilds `progress/earth.json`
(`scripts/progress_snapshot.py`), copies `dashboard/` into `site/`, runs the
browser tests, and publishes. The site must stay under 20 MiB.

## Test locally

Python's `http.server` does not support Range requests. Use:

```sh
node tests/range_server.mjs --data /path/to/region-copy --port 8765
# http://127.0.0.1:8765/blocks.html?base=/data/&chunk=2,2
```

Never point it at a save Minecraft has open; use a copy. Tests:

```sh
node tests/anvil.mjs              # decoder and mesher
node tests/view_cache.mjs         # cache matches the pinned revision
node tests/dashboard_browser.mjs  # map (needs @playwright/test)
node tests/blocks_browser.mjs     # 3D viewer
```
