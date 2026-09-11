"""Freeze geometry/visibility; measure only colour aggregation loss on held-out photo."""
import json, runpy, time
from pathlib import Path
import cv2
import numpy as np

start = time.monotonic()
root = Path(__file__).resolve().parents[1]
s = runpy.run_path(str(root / 'scripts/photo_pair_probe.py'))
out = s['OUT']
data = np.load(out / 'dense-multiview.npz')
xyz, rgb = data['xyz'], data['rgb'].astype(float)
name, R, t, K = s['poses'][2]
photo = cv2.imread(str(s['ROOT'] / 'images' / name))
scale = 1600 / photo.shape[1]
photo = cv2.resize(photo, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
K = K.copy(); K[:2] *= scale
cam = (R @ xyz.T + t[:, None]).T
projected = cam @ K.T
uv = np.rint(projected[:, :2] / projected[:, 2:]).astype(int)
inside = (cam[:, 2] > 0) & (uv[:, 0] >= 0) & (uv[:, 0] < photo.shape[1]) & (uv[:, 1] >= 0) & (uv[:, 1] < photo.shape[0])
ids = np.flatnonzero(inside)
order = np.argsort(cam[ids, 2])
pixels = uv[ids, 1] * photo.shape[1] + uv[ids, 0]
_, first = np.unique(pixels[order], return_index=True)
visible = ids[order[first]]
truth = photo[uv[visible, 1], uv[visible, 0]][:, ::-1].astype(float)
coords = xyz[:, [1, 2, 0]] * [1, 1, -1]
coords -= np.floor(coords.min(axis=0))
variants = [('Original photo samples', rgb)]
for spacing in (1, 1/16):
    _, inverse, counts = np.unique(np.floor(coords / spacing).astype(int), axis=0, return_inverse=True, return_counts=True)
    means = np.stack([np.bincount(inverse, weights=rgb[:, c]) / counts for c in range(3)], axis=1)
    variants.append((f'{spacing:g} unit colour cells', means[inverse]))
panels = [photo]
metrics = []
for label, colors in variants:
    render = np.zeros_like(photo)
    render[uv[visible, 1], uv[visible, 0]] = np.clip(colors[visible][:, ::-1], 0, 255).astype('uint8')
    panels.append(render)
    error = np.abs(colors[visible] - truth).mean(axis=1)
    metrics.append({'variant': label, 'mean_rgb_error_255': float(error.mean()), 'median_rgb_error_255': float(np.median(error)), 'p90_rgb_error_255': float(np.percentile(error, 90))})
thumbs = []
for label, panel in zip(['Held-out photograph'] + [v[0] for v in variants], panels):
    small = cv2.resize(panel, (800, round(panel.shape[0]/2)))
    small = cv2.copyMakeBorder(small, 40, 0, 0, 0, cv2.BORDER_CONSTANT, value=(25,25,25))
    cv2.putText(small, label, (15, 27), cv2.FONT_HERSHEY_SIMPLEX, .65, (255,255,255), 1, cv2.LINE_AA)
    thumbs.append(small)
cv2.imwrite(str(out / 'appearance-ablation.jpg'), np.vstack([np.hstack(thumbs[:2]), np.hstack(thumbs[2:])]))
report = {'status': 'diagnostic_not_minecraft_export', 'heldout_image': name, 'visible_pixels': len(visible), 'coverage': len(visible)/(photo.shape[0]*photo.shape[1]), 'geometry_and_visibility_identical': True, 'metrics': metrics, 'elapsed_seconds': time.monotonic()-start, 'limitations': ['Colour-cell experiment only; finer cells are not a implemented resource pack', 'No geometry quantization or Minecraft lighting simulated', 'Unobserved occlusion, exposure and correlated observations remain', 'Dataset metric units and gravity not independently verified']}
(out / 'appearance-ablation.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
