"""Lake Michigan as procedural water tiles: a bathymetry crop instead of region files.

    PYTHONPATH=scripts python scripts/lake_tiles.py crop --frame configs/world-frame.json \
        --tif michigan_lld.tif --bbox -215 -168 114 251 --out lake/
    PYTHONPATH=scripts python scripts/lake_tiles.py select --frame configs/world-frame.json \
        --plan-tiles plan-tiles.json --out lake/
    PYTHONPATH=scripts python scripts/lake_tiles.py index --frame configs/world-frame.json \
        --templates templates/ --out lake/
    PYTHONPATH=scripts python scripts/lake_tiles.py materialize --frame configs/world-frame.json \
        --templates templates/ --out lake/ --tiles 24_-168 --region-dir region/

A water tile is fully determined by the lake bed, so the dataset stores only the
inputs (``lake/v1``): ``bathymetry.bin.gz`` + ``bathymetry.json`` (the NOAA grid
cropped to the plan's box, gzipped int16 decimetres, ~0.7 MB) and ``index.json`` (every lake tile,
with the SHA-256 of its decompressed chunks). The installer rebuilds a tile with
``materialize`` and checks that digest; the site builds the same chunks in the
browser (dashboard/lake.mjs). One packed water tile is ~540 KB, so storing the
5,934 tiles of the plan's box would take ~3.2 GB; the inputs take ~1 MB.

Bed: NOAA NCEI Great Lakes Bathymetry, Lake Michigan, 3 arc-second (public
domain; metres relative to Low Water Datum 176.0 m), bilinear at each block
centre: bed y = 60 + round(depth). The surface is y=60 (OSM ``ele=176`` with the
frame's -116 m vertical offset), as in the shore water layer. Each column holds
a two-block sand bed, water up to the surface and air above; beds below the
world floor are clamped and counted. A tile is selected when the plan does not
cover it and at least 95% of a 16 m grid over it is lake.
"""
import argparse
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path

import nbtlib as n
import numpy as np
from pyproj import Transformer

from contributions import chunk_digest
from dataset_coverage import projection
from inspect_world import chunks
from metric_world import BLOCK, PALETTE, packed, palette_indices, region_write

TILE = 512
SURFACE_Y = 60
MIN_Y = -64
BED_DEPTH = 2
LAKE_FRACTION = 0.95
NODATA = -32768
SCHEMA = 'earthcraft.lake-tiles.v1'
SOURCE = {'name': 'NOAA NCEI Great Lakes Bathymetry, Lake Michigan (3 arc-second)',
          'url': 'https://www.ngdc.noaa.gov/mgg/greatlakes/michigan.html',
          'license': 'U.S. Government public domain',
          'vertical_datum': 'Low Water Datum 176.0 m (IGLD 1985); values are metres, negative below datum'}


class Bathymetry:
    """The published crop: int16 decimetres on a lon/lat grid, bilinear at block centres."""

    def __init__(self, folder, frame):
        header = json.loads((Path(folder) / 'bathymetry.json').read_text())
        raw = np.frombuffer(gzip.decompress((Path(folder) / 'bathymetry.bin.gz').read_bytes()), dtype='<i2')
        grid = raw.reshape(header['height'], header['width']).astype(np.float64)
        grid[raw.reshape(grid.shape) == NODATA] = np.nan
        self.depth = grid * header['scale']
        self.lon0, self.lat0, self.step = header['west'], header['north'], header['step_deg']
        self.to_lonlat = Transformer.from_crs(frame['crs'], 'EPSG:4326', always_xy=True)
        self.frame = frame

    def sample(self, x, z):
        lon, lat = self.to_lonlat.transform(self.frame['west'] + np.asarray(x, float), self.frame['north'] - np.asarray(z, float))
        col = (np.asarray(lon) - self.lon0) / self.step - 0.5
        row = (self.lat0 - np.asarray(lat)) / self.step - 0.5
        c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
        fc, fr = col - c0, row - r0
        h, w = self.depth.shape
        ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < w) & (r0 + 1 < h)
        c0, r0 = np.clip(c0, 0, w - 2), np.clip(r0, 0, h - 2)
        d = self.depth
        value = (d[r0, c0] * (1 - fc) * (1 - fr) + d[r0, c0 + 1] * fc * (1 - fr)
                 + d[r0 + 1, c0] * (1 - fc) * fr + d[r0 + 1, c0 + 1] * fc * fr)
        return np.where(ok, value, np.nan)

    def bed(self, tx, tz):
        """[z, x] bed y for one tile and the number of columns clamped at the world floor."""
        centre = np.arange(TILE) + 0.5
        gx, gz = np.meshgrid(centre, centre)
        depth = self.sample(tx * TILE + gx, tz * TILE + gz)
        bed = SURFACE_Y + np.round(np.nan_to_num(depth, nan=-1.0)).astype(int)
        clamped = int((bed < MIN_Y + BED_DEPTH - 1).sum())
        return np.clip(bed, MIN_Y + BED_DEPTH - 1, SURFACE_Y - 1), clamped


def crop(a):
    import rasterio
    frame = json.loads(a.frame.read_text())
    inverse = Transformer.from_crs(frame['crs'], 'EPSG:4326', always_xy=True)
    x0, z0, x1, z1 = a.bbox
    corners = [inverse.transform(frame['west'] + x * TILE, frame['north'] - z * TILE)
               for x in (x0, x1 + 1) for z in (z0, z1 + 1)]
    lons, lats = zip(*corners)
    with rasterio.open(a.tif) as src:
        step = src.res[0]
        if abs(src.res[1] - step) > 1e-12 or src.transform.b or src.transform.d:
            raise ValueError('Expected a north-up grid with square cells')
        col0 = max(0, int(math.floor((min(lons) - src.bounds.left) / step)) - 2)
        col1 = min(src.width, int(math.ceil((max(lons) - src.bounds.left) / step)) + 2)
        row0 = max(0, int(math.floor((src.bounds.top - max(lats)) / step)) - 2)
        row1 = min(src.height, int(math.ceil((src.bounds.top - min(lats)) / step)) + 2)
        data = src.read(1, window=((row0, row1), (col0, col1))).astype(np.float64)
        data[data == src.nodata] = np.nan
        source_sha = hashlib.sha256(Path(a.tif).read_bytes()).hexdigest()
        west, north = src.bounds.left + col0 * step, src.bounds.top - row0 * step
        crs = str(src.crs)
    grid = np.where(np.isnan(data), NODATA, np.clip(np.round(data * 10), -32767, 32767)).astype('<i2')
    a.out.mkdir(parents=True, exist_ok=True)
    raw = grid.tobytes()
    (a.out / 'bathymetry.bin.gz').write_bytes(gzip.compress(raw, 9, mtime=0))
    header = {'schema': 'earthcraft.bathymetry-crop.v1', 'width': grid.shape[1], 'height': grid.shape[0],
              'west': west, 'north': north, 'step_deg': step, 'scale': 0.1, 'nodata': NODATA, 'dtype': 'int16 little-endian',
              'crs': crs, 'note': 'NAD83 lon/lat; within ~1 m of WGS 84 here, sampled as WGS 84',
              'source': {**SOURCE, 'sha256': source_sha}, 'bbox_tiles': a.bbox,
              'file': 'bathymetry.bin.gz', 'bin_sha256': hashlib.sha256(raw).hexdigest()}
    (a.out / 'bathymetry.json').write_text(json.dumps(header, indent=1))
    print(f'crop {grid.shape[1]} x {grid.shape[0]} cells, {(a.out / "bathymetry.bin.gz").stat().st_size} bytes gzipped')


def select(a):
    frame = json.loads(a.frame.read_text())
    bathy = Bathymetry(a.out, frame)
    plan = set(json.loads(a.plan_tiles.read_text()))
    x0, z0, x1, z1 = json.loads((a.out / 'bathymetry.json').read_text())['bbox_tiles']
    grid = np.arange(8, TILE, 16)
    gx, gz = np.meshgrid(grid, grid)
    tiles = []
    for tz in range(z0, z1 + 1):
        for tx in range(x0, x1 + 1):
            if f'{tx}_{tz}' in plan:
                continue
            depth = bathy.sample(tx * TILE + gx, tz * TILE + gz)
            if np.mean(np.nan_to_num(depth, nan=1.0) < 0) >= LAKE_FRACTION:
                tiles.append(f'{tx}_{tz}')
    (a.out / 'selection.json').write_text(json.dumps({'tiles': tiles, 'lake_fraction': LAKE_FRACTION,
                                                      'plan_tiles_excluded': len(plan)}))
    print(f'{len(tiles)} lake tiles outside the plan')


def chunk_tag(sample, cx, cz, bed):
    """One 16x16 water chunk; bed is [z, x] lake-bed y (sand top)."""
    height = SURFACE_Y - MIN_Y + 1
    yy = np.arange(MIN_Y, SURFACE_Y + 1)[:, None, None]
    volume = np.full((height, 16, 16), BLOCK['air'], np.uint8)
    volume[(yy > bed[None]) & (yy <= SURFACE_Y)] = BLOCK['water']
    volume[(yy <= bed[None]) & (yy > bed[None] - BED_DEPTH)] = BLOCK['sand']
    tag = copy.copy(sample)
    for name in ('entities', 'block_entities', 'block_ticks', 'fluid_ticks'):
        tag[name] = n.List[n.Compound]([])
    tag['structures'] = n.Compound({'starts': n.Compound({}), 'References': n.Compound({})})
    tag['xPos'], tag['zPos'], tag['yPos'] = n.Int(cx), n.Int(cz), n.Int(MIN_Y // 16)
    tag['isLightOn'] = n.Byte(0)
    sections = []
    for si in range(math.ceil(height / 16)):
        section = volume[si * 16:si * 16 + 16]
        if not section.any():
            continue
        if len(section) < 16:
            section = np.concatenate([section, np.zeros((16 - len(section), 16, 16), np.uint8)])
        values, inverse = palette_indices(section)
        states = n.Compound({'palette': n.List[n.Compound]([n.Compound({'Name': n.String('minecraft:' + PALETTE[v])}) for v in values])})
        if len(values) > 1:
            states['data'] = packed(inverse, max(4, (len(values) - 1).bit_length()))
        sections.append(n.Compound({'Y': n.Byte(si + MIN_Y // 16), 'block_states': states,
                                    'biomes': n.Compound({'palette': n.List[n.String](['minecraft:plains'])})}))
    tag['sections'] = n.List[n.Compound](sections)
    bits = (1024).bit_length()
    surface = packed(np.full(256, SURFACE_Y + 1 - MIN_Y), bits)
    tag['Heightmaps'] = n.Compound({'WORLD_SURFACE': surface, 'MOTION_BLOCKING': surface,
                                    'MOTION_BLOCKING_NO_LEAVES': surface,
                                    'OCEAN_FLOOR': packed((bed + 1 - MIN_Y).ravel(), bits)})
    tag['PostProcessing'] = n.List[n.List[n.Short]]([n.List[n.Short]([]) for _ in sections])
    return tag


def region_bytes(bathy, sample, tile, scratch):
    """The tile's .mca bytes, its bed range and clamped column count."""
    tx, tz = map(int, tile.split('_'))
    bed, clamped = bathy.bed(tx, tz)
    records = [(tx * 32 + cx, tz * 32 + cz, chunk_tag(sample, tx * 32 + cx, tz * 32 + cz,
                                                     bed[cz * 16:cz * 16 + 16, cx * 16:cx * 16 + 16]))
               for cz in range(32) for cx in range(32)]
    path = Path(scratch) / f'r.{tx}.{tz}.mca'
    path.unlink(missing_ok=True)
    region_write(path, records)
    raw = path.read_bytes()
    path.unlink()
    return raw, [int(bed.min()), int(bed.max())], clamped


def template_sample(templates):
    _, sample, _ = next(chunks(Path(templates) / 'chicago-water-tower-64/Arnis World 1/region/r.0.0.mca'))
    return copy.deepcopy(sample)


def index(a):
    frame = json.loads(a.frame.read_text())
    bathy = Bathymetry(a.out, frame)
    sample = template_sample(a.templates)
    tiles = json.loads((a.out / 'selection.json').read_text())['tiles']
    header = json.loads((a.out / 'bathymetry.json').read_text())
    entries = {}
    for i, tile in enumerate(tiles, 1):
        raw, bed, clamped = region_bytes(bathy, sample, tile, a.out)
        entries[tile] = {'chunks_sha256': chunk_digest(raw), 'bed_y': bed, **({'clamped_columns': clamped} if clamped else {})}
        if i % 500 == 0:
            print(f'{i}/{len(tiles)}', flush=True)
    out = {'schema': SCHEMA, 'frame': {k: frame[k] for k in ('crs', 'west', 'north')}, 'projection': projection(frame),
           'surface_y': SURFACE_Y, 'surface_m': 176.0, 'bed_depth_blocks': BED_DEPTH, 'min_y': MIN_Y,
           'lake_fraction': LAKE_FRACTION, 'bathymetry': {'json': 'bathymetry.json', 'bin_sha256': header['bin_sha256'],
                                                           'source': header['source']},
           'generator': 'scripts/lake_tiles.py (materialize)', 'site_generator': 'dashboard/lake.mjs',
           'digest': 'chunks_sha256 = contributions.chunk_digest: SHA-256 of every decompressed chunk, slot by slot',
           'tiles': entries}
    (a.out / 'index.json').write_text(json.dumps(out, separators=(',', ':')))
    print(f'indexed {len(entries)} tiles')


def materialize(a):
    """Write .mca files for the given tiles and check each against the index."""
    frame = json.loads(a.frame.read_text())
    bathy = Bathymetry(a.out, frame)
    sample = template_sample(a.templates)
    entries = json.loads((a.out / 'index.json').read_text())['tiles']
    a.region_dir.mkdir(parents=True, exist_ok=True)
    for tile in a.tiles:
        raw, _, _ = region_bytes(bathy, sample, tile, a.region_dir)
        if chunk_digest(raw) != entries[tile]['chunks_sha256']:
            raise ValueError(f'{tile}: rebuilt chunks differ from the index')
        tx, tz = map(int, tile.split('_'))
        target = a.region_dir / f'r.{tx}.{tz}.mca'
        if target.exists():
            raise FileExistsError(target)
        target.write_bytes(raw)
        print(f'{target}: {len(raw)} bytes, chunks match the index')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('crop', 'select', 'index', 'materialize'):
        s = sub.add_parser(name)
        s.add_argument('--frame', type=Path, required=True)
        s.add_argument('--out', type=Path, required=True, help='folder holding the lake inputs and index')
    sub.choices['crop'].add_argument('--tif', type=Path, required=True)
    sub.choices['crop'].add_argument('--bbox', type=int, nargs=4, required=True, metavar=('X0', 'Z0', 'X1', 'Z1'))
    sub.choices['select'].add_argument('--plan-tiles', type=Path, required=True)
    for name in ('index', 'materialize'):
        sub.choices[name].add_argument('--templates', type=Path, required=True)
    sub.choices['materialize'].add_argument('--tiles', nargs='+', required=True)
    sub.choices['materialize'].add_argument('--region-dir', type=Path, required=True)
    a = p.parse_args()
    {'crop': crop, 'select': select, 'index': index, 'materialize': materialize}[a.command](a)


if __name__ == '__main__':
    main()
