"""Quick QA renders for street-detail layers (top-down map and eye-level views).

This is a judging aid, not a viewer: it decodes a bounded block window into a
dense ``[z, x, y]`` array of palette indices and draws flat-shaded faces whose
colours are the mean texels of the owned Minecraft client jar (biome tints for
grass and leaves use the plains colormap values). Renders never write region
files.

    PYTHONPATH=scripts python scripts/detail_render.py --region-dir R \
        --jar 1.21.10.jar --bounds -256 -256 256 256 --out before
"""
import argparse
import io
import json
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from detail_anvil import ChunkVolume, block_name, read_region

# Plains biome tints (vanilla colormap samples at temperature 0.8, downfall 0.4).
GRASS_TINT = np.array([145, 189, 89]) / 255
FOLIAGE_TINT = np.array([119, 171, 47]) / 255
TINTED = {'grass_block': 'top', 'short_grass': 'all', 'tall_grass': 'all', 'fern': 'all',
          'oak_leaves': 'foliage', 'jungle_leaves': 'foliage', 'acacia_leaves': 'foliage',
          'dark_oak_leaves': 'foliage', 'mangrove_leaves': 'foliage', 'vine': 'foliage'}
FIXED_TINT = {'birch_leaves': np.array([128, 167, 85]) / 255,
              'spruce_leaves': np.array([97, 153, 97]) / 255}
# Texture names for blocks whose model does not use <name>.png.
TEXTURE_ALIASES = {
    'grass_block': ('grass_block_top', 'grass_block_side'),
    'oak_log': ('oak_log_top', 'oak_log'), 'birch_log': ('birch_log_top', 'birch_log'),
    'spruce_log': ('spruce_log_top', 'spruce_log'), 'dark_oak_log': ('dark_oak_log_top', 'dark_oak_log'),
    'jungle_log': ('jungle_log_top', 'jungle_log'),
    'water': ('water_still', 'water_still'), 'smooth_stone_slab': ('smooth_stone', 'smooth_stone_slab_side'),
    'smooth_stone': ('smooth_stone', 'smooth_stone'),
    'stone_brick_slab': ('stone_bricks', 'stone_bricks'), 'stone_brick_wall': ('stone_bricks', 'stone_bricks'),
    'andesite_wall': ('andesite', 'andesite'), 'polished_andesite_slab': ('polished_andesite', 'polished_andesite'),
    'cobblestone_wall': ('cobblestone', 'cobblestone'), 'iron_bars': ('iron_bars', 'iron_bars'),
    'sandstone': ('sandstone_top', 'sandstone'), 'smooth_sandstone': ('sandstone_top', 'sandstone_top'),
    'cut_sandstone': ('sandstone_top', 'cut_sandstone'),
    'polished_blackstone_wall': ('polished_blackstone', 'polished_blackstone'),
    'polished_blackstone_button': ('polished_blackstone', 'polished_blackstone'),
    'blackstone_wall': ('blackstone_top', 'blackstone'),
    'spruce_fence': ('spruce_planks', 'spruce_planks'), 'oak_fence': ('oak_planks', 'oak_planks'),
    'dark_oak_fence': ('dark_oak_planks', 'dark_oak_planks'),
    'spruce_slab': ('spruce_planks', 'spruce_planks'), 'dark_oak_slab': ('dark_oak_planks', 'dark_oak_planks'),
    'spruce_trapdoor': ('spruce_trapdoor', 'spruce_trapdoor'),
    'lantern': ('lantern', 'lantern'), 'sea_lantern': ('sea_lantern', 'sea_lantern'),
    'redstone_lamp': ('redstone_lamp', 'redstone_lamp'),
    'chain': ('chain', 'chain'), 'end_rod': ('end_rod', 'end_rod'),
    'light_gray_carpet': ('light_gray_wool', 'light_gray_wool'), 'white_carpet': ('white_wool', 'white_wool'),
    'yellow_carpet': ('yellow_wool', 'yellow_wool'), 'gray_carpet': ('gray_wool', 'gray_wool'),
    'black_carpet': ('black_wool', 'black_wool'),
    'quartz_block': ('quartz_block_top', 'quartz_block_side'), 'smooth_quartz': ('quartz_block_bottom', 'quartz_block_bottom'),
    'mud_bricks': ('mud_bricks', 'mud_bricks'), 'rooted_dirt': ('rooted_dirt', 'rooted_dirt'),
    'coarse_dirt': ('coarse_dirt', 'coarse_dirt'), 'podzol': ('podzol_top', 'podzol_side'),
    'moss_block': ('moss_block', 'moss_block'), 'dirt_path': ('dirt_path_top', 'dirt_path_side'),
    'polished_andesite': ('polished_andesite', 'polished_andesite'),
    'glass_pane': ('glass', 'glass'), 'gray_stained_glass_pane': ('gray_stained_glass', 'gray_stained_glass'),
    'light_blue_stained_glass_pane': ('light_blue_stained_glass', 'light_blue_stained_glass'),
    'tinted_glass': ('tinted_glass', 'tinted_glass'), 'hay_block': ('hay_block_top', 'hay_block_side'),
    'deepslate_tiles': ('deepslate_tiles', 'deepslate_tiles'), 'calcite': ('calcite', 'calcite'),
    'red_sandstone': ('red_sandstone_top', 'red_sandstone'),
    'terracotta': ('terracotta', 'terracotta'),
    'dark_oak_slab': ('dark_oak_planks', 'dark_oak_planks'), 'oak_slab': ('oak_planks', 'oak_planks'),
    'cauldron': ('cauldron_top', 'cauldron_side'),
    'light_blue_stained_glass_pane': ('light_blue_stained_glass', 'light_blue_stained_glass'),
}
# Thin or partial blocks: (half width in x/z, y bottom, y top) of the box the eye-level renderer intersects.
SHAPES = {'_wall': (0.25, 0, 1), 'iron_bars': (0.08, 0, 1), '_pane': (0.08, 0, 1), 'lantern': (0.19, 0.06, 0.6),
          '_fence': (0.125, 0, 1), 'chain': (0.05, 0, 1), '_slab': (0.5, 0, 0.5), '_carpet': (0.5, 0, 0.07)}
COVERAGE = {'_wall': 0.6, 'iron_bars': 0.35, '_pane': 0.35, 'lantern': 0.55, '_fence': 0.45, 'cauldron': 0.85,
            '_slab': 0.9, 'chain': 0.25}


def block_shapes(keys):
    out = np.tile(np.array([0.5, 0.0, 1.0]), (len(keys), 1))
    for i, key in enumerate(keys):
        name = block_name(key).removeprefix('minecraft:')
        for suffix, shape in SHAPES.items():
            if suffix in name:
                out[i] = shape
        if name == 'lantern' and 'hanging=true' in key:
            out[i] = (0.19, 0.06, 0.63)
        if name.endswith('_slab') and 'type=top' in key:
            out[i] = (0.5, 0.5, 1.0)
    return out


@lru_cache(maxsize=4)
def _jar(path):
    return zipfile.ZipFile(path)


def _texture_mean(jar, name):
    archive = _jar(str(jar))
    try:
        raw = archive.read(f'assets/minecraft/textures/block/{name}.png')
    except KeyError:
        return None
    rgba = np.asarray(Image.open(io.BytesIO(raw)).convert('RGBA'), float)
    rgba = rgba[:16] if rgba.shape[0] > rgba.shape[1] else rgba  # animated strips: first frame
    alpha = rgba[..., 3] > 8
    if not alpha.any():
        return None
    return rgba[..., :3][alpha].mean(axis=0) / 255, float(alpha.mean())


def block_colors(jar, keys):
    """Return (top_rgb[N,3], side_rgb[N,3], opacity[N]) in 0..1 for palette keys."""
    top = np.zeros((len(keys), 3)); side = np.zeros((len(keys), 3)); alpha = np.ones(len(keys))
    for i, key in enumerate(keys):
        name = block_name(key).removeprefix('minecraft:')
        if name in ('air', 'cave_air', 'void_air'):
            alpha[i] = 0
            continue
        t_name, s_name = TEXTURE_ALIASES.get(name, (name, name))
        if name.endswith('_slab') or name.endswith('_stairs') or name.endswith('_wall'):
            base = name.rsplit('_', 1)[0]
            for candidate in (base, base + 's', base.replace('brick', 'bricks')):
                if _texture_mean(jar, candidate):
                    t_name = s_name = candidate
                    break
        t = _texture_mean(jar, t_name) or _texture_mean(jar, name + '_top') or ((0.8, 0.0, 0.8), 1)
        s = _texture_mean(jar, s_name) or _texture_mean(jar, name + '_side') or t
        top[i], side[i] = np.asarray(t[0]), np.asarray(s[0])
        if name in TINTED:
            mode = TINTED[name]
            tint = GRASS_TINT if mode in ('top', 'all') else FOLIAGE_TINT
            top[i] = top[i] * tint
            if mode != 'top':
                side[i] = side[i] * tint
        if name == 'water':
            top[i] = top[i] * np.array([63, 118, 228]) / 255 * 1.6
            side[i] = top[i]
            alpha[i] = 0.75
        if name in FIXED_TINT:
            top[i] = top[i] * FIXED_TINT[name]; side[i] = side[i] * FIXED_TINT[name]
        if 'glass' in name and not name.endswith('_pane'):
            alpha[i] = 0.55
        elif name.endswith('_leaves'):
            alpha[i] = 0.92
        for suffix, cover in COVERAGE.items():
            if suffix in name:
                alpha[i] = cover
    return top, side, alpha


def load_window(region_dir, bounds, ylo, yhi):
    """Dense [z, x, y] uint16 palette indices for x0<=x<x1, z0<=z<z1, ylo<=y<yhi."""
    x0, z0, x1, z1 = bounds
    keys = {'minecraft:air': 0}
    vol = np.zeros((z1 - z0, x1 - x0, yhi - ylo), np.uint16)
    for rx in range(x0 >> 9, ((x1 - 1) >> 9) + 1):
        for rz in range(z0 >> 9, ((z1 - 1) >> 9) + 1):
            path = Path(region_dir) / f'r.{rx}.{rz}.mca'
            if not path.exists():
                continue
            for (cx, cz), record in read_region(path).items():
                bx, bz = cx * 16, cz * 16
                if bx + 16 <= x0 or bx >= x1 or bz + 16 <= z0 or bz >= z1:
                    continue
                chunk = ChunkVolume(record['tag'])
                xs = slice(max(bx, x0), min(bx + 16, x1)); zs = slice(max(bz, z0), min(bz + 16, z1))
                for sy in range(ylo >> 4, ((yhi - 1) >> 4) + 1):
                    section = chunk.section(sy)
                    ids = np.fromiter((keys.setdefault(k, len(keys)) for k in section), np.uint16, 4096)
                    ids = ids.reshape(16, 16, 16)
                    lo, hi = max(sy * 16, ylo), min(sy * 16 + 16, yhi)
                    part = ids[lo - sy * 16:hi - sy * 16, zs.start - bz:zs.stop - bz, xs.start - bx:xs.stop - bx]
                    vol[zs.start - z0:zs.stop - z0, xs.start - x0:xs.stop - x0, lo - ylo:hi - ylo] = part.transpose(1, 2, 0)
    names = [None] * len(keys)
    for k, v in keys.items():
        names[v] = k
    return vol, names


def topdown(vol, names, jar, scale=2):
    """Orthographic map: top colour, slope hillshade and a cast-shadow pass."""
    top_rgb, _, alpha = block_colors(jar, names)
    opaque = alpha[vol] > 0.5
    height = vol.shape[2] - 1 - np.argmax(opaque[:, :, ::-1], axis=2)
    height = np.where(opaque.any(axis=2), height, 0).astype(float)
    zz, xx = np.indices(height.shape)
    rgb = top_rgb[vol[zz, xx, height.astype(int)]]
    # Translucent tops (glass) blend over what lies beneath them.
    first = vol.shape[2] - 1 - np.argmax((alpha[vol] > 0)[:, :, ::-1], axis=2)
    glassy = (first > height)
    over = top_rgb[vol[zz, xx, first]]
    a = alpha[vol[zz, xx, first]][..., None]
    rgb = np.where(glassy[..., None], over * a + rgb * (1 - a), rgb)
    gz, gx = np.gradient(height)
    shade = np.clip(1 + (-gx + gz) * 0.08, 0.6, 1.25)
    # Sun from the south-east, 40 degrees: march heights to darken shadowed cells.
    shadow = np.zeros_like(height, bool)
    for step in range(1, 120, 2):
        dz, dx = int(round(step * 0.7)), int(round(-step * 0.7))
        shifted = np.full_like(height, -1)
        zsrc = slice(max(0, dz), height.shape[0] + min(0, dz)); zdst = slice(max(0, -dz), height.shape[0] - max(0, dz))
        xsrc = slice(max(0, -dx), height.shape[1] + min(0, -dx)); xdst = slice(max(0, dx), height.shape[1] - max(0, -dx))
        shifted[zdst, xdst] = height[zsrc, xsrc]
        shadow |= shifted > height + step * 0.84
    img = rgb * shade[..., None] * np.where(shadow, 0.62, 1.0)[..., None]
    img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(img).resize((img.shape[1] * scale, img.shape[0] * scale), Image.NEAREST)


def perspective(vol, names, jar, eye, yaw_deg, pitch_deg=0.0, fov_deg=70, size=(960, 540), max_dist=400):
    """Eye-level voxel ray cast (Amanatides-Woo DDA), flat-shaded with fog and sky.

    ``eye`` is (x, y, z) in window coordinates; yaw 0 looks north (-z), 90 east.
    """
    top_rgb, side_rgb, alpha = block_colors(jar, names)
    shapes = block_shapes(names)
    shaped = (shapes[:, 0] < 0.5) | (shapes[:, 1] > 0) | (shapes[:, 2] < 1)
    alpha = np.where(shaped & (alpha > 0), np.maximum(alpha, 0.97), alpha)
    W, H = size
    yaw, pitch = np.radians(yaw_deg), np.radians(pitch_deg)
    forward = np.array([np.sin(yaw) * np.cos(pitch), np.sin(pitch), -np.cos(yaw) * np.cos(pitch)])
    right = np.array([np.cos(yaw), 0, np.sin(yaw)])
    up = np.cross(right, forward)
    f = 1 / np.tan(np.radians(fov_deg) / 2)
    px, py = np.meshgrid((np.arange(W) + 0.5) / W * 2 - 1, 1 - (np.arange(H) + 0.5) / H * 2)
    px = px * (W / H)
    dirs = forward[None, None] * f + right[None, None] * px[..., None] + up[None, None] * py[..., None]
    dirs = (dirs / np.linalg.norm(dirs, axis=2, keepdims=True)).reshape(-1, 3)
    # Volume is [z, x, y]; rays use (x, y, z).
    shape = np.array([vol.shape[1], vol.shape[2], vol.shape[0]])
    pos = np.array(eye, float)
    cell = np.floor(pos).astype(int)[None].repeat(len(dirs), 0)
    step = np.sign(dirs).astype(int)
    with np.errstate(divide='ignore', invalid='ignore'):
        tdelta = np.abs(1 / dirs)
        nxt = np.where(step > 0, cell + 1 - pos, pos - cell)
        tmax = np.where(dirs != 0, nxt * tdelta, np.inf)
    color = np.zeros((len(dirs), 3)); remaining = np.ones(len(dirs))
    active = np.ones(len(dirs), bool); face = np.full(len(dirs), 1); tcur = np.zeros(len(dirs))
    sun = np.array([0.45, 0.8, 0.4]); sun /= np.linalg.norm(sun)
    normals = {0: np.array([1, 0, 0]), 1: np.array([0, 1, 0]), 2: np.array([0, 0, 1])}
    for _ in range(int(max_dist * 1.8)):
        idx = np.flatnonzero(active)
        if not len(idx):
            break
        axis = np.argmin(tmax[idx], axis=1)
        rows = idx
        tcur[rows] = tmax[rows, axis]
        cell[rows, axis] += step[rows, axis]
        tmax[rows, axis] += tdelta[rows, axis]
        face[rows] = axis
        c = cell[rows]
        inside = np.all((c >= 0) & (c < shape), axis=1)
        out = rows[~inside]
        active[out[(tcur[out] > max_dist) | True]] = False
        rows, c = rows[inside], c[inside]
        if not len(rows):
            continue
        ids = vol[c[:, 2], c[:, 0], c[:, 1]]
        a = alpha[ids]
        hit = a > 0
        if not hit.any():
            active[rows[tcur[rows] > max_dist]] = False
            continue
        r, ids, a = rows[hit], ids[hit], a[hit]
        ax = face[r].copy()
        thin = shaped[ids]
        if thin.any():
            tr = r[thin]
            cc = cell[tr].astype(float)
            w, y0, y1 = shapes[ids[thin], 0], shapes[ids[thin], 1], shapes[ids[thin], 2]
            lo = np.stack([cc[:, 0] + 0.5 - w, cc[:, 1] + y0, cc[:, 2] + 0.5 - w], 1)
            hi = np.stack([cc[:, 0] + 0.5 + w, cc[:, 1] + y1, cc[:, 2] + 0.5 + w], 1)
            d = dirs[tr]
            with np.errstate(divide='ignore', invalid='ignore'):
                t1 = (lo - pos) / d; t2 = (hi - pos) / d
            tn = np.where(np.isnan(t1), -np.inf, np.minimum(t1, t2)); tf = np.where(np.isnan(t2), np.inf, np.maximum(t1, t2))
            tnear = tn.max(axis=1); tfar = tf.min(axis=1)
            ok = (tfar >= tnear) & (tfar > 0)
            keep = np.ones(len(r), bool)
            keep[np.flatnonzero(thin)[~ok]] = False
            ax[np.flatnonzero(thin)[ok]] = np.argmax(tn[ok], axis=1)
            r, ids, a, ax = r[keep], ids[keep], a[keep], ax[keep]
            if not len(r):
                active[rows[tcur[rows] > max_dist]] = False
                continue
        base = np.where((ax == 1)[:, None], top_rgb[ids], side_rgb[ids])
        normal = np.stack([normals[k] for k in range(3)])[ax] * -step[r, ax][:, None]
        light = 0.55 + 0.45 * np.clip(normal @ sun, 0, 1)
        fog = np.clip(tcur[r] / max_dist, 0, 1)[:, None] ** 1.3
        shaded = base * light[:, None] * (1 - fog) + np.array([0.78, 0.84, 0.92]) * fog
        color[r] += remaining[r, None] * a[:, None] * shaded
        remaining[r] *= (1 - a)
        done = remaining[r] < 0.05
        active[r[done]] = False
        active[rows[tcur[rows] > max_dist]] = False
    sky = np.array([0.62, 0.76, 0.93]) * (1 - np.clip(dirs[:, 1:2], 0, 1) * 0.5) + np.array([0.15, 0.1, 0.05]) * 0
    color += remaining[:, None] * sky
    img = (np.clip(color.reshape(H, W, 3), 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(img)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--region-dir', type=Path, required=True)
    p.add_argument('--jar', type=Path, required=True)
    p.add_argument('--bounds', type=int, nargs=4, metavar=('X0', 'Z0', 'X1', 'Z1'), required=True)
    p.add_argument('--y', type=int, nargs=2, default=(56, 470))
    p.add_argument('--out', type=Path, required=True, help='Output PNG prefix')
    p.add_argument('--views', default='top', help='"top" for the map; eye-level views come from --eye')
    p.add_argument('--eye', type=float, nargs=5, action='append', default=[],
                   metavar=('X', 'Y', 'Z', 'YAW', 'PITCH'), help='World-coordinate eye-level view')
    a = p.parse_args()
    x0, z0, x1, z1 = a.bounds
    vol, names = load_window(a.region_dir, a.bounds, *a.y)
    outputs = []
    if 'top' in a.views:
        path = a.out.with_name(a.out.name + '-top.png'); topdown(vol, names, a.jar).save(path); outputs.append(str(path))
    for i, (x, y, z, yaw, pitch) in enumerate(a.eye):
        path = a.out.with_name(a.out.name + f'-eye{i}.png')
        perspective(vol, names, a.jar, (x - x0, y - a.y[0], z - z0), yaw, pitch).save(path); outputs.append(str(path))
    print(json.dumps(outputs))


if __name__ == '__main__':
    main()
