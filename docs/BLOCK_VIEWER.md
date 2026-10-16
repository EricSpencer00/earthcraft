# Block viewer

`dashboard/blocks.html` is the 3D block viewer beside the world map
(`dashboard/index.html`). Like the rest of `dashboard/`, it has no build step:
ES modules, vendored three.js, and bundled fonts.

`blocks.html` reads Minecraft Java 1.21.10 Anvil region files over HTTP Range
requests. For each region it reads the 8 KiB location header, then fetches only
the requested chunks; nearby sectors are combined into one bounded request.
Chunks are decoded in the browser: zlib, gzip, or uncompressed NBT, plus 1.18+
block-state palettes. LZ4 chunks and external `.mcc` chunks are reported as
unreadable. The viewer culls faces against loaded neighbouring chunks, shades
each face by direction, and adds vertex ambient occlusion. Glass, water, and
leaves are drawn in a separate transparent pass. Block colors are authored
approximations in `block-colors.mjs`; no Minecraft textures are bundled.

In the world map, select a cell and choose **View blocks in 3D**. This opens the
viewer at the cell centre, with enough chunks to cover the cell, up to radius 8.

| Parameter | Meaning |
| --- | --- |
| `base` | Region folder URL. Defaults to the published dataset (below). |
| `revision` | Dataset revision for the default base (`main` unless pinned). |
| `chunk=cx,cz` | Centre chunk, absolute coordinates. Alternatives: `block=x,z` or `region=X,Z`. |
| `radius` | Chunks around the centre, 0–8 (default 3, or 2 in QA mode). |
| `view` | `orbit`, `iso`, `top` or `street`. |
| `yaw`, `pitch` | Heading in degrees clockwise from north; pitch is downward. |
| `floor` | Cut away blocks below this Y (default: a few blocks under the local ground). |
| `qa=1`, `w`, `h` | Fixed-size capture without page chrome. |

Published layout: `https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/<revision>/world/region/<bx>/r.X.Z.mca`,
where `bx = floor(X / 16)`. The optional coverage index,
`world/index/regions.json` (`earthcraft.world-regions.v1`), has a 128-byte
chunk bitmap per region. Chunk index `(cx & 31) + (cz & 31) * 32` maps to byte
`i >> 3`, bit `i & 7`, least significant bit first; `parseRegionIndex` in
`anvil.mjs` reads it. A missing region file is reported as “no region file”, not
as an error.

With `qa=1`, the page shows only the canvas. `window.__earthcraftRender` starts as
`{status:'loading'}`. After the frame is drawn it becomes `done` or `error`, with
`chunks_requested`, `chunks_loaded`, `blocks`, `missing`, `errors`, `error`,
`floor_y`, and the camera. Capture the first `<canvas>` after the status
changes. Headless Chromium needs `--use-angle=swiftshader --enable-unsafe-swiftshader`.

## Local checks

Python's `http.server` ignores Range. Use the development server, which serves
`dashboard/` and mounts a region folder read-only at `/data/`:

```sh
node tests/range_server.mjs --data /path/to/save/region-copy --port 8765
# http://127.0.0.1:8765/blocks.html?base=/data/&chunk=2,2&radius=3
```

The mounted folder must use the published `<bx>/r.X.Z.mca` layout. Do not point
it at a save that Minecraft has open; use a copy.

```sh
node tests/anvil.mjs            # NBT, palettes, Range client and mesher
node tests/blocks_browser.mjs   # Playwright: QA captures, layout, accessibility
```

The browser test uses a 45 KiB, nine-chunk Water Tower fixture in `tests/fixtures/anvil`.
Chunk records are byte-identical to the save; only the region header was rebuilt.
