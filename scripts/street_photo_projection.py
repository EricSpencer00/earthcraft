"""Calibrated projection for ordinary street photos and 360 panoramas.

Camera coordinates follow OpenSfM: right, down, forward; normalized image
coordinates use the larger image dimension. This projects observations only.
World registration, depth/occlusion masks and attribution remain admission
requirements of appearance_adapter; this module never edits a world.

Reference: https://opensfm.org/docs/geometry.html
"""
import numpy as np
from scipy.spatial.transform import Rotation


def project_camera(points, calibration, width, height):
    points = np.asarray(points, dtype=float)
    if (points.ndim != 2 or points.shape[1] != 3 or len(points) > 1_000_000 or
            not np.isfinite(points).all() or type(width) is not int or type(height) is not int or
            width < 1 or height < 1 or width * height > 32_000_000):
        raise ValueError('Bounded finite points and image dimensions required')
    model = calibration.get('projection_type')
    x, y, z = points.T
    distance = np.linalg.norm(points, axis=1)
    if model in ('spherical', 'equirectangular'):
        if abs(width - 2 * height) > 2:
            raise ValueError('A panorama must retain its 2:1 projection')
        u = ((.5 + np.arctan2(x, z) / (2 * np.pi)) * width - .5) % width
        v = (.5 + np.arctan2(y, np.hypot(x, z)) / np.pi) * height - .5
        visible = distance > 0
        v = np.clip(v, 0, height - 1)
    elif model == 'perspective':
        f, k1, k2 = (float(calibration.get(k, 0)) for k in ('focal', 'k1', 'k2'))
        if not np.isfinite([f, k1, k2]).all() or f <= 0:
            raise ValueError('Measured focal length and finite distortion required')
        if 'width' in calibration and 'height' in calibration:
            ratio = calibration['width'] / calibration['height']
            if abs(width - ratio * height) > 2:
                raise ValueError('Cropped or rotated photo differs from calibration')
        xn = np.divide(x, z, out=np.zeros_like(x), where=z > 0)
        yn = np.divide(y, z, out=np.zeros_like(y), where=z > 0)
        r2 = xn * xn + yn * yn
        distortion = 1 + k1 * r2 + k2 * r2 ** 2
        u = f * distortion * xn * max(width, height) + (width - 1) / 2
        v = f * distortion * yn * max(width, height) + (height - 1) / 2
        visible = ((z > 0) & (distortion > 0) & (u >= 0) & (u <= width - 1) &
                   (v >= 0) & (v <= height - 1))
    else:
        raise ValueError('Unsupported camera model; do not treat an ordinary photo as a panorama')
    uv = np.column_stack((u, v))
    visible &= np.isfinite(uv).all(axis=1)
    return {'source_uv': uv, 'in_image': visible, 'camera_z': z, 'ray_distance': distance}


def project_shot(points, shot, calibration, width, height):
    """Keep the cluster's pose and datum; registration is a separate operation."""
    rotation = np.asarray(shot['rotation'], dtype=float)
    translation = np.asarray(shot['translation'], dtype=float)
    if rotation.shape != (3,) or translation.shape != (3,) or not np.isfinite([rotation, translation]).all():
        raise ValueError('Finite OpenSfM rotation/translation required')
    camera_points = np.asarray(points, dtype=float) @ Rotation.from_rotvec(rotation).as_matrix().T + translation
    return project_camera(camera_points, calibration, width, height)
