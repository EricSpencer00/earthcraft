"""Retain calibrated photo observations for a later LiDAR registration pass.

Run on a compute host. Output stays in the reconstruction's local frame and
cannot be imported as world coordinates. In-image points are not proof of
visibility, static facade ownership, or independent alignment.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from street_photo_projection import project_shot


def source_shot(reconstructions, capture_timestamp_ms):
    if not isinstance(reconstructions, list):
        reconstructions = [reconstructions]
    matches = [(record, shot) for record in reconstructions
               for shot in record['shots'].values()
               if 'capture_time' in shot and round(shot['capture_time'] * 1000) == capture_timestamp_ms]
    if len(matches) != 1:
        raise ValueError('Exactly one calibrated source shot must match the capture timestamp')
    return matches[0]


def build(cluster_path, image_path, metadata_path, output):
    cluster_path, image_path, metadata_path, output = map(Path, (cluster_path, image_path, metadata_path, output))
    if output.exists():
        raise FileExistsError('Keep each source projection in a new directory')
    raw = cluster_path.read_bytes()
    metadata = json.loads(metadata_path.read_text())
    record, shot = source_shot(json.loads(raw), metadata['captured_at'])
    calibration = record['cameras'][shot['camera']]
    with Image.open(image_path) as image:
        if image.width * image.height > 32_000_000:
            raise ValueError('Bounded source photo required')
        rgb = np.asarray(image.convert('RGB'))
    height, width = rgb.shape[:2]
    points = np.asarray([p['coordinates'] for p in record['points'].values()], dtype=float).reshape(-1, 3)
    projection = project_shot(points, shot, calibration, width, height)
    mask = projection['in_image']
    uv = projection['source_uv'][mask]
    pixels = np.rint(uv).astype(int)
    pixels[:, 0] %= width
    pixels[:, 1] = np.clip(pixels[:, 1], 0, height - 1)
    colors = rgb[pixels[:, 1], pixels[:, 0]]
    output.mkdir(parents=True)
    candidate = output / 'observations.npz'
    np.savez_compressed(candidate, cluster_xyz=points[mask], rgb=colors, source_uv=uv)
    proof = {
        'schema': 'earthcraft.street-photo-candidates.v1',
        'source_image_id': metadata['id'], 'capture_timestamp_ms': metadata['captured_at'],
        'camera_model': calibration['projection_type'], 'reconstruction_points': len(points),
        'projected_color_candidates': int(mask.sum()),
        'photo_sha256': hashlib.sha256(image_path.read_bytes()).hexdigest(),
        'cluster_sha256': hashlib.sha256(raw).hexdigest(),
        'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
        'coordinate_frame': 'unregistered_reconstruction',
        'admitted_world_samples': 0, 'world_modified': False,
        'pending': ['independent_lidar_registration', 'static_surface_ownership',
                    'depth_occlusion_checks', 'source_attribution'],
    }
    (output / 'proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cluster', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.cluster, args.image, args.metadata, args.output)))
