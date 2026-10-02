"""Map observed Orthofacade appearance candidates into the inherited city frame.

This is an offline adapter, not a world writer. It retains unknown pixels and
requires ground/wall anchors from the existing world, rather than importing the
upstream photogrammetry height reference or changing LiDAR geometry.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image
from pyproj import Transformer

from metric_frame import validate_frame
from world_snapshot import atomic_json, digest

UPSTREAM = 'https://github.com/louis-e/orthofacade'
REVISION = 'a24e4ca349a1ceecec5c64517e62b6828f4aebac'
CLASSES = {255: 'wall', 192: 'window', 128: 'door'}


def frame_digest(frame):
    return hashlib.sha256(json.dumps(frame, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def project_wall(record, anchor, frame, rgba):
    validate_frame(frame)
    if anchor['world_frame_sha256'] != frame_digest(frame):
        raise ValueError('Anchor belongs to another world frame')
    if not re.fullmatch(r'[0-9a-f]{64}', anchor['source_sha256']):
        raise ValueError('Existing-world anchor source checksum required')
    if not record.get('reachable') or record.get('tier') not in ('A', 'B'):
        raise ValueError('Wall has no usable upstream texture candidate')
    rows, cols = record['rows'], record['cols']
    if type(rows) is not int or type(cols) is not int or not 1 <= rows <= 2032 or not 1 <= cols <= 2048:
        raise ValueError('Bounded metre-resolution grid required')
    rgba = np.asarray(rgba)
    observed = np.asarray(record['observed'])
    classes = np.asarray(record['cls_grid'])
    if (rgba.shape != (rows, cols, 4) or observed.shape != (rows, cols) or
            classes.shape != (rows, cols) or not np.isin(observed, [0, 1]).all() or
            not np.array_equal(rgba[:, :, 3], classes)):
        raise ValueError('PNG classes and explicit observation mask disagree')
    project = Transformer.from_crs(4326, frame['crs'], always_xy=True)
    points = []
    for name in ('a_lonlat', 'b_lonlat'):
        lon, lat = record[name]
        if not all(math.isfinite(v) for v in (lon, lat)) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError('Finite WGS84 wall endpoints required')
        east, north = project.transform(lon, lat)
        points.append([east-frame['west'], frame['north']-north])
    points = np.asarray(points)
    existing = np.asarray([anchor['a_world_xz'], anchor['b_world_xz']], dtype=float)
    if existing.shape != (2, 2) or not np.isfinite(existing).all() or np.linalg.norm(points-existing, axis=1).max() > .75:
        raise ValueError('Source endpoints do not match the existing wall')
    vector = existing[1]-existing[0]
    length = float(np.linalg.norm(vector))
    if not length > 0 or abs(length-record['length_m']) > max(.25, length*.01):
        raise ValueError('Wall length/scale differs')
    start, stop = record['extent']['s_l'], record['extent']['s_r']
    base = anchor['base_world_y']
    if (not all(math.isfinite(v) for v in (start, stop, base)) or
            abs(stop-start-cols) > 1e-6 or base < frame['dimension_min_y'] or
            base+rows >= frame['dimension_min_y']+frame['dimension_height']):
        raise ValueError('Invalid metre grid extent or existing ground anchor')
    row, col = np.nonzero(observed.astype(bool) & np.isin(classes, list(CLASSES)))
    s = start+col+.5
    # Discard upstream cropped/extended pixels outside the matched wall. Never
    # stretch its texture to cover a different footprint.
    inside = (s >= 0) & (s < length)
    row, col, s = row[inside], col[inside], s[inside]
    xz = existing[0]+s[:, None]*(vector/length)
    samples = []
    for index, (r, c) in enumerate(zip(row, col)):
        samples.append({'pixel_rc': [int(r), int(c)],
                        'world_xyz_m': [float(xz[index, 0]), float(base+rows-r-.5), float(xz[index, 1])],
                        'rgb': [int(v) for v in rgba[r, c, :3]],
                        'source_class_candidate': CLASSES[int(classes[r, c])]})
    return {'wall': record['key'], 'osm_id': record['osm_id'],
            'status': 'candidate_registration_pending',
            'samples': samples, 'candidate_samples': len(samples), 'admitted_samples': 0,
            'unknown_or_outside_samples': rows*cols-len(samples),
            'world_frame_sha256': frame_digest(frame),
            'anchor_source_sha256': anchor['source_sha256'],
            'source_panorama_ids': [str(v['pano']) for v in record.get('views', [])],
            'geometry_changed': False, 'independent_registration_verified': False,
            'import_contract': 'Apply only after held-out registration/attribution checks, to existing exposed faces; never create blocks or fill unknown pixels.'}


def adapt(blocks, anchors_path, coverage_path, output):
    blocks, output = Path(blocks).resolve(), Path(output)
    if output.exists():
        raise FileExistsError('Use a new appearance candidate directory')
    frame = json.loads(Path(coverage_path).read_text())['frame']
    validate_frame(frame)
    anchors = json.loads(Path(anchors_path).read_text())['walls']
    output.mkdir(parents=True)
    accepted, rejected = [], []
    for path in sorted(blocks.glob('*.json')):
        record = json.loads(path.read_text())
        key = record.get('key', path.stem)
        try:
            if Path(key).name != key or key in ('', '.', '..') or key != path.stem:
                raise ValueError('Unsafe/mismatched wall identifier')
            if key not in anchors:
                raise ValueError('No existing-world wall/ground anchor')
            png_name = record.get('png', '')
            if Path(png_name).name != png_name or not png_name.endswith('.png'):
                raise ValueError('Unsafe/missing blocks PNG')
            png = blocks/png_name
            if png.resolve().parent != blocks or png.is_symlink():
                raise ValueError('PNG must be a regular local source')
            with Image.open(png) as image:
                if image.mode != 'RGBA' or image.size != (record['cols'], record['rows']):
                    raise ValueError('Expected exact upstream RGBA metre grid')
                result = project_wall(record, anchors[key], frame, np.asarray(image))
            result['source_record_sha256'] = digest(path)
            result['source_png_sha256'] = digest(png)
            atomic_json(output/(key+'.json'), result)
            accepted.append({'wall': key, 'candidate_samples': result['candidate_samples']})
        except (ValueError, KeyError, OSError) as error:
            rejected.append({'wall': key, 'reason': str(error)})
    report = {'schema': 'earthcraft.orthofacade-candidates.v1', 'status': 'candidate_registration_pending',
              'upstream': UPSTREAM, 'upstream_revision': REVISION,
              'world_frame': frame, 'world_frame_sha256': frame_digest(frame),
              'anchor_manifest_sha256': digest(anchors_path),
              'candidate_walls': accepted, 'rejected_walls': rejected,
              'admitted_samples': 0, 'world_modified': False}
    atomic_json(output/'manifest.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blocks', type=Path, required=True)
    parser.add_argument('--anchors', type=Path, required=True)
    parser.add_argument('--coverage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = adapt(args.blocks, args.anchors, args.coverage, args.output)
    print(json.dumps({'candidate_walls': len(result['candidate_walls']), 'rejected_walls': len(result['rejected_walls']), 'admitted_samples': 0}))
