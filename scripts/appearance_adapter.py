"""Deterministic temporal selection and calibrated surface painting; no inference.

Discovery, pose solving, semantic masks and independent validation are upstream
inputs, not facts this sampler invents. All geometry is in one metre-valued frame.
"""
import calendar
from datetime import date, datetime, timezone
import re

import numpy as np


def capture_interval(value):
    """Preserve year/month uncertainty; never substitute retrieval/upload dates."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        start, _ = capture_interval(value[0])
        _, end = capture_interval(value[1])
        if start > end:
            raise ValueError('Reversed acquisition interval')
        return start, end
    if not isinstance(value, str):
        raise ValueError('Explicit acquisition date or interval required')
    if re.fullmatch(r'\d{4}', value):
        return date(int(value), 1, 1), date(int(value), 12, 31)
    if re.fullmatch(r'\d{4}-\d{2}', value):
        year, month = map(int, value.split('-'))
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
        else:
            timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if timestamp.tzinfo:
                timestamp = timestamp.astimezone(timezone.utc)
            day = timestamp.date()
    except (ValueError, TypeError) as exc:
        raise ValueError('Unknown/non-ISO acquisition date; normalize from source metadata first') from exc
    return day, day


def temporal_relation(source, target):
    a, b = capture_interval(source)
    c, d = capture_interval(target)
    return {
        'minimum_gap_days': max(0, (a-d).days, (c-b).days),
        'maximum_gap_days': max(abs((a-d).days), abs((b-c).days)),
        'source_uncertainty_days': (b-a).days,
        'target_uncertainty_days': (d-c).days,
        'intervals_overlap': a <= d and c <= b,
        'same_exact_day': a == b == c == d,
    }


def rank_capture(source, target):
    relation = temporal_relation(source, target)
    # A broad, overlapping date interval must not outrank a known near-date image
    # just because it has a possible zero-day gap. Rank worst-case date distance.
    return relation['maximum_gap_days'], relation['source_uncertainty_days']


def paint_surface(xyz, normals, rgb, depth, camera, valid_pixels, *,
                  depth_tolerance_m=.15, min_facing_cosine=.25):
    """Sample visible calibrated pixels onto surfaces without moving any point.

    camera contains K, R, t, with camera_xyz = world_xyz @ R.T + t;
    +Z forward, +X image-right, +Y image-down. Images must be undistorted.
    depth is camera Z in metres, NOT Euclidean ray distance. Missing depth is
    rejected, not treated as unoccluded. Caller supplies a static/semantic mask.
    Alpha=0 means unknown; black is a valid observed colour. No inpainting.
    """
    p = np.asarray(xyz, dtype=float)
    normal = np.asarray(normals, dtype=float)
    rgb = np.asarray(rgb)
    depth = np.asarray(depth, dtype=float)
    mask = np.asarray(valid_pixels)
    k, r, t = (np.asarray(camera[key], dtype=float) for key in ('K', 'R', 't'))
    if (p.ndim != 2 or p.shape[1] != 3 or len(p) > 1_000_000 or normal.shape != p.shape
            or not np.isfinite(p).all() or not np.isfinite(normal).all()):
        raise ValueError('Finite bounded Nx3 surfaces and normals required')
    if (rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3
            or not 0 < rgb.shape[0]*rgb.shape[1] <= 16_000_000
            or depth.shape != rgb.shape[:2] or mask.shape != depth.shape or mask.dtype != bool):
        raise ValueError('Bounded U8 RGB, metre depth and Boolean mask of equal size required')
    if (k.shape != (3,3) or r.shape != (3,3) or t.shape != (3,)
            or not all(np.isfinite(v).all() for v in (k,r,t))
            or not np.allclose(r @ r.T, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(r), 1, atol=1e-6)
            or not np.allclose(k[2], [0,0,1]) or k[0,0] <= 0 or k[1,1] <= 0):
        raise ValueError('Calibrated right-handed pinhole camera required')
    if (not np.isfinite(depth_tolerance_m) or not 0 <= depth_tolerance_m <= 1
            or not np.isfinite(min_facing_cosine) or not 0 <= min_facing_cosine <= 1):
        raise ValueError('Invalid depth/facing thresholds')
    camera_points = p @ r.T + t
    projected = camera_points @ k.T
    uv = np.divide(projected[:,:2], projected[:,2,None],
                   out=np.full((len(p),2), np.nan), where=projected[:,2,None] > 0)
    h, w = depth.shape
    in_image = (np.isfinite(uv).all(axis=1) & (uv[:,0] >= 0) & (uv[:,0] <= w-1)
                & (uv[:,1] >= 0) & (uv[:,1] <= h-1))
    pixels = np.rint(np.where(in_image[:,None], uv, 0)).astype(int)
    x, y = pixels.T
    sample_depth = depth[y,x]
    center = -r.T @ t
    view = center-p
    length = np.linalg.norm(view, axis=1)*np.linalg.norm(normal, axis=1)
    facing = np.divide(np.sum(normal*view, axis=1), length,
                       out=np.full(len(p), -1.), where=length > 0)
    visible = (in_image & mask[y,x] & np.isfinite(sample_depth) & (sample_depth > 0)
               & (np.abs(camera_points[:,2]-sample_depth) <= depth_tolerance_m)
               & (facing >= min_facing_cosine))
    rgba = np.zeros((len(p),4), np.uint8)
    rgba[visible,:3] = rgb[y[visible],x[visible]]
    rgba[visible,3] = 255
    return {'rgba': rgba, 'visible': visible, 'source_uv': uv,
            'facing_cosine': facing, 'depth_residual_m': camera_points[:,2]-sample_depth}
