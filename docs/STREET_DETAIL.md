# Street detail

Detail passes add trees, streets, cleaner buildings and water on top of the
generated world. Each pass writes a **layer**: a list of block edits, each with
its source. Layers are applied to **copies** of region files, never to the open
save. A layer can be undone, because every edit records what it replaced.

The first test area is the Water Tower tile (X −256…255, Z −256…255).

## Passes

Apply them in this order:

| Order | Script | Layer | What it does |
| --- | --- | --- | --- |
| 1 | `detail_buildings.py` | `buildings` | Closes thin or holey building shells from LiDAR. Keeps placement and height. |
| 2 | `detail_trees.py` | `trees` | Places trees from the LiDAR canopy height model. |
| 3 | `detail_street.py` | `street`, `streetlights` | Curbs, sidewalks, crossings, lane paint, lamps and furniture. |
| 4 | `detail_facade.py` | `facades` | Recolours outer walls only. Never adds or removes blocks. |
| 5 | `detail_water.py` | `water` | Fills open water (first test: Lake Michigan) from OSM shoreline ways. |

`detail_build.py` runs passes 1–4, `detail_apply.py` writes layers into region
copies, and `detail_render.py` renders before/after images.

## Sources

USGS 3DEP LiDAR (public domain), OpenStreetMap (ODbL), City of Chicago open
data (city terms), Panoramax and Mapillary street photos (CC BY-SA). Never
Google Street View or Google map tiles. The City streetlight data has no stated
licence, so it has its own `streetlights` layer and can be left out.

## Layer format (`detail/<layer>/v1/`)

```
```
manifest.json                 schema earthcraft.detail-layer.v1, frame, sources[], regions{r.X.Z:{path,bytes,sha256,edits,chunks}}, report
regions/r.<rx>.<rz>.jsonl.gz  line 1: {"schema":"earthcraft.detail-region.v1","region":[rx,rz],"palette":[...]}
                              then one line per chunk: {"c":[cx,cz],"x":[..],"y":[..],"z":[..],"p":[..],"q":[..],"s":[..]}
```
```

- `x`, `z` are inside the chunk (0–15); `y` is the world height.
- `p` is the new block (index into the palette).
- `q` is the block expected there now; `-1` means air. If the block changed
  (for example a player built there), the edit is skipped.
- `s` is the source (index into `manifest.sources`).

## Applying safely

`detail_apply.py` refuses to write into its input folder or into any folder
that holds `level.dat` or `session.lock`. All writes go through a byte cap and
a free-space floor. Untouched chunks keep their original bytes; edited chunks
are relit by the game. Every output file is re-read and checked.

## Mapillary

When a token exists, keep it in the `MAPILLARY_TOKEN` environment variable,
never in git or logs. Mapillary map features (lamps, hydrants, benches,
crosswalks) map onto the street furniture kinds, deduplicated against OSM
within 2.5 m. Keep Mapillary-derived data in its own layer.

## Reproduce

```sh
# 0. copies only (APFS clones on the save's host), then on a worker:
PYTHONPATH=scripts python scripts/detail_points.py --project USGS_LPC_IL_4County_Cook_2017_LAS_2019 \
  --frame frame.json --west -288 --north 289 --size 512 --halo 24 --out work/ept/cook2017
PYTHONPATH=scripts python scripts/detail_build.py --regions copy/region --frame frame.json \
  --points work/ept/cook2017 --sources work/sources --tile -256 -256 512 --out work/layers-b --layers buildings
PYTHONPATH=scripts python scripts/detail_apply.py --regions copy/region --layer work/layers-b/buildings --out work/staged
PYTHONPATH=scripts python scripts/detail_build.py --regions work/staged --frame frame.json --points work/ept/cook2017 \
  --sources work/sources --tile -256 -256 512 --out work/layers --layers trees,street,streetlights,facades \
  --facades facades.json
PYTHONPATH=scripts python scripts/detail_apply.py --regions copy/region --layer work/layers-b/buildings \
  --layer work/layers/trees --layer work/layers/street --layer work/layers/streetlights --layer work/layers/facades \
  --out work/after
PYTHONPATH=scripts python scripts/detail_water.py --regions copy/lake --frame frame.json \
  --osm lake_members.json --tile 512 0 512 --seed 1020 30 --land-seed 520 256 --out work/layers
PYTHONPATH=scripts python scripts/detail_render.py --region-dir work/after --jar 1.21.10.jar \
  --bounds -256 -256 256 256 --out work/after --views top --eye 50 90 230 0 -6
```

`sources/` holds the OSM halo extract (`osm_halo.json`) and the City GeoJSON
extracts named by dataset ID. Renders use the owned client jar's textures
only on the local machine; no textures are committed.
