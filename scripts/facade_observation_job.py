"""Replayable, local-only cross-photo admission gate for one facade observation.

This deliberately does not recover a camera pose, texture an unobserved wall, or
modify a Minecraft save.  It verifies frozen Commons inputs, measures whether a
distinct photograph has enough geometrically distributed agreement with a
baseline observation to become a *candidate* for a later metric registration.
Until that gate passes, admitted texture coverage remains zero.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SOURCE_BATCHES = {
    "frozen": ROOT / "runs/water-tower-facade-photos",
    "overlap": ROOT / "runs/water-tower-photo-overlap-001",
}
# This is intentionally a different source and capture date from the frozen
# south view.  It is the closest dated cached Commons candidate, not validation.
PAIR = ("south-a", "tower-2022")
GATE = {
    "mutual_ratio_matches": 30,
    "fundamental_inliers": 20,
    "homography_inliers": 20,
    "minimum_hull_fraction_each_image": 0.02,
    "median_homography_reprojection_px": 2.0,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def packed_mask(mask: np.ndarray) -> str:
    """Encode a 16×16 row-major support mask without turning gaps opaque."""
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != (16, 16):
        raise ValueError(f"Expected 16x16 visibility mask, got {mask.shape}")
    return base64.b64encode(np.packbits(mask.ravel(), bitorder="big").tobytes()).decode("ascii")


def frozen_sparse_patch() -> dict[str, Any]:
    """Export the existing immutable west layer as importer-ready sparse facts.

    The payload points into the immutable project fixture rather than copying its
    textures or changing its world.  Each record contains its exact target face,
    texture checksum and its transparent/observed texel mask.
    """
    world = ROOT / "worlds/Earthcraft-Water-Tower-Photo-Skin-v3"
    export = ROOT / "runs/Earthcraft-Water-Tower-Photo-Skin-v3/export.json"
    samples_path = ROOT / "runs/Earthcraft-Water-Tower-Photo-Skin-v3/texture-samples.npz"
    skin = json.loads((world / "photo-skin.json").read_text())
    frame = json.loads((world / "earthcraft.json").read_text())["source"]
    with zipfile.ZipFile(world / "resources.zip") as archive:
        panels = json.loads(archive.read("earthcraft-skin-manifest.json"))
    samples = np.load(samples_path, allow_pickle=False)
    cells, faces, rgba = samples["cells"], samples["faces"], samples["rgba"]
    if len(panels) != len(cells) or len(cells) != len(faces) or len(faces) != len(rgba):
        raise ValueError("Frozen panel manifest and texture samples disagree")
    records = []
    for index, panel in enumerate(panels):
        cell = np.asarray(panel["cell"], dtype=int)
        if panel["id"] != index or not np.array_equal(cell, cells[index]) or panel["face"] != str(faces[index]):
            raise ValueError("Frozen panel ordering or face coordinates disagree")
        mask = rgba[index, :, :, 3] == 255
        if int(mask.sum()) != panel["observed_texels"]:
            raise ValueError("Frozen panel opacity does not match manifest")
        records.append({
            "panel_id": f"water-tower-west-{index:03d}",
            "source_panel_index": index,
            "minecraft_cell_xyz": cell.tolist(),
            "face": panel["face"],
            "texture_resource": f"resources.zip!assets/earthcraft_skin/textures/block/face_{index}.png",
            "texture_png_sha256": panel["texture_sha256"],
            "texel_grid": {"width": 16, "height": 16, "row_major": True,
                           "observed_alpha_equals_255_mask_base64": packed_mask(mask)},
            "observed_texels": int(mask.sum()),
        })
    return {
        "schema": "earthcraft.facade-sparse-patches.v1",
        "status": "experimental_source_backed_sparse_layer_only",
        "fixture_world": str(world.relative_to(ROOT)),
        "fixture_world_photo_skin_sha256": sha256(world / "photo-skin.json"),
        "fixture_export_sha256": sha256(export),
        "coordinate_frame": {key: frame[key] for key in ("crs", "projection_origin", "west", "north", "axes", "metres_per_block")},
        "surface_offset_m": skin["surface_offset_m"],
        "collision_or_geometry_changed": False,
        "registration_status": {
            "state": "experimental_camera_alignment_not_independently_validated",
            "independent_photo_validation": skin["independent_photo_validation"],
            "independent_geographic_accuracy_verified": False,
        },
        "provenance": skin["photo"],
        "source_point_sha256": skin["point_source_sha256"],
        "panel_count": len(records),
        "observed_texel_count": sum(record["observed_texels"] for record in records),
        "panels": records,
        "importer_contract": [
            "Read only the named texture resource and apply alpha only where the packed mask bit is 1.",
            "Place the panel 0.002 m outside the named exposed voxel face; do not alter the voxel or collision.",
            "Retain transparent texels as unknown/underlying material and do not extrapolate texture, colour, or windows.",
            "Treat this as experimental appearance mapping, never as an independently verified camera or facade-accuracy result.",
        ],
    }


def load_assets() -> dict[str, dict[str, Any]]:
    """Load manifest records and reject altered originals or unsafe paths."""
    assets: dict[str, dict[str, Any]] = {}
    for batch_name, directory in SOURCE_BATCHES.items():
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest.get("status") != "originals_verified_registration_pending":
            raise ValueError(f"Unverified source batch: {directory}")
        if manifest.get("llm_used") is not False:
            raise ValueError("Facade observation sources must not use LLM output")
        for asset in manifest["assets"]:
            name, filename = asset["id"], asset["file"]
            if name in assets or Path(filename).name != filename:
                raise ValueError(f"Duplicate or unsafe source asset: {name}")
            path = directory / filename
            if not path.is_file() or sha256(path) != asset["sha256"]:
                raise ValueError(f"Frozen source checksum mismatch: {name}")
            assets[name] = {**asset, "batch": batch_name, "path": path}
    return assets


def image_rgb(path: Path, long_edge: int = 1600) -> np.ndarray:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    image.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    return np.asarray(image)


def hull_fraction(points: np.ndarray, shape: tuple[int, int]) -> float:
    if len(points) < 3:
        return 0.0
    area = float(cv2.contourArea(cv2.convexHull(points.astype(np.float32))))
    return area / float(shape[0] * shape[1])


def mutual_ratio_matches(descriptors_a: np.ndarray, descriptors_b: np.ndarray) -> list[cv2.DMatch]:
    """Use mutual 0.70 ratio matches; deterministic brute-force matching only."""
    if descriptors_a is None or descriptors_b is None:
        return []
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    forward = matcher.knnMatch(descriptors_a, descriptors_b, k=2)
    reverse = matcher.knnMatch(descriptors_b, descriptors_a, k=2)
    good_f = {m.queryIdx: m for pair in forward if len(pair) == 2
              for m, n in [pair] if m.distance < .70 * n.distance}
    good_r = {m.queryIdx: m for pair in reverse if len(pair) == 2
              for m, n in [pair] if m.distance < .70 * n.distance}
    return [m for query, m in sorted(good_f.items()) if good_r.get(m.trainIdx)
            and good_r[m.trainIdx].trainIdx == query]


def match_images(first: np.ndarray, second: np.ndarray) -> dict[str, Any]:
    """Measure independent classical-CV agreement without admitting a pose."""
    cv2.setNumThreads(4)
    cv2.setRNGSeed(0)
    gray_a = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(
        cv2.cvtColor(first, cv2.COLOR_RGB2GRAY))
    gray_b = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(
        cv2.cvtColor(second, cv2.COLOR_RGB2GRAY))
    sift = cv2.SIFT_create(nfeatures=8000, contrastThreshold=.02)
    key_a, desc_a = sift.detectAndCompute(gray_a, None)
    key_b, desc_b = sift.detectAndCompute(gray_b, None)
    matches = mutual_ratio_matches(desc_a, desc_b)
    points_a = np.float32([key_a[m.queryIdx].pt for m in matches]) if matches else np.empty((0, 2), np.float32)
    points_b = np.float32([key_b[m.trainIdx].pt for m in matches]) if matches else np.empty((0, 2), np.float32)
    fundamental = np.zeros(len(matches), bool)
    homography = np.zeros(len(matches), bool)
    reprojection: float | None = None
    if len(matches) >= 8:
        cv2.setRNGSeed(0)
        _, mask = cv2.findFundamentalMat(points_a, points_b, cv2.FM_RANSAC, 1.5, .999)
        if mask is not None:
            fundamental = mask.ravel().astype(bool)
    if len(matches) >= 4:
        cv2.setRNGSeed(0)
        matrix, mask = cv2.findHomography(points_a, points_b, cv2.RANSAC, 2.0, maxIters=5000, confidence=.999)
        if matrix is not None and mask is not None:
            homography = mask.ravel().astype(bool)
            projected = cv2.perspectiveTransform(points_a[homography][None], matrix)[0]
            reprojection = float(np.median(np.linalg.norm(projected - points_b[homography], axis=1)))
    hulls = [hull_fraction(points_a[homography], first.shape[:2]),
             hull_fraction(points_b[homography], second.shape[:2])]
    return {
        "detector": "SIFT+CLAHE / mutual Lowe 0.70 / OpenCV RANSAC",
        "source_image_size": [int(first.shape[1]), int(first.shape[0])],
        "candidate_image_size": [int(second.shape[1]), int(second.shape[0])],
        "keypoints": [len(key_a), len(key_b)],
        "mutual_ratio_matches": len(matches),
        "fundamental_inliers": int(fundamental.sum()),
        "homography_inliers": int(homography.sum()),
        "homography_hull_fractions": hulls,
        "median_homography_reprojection_px": reprojection,
    }


def admitted(metrics: dict[str, Any]) -> bool:
    return (metrics["mutual_ratio_matches"] >= GATE["mutual_ratio_matches"] and
            metrics["fundamental_inliers"] >= GATE["fundamental_inliers"] and
            metrics["homography_inliers"] >= GATE["homography_inliers"] and
            min(metrics["homography_hull_fractions"]) >= GATE["minimum_hull_fraction_each_image"] and
            metrics["median_homography_reprojection_px"] is not None and
            metrics["median_homography_reprojection_px"] <= GATE["median_homography_reprojection_px"])


def report() -> dict[str, Any]:
    assets = load_assets()
    first, second = (assets[name] for name in PAIR)
    if first["sha256"] == second["sha256"] or first["author_html"] == second["author_html"]:
        raise ValueError("Candidate must be a distinct source observation")
    started = time.monotonic()
    metrics = match_images(image_rgb(first["path"]), image_rgb(second["path"]))
    passes = admitted(metrics)
    source_records = []
    for asset in (first, second):
        source_records.append({key: asset.get(key) for key in (
            "id", "title", "description_url", "sha256", "bytes", "author_html", "license",
            "license_url", "capture_date_source", "source_camera_latitude", "source_camera_longitude", "batch")})
    result = {
        "schema": "earthcraft.facade-observation-job.v1",
        "status": "metric_registration_candidate" if passes else "rejected_before_metric_registration",
        "building": {"name": "Chicago Water Tower", "id": "Cook County 2022 OBJECTID 833197"},
        "pair": list(PAIR),
        "source_records": source_records,
        "input_manifest_sha256": {name: sha256(directory / "manifest.json") for name, directory in SOURCE_BATCHES.items()},
        "metrics": metrics,
        "fixed_gate": GATE,
        "distinct_source": True,
        "admitted_observed_facade_coverage": {"panels": 0, "opaque_texels": 0, "reason":
            "No texture or world output is admitted until the cross-view gate and a separate metric camera registration both pass."},
        "independent_geographic_accuracy_verified": False,
        "camera_registration_verified": False,
        "world_modified": False,
        "llm_used": False,
        "local_runtime": {"opencv": cv2.__version__, "cpu_threads": 4,
                          "elapsed_seconds": round(time.monotonic() - started, 6)},
        "limitations": [
            "A passing image-space gate would be only a candidate for metric camera registration, not a pose or facade-accuracy certificate.",
            "No unobserved pixels, windows, colours, or geometry are filled.",
            "The 2013 and 2022 capture dates differ; agreement cannot prove unchanged appearance.",
            "This job retains unknown coverage rather than extending the 193-panel experimental west skin.",
        ],
    }
    result["replay_fingerprint_sha256"] = hashlib.sha256(canonical(
        {k: v for k, v in result.items() if k not in {"local_runtime", "replay_fingerprint_sha256"}})).hexdigest()
    return result


def run(output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"Never overwrite a facade run: {output}")
    if output.name != str(output.name) or output.parent.name != "runs":
        raise ValueError("Output must be a direct new directory under runs/")
    if ROOT.stat().st_dev != output.parent.stat().st_dev:
        raise ValueError("Facade artifacts must remain on the project volume")
    output.mkdir()
    value = report()
    patch = frozen_sparse_patch()
    (output / "frozen-west-sparse-patches.json").write_text(json.dumps(patch, indent=2) + "\n")
    value["frozen_sparse_patch"] = {
        "path": "frozen-west-sparse-patches.json",
        "sha256": sha256(output / "frozen-west-sparse-patches.json"),
        "panels": patch["panel_count"], "opaque_texels": patch["observed_texel_count"],
    }
    value["replay_fingerprint_sha256"] = hashlib.sha256(canonical(
        {k: v for k, v in value.items() if k not in {"local_runtime", "replay_fingerprint_sha256"}})).hexdigest()
    (output / "report.json").write_text(json.dumps(value, indent=2) + "\n")
    return value


def replay(existing: Path) -> dict[str, Any]:
    saved = json.loads((existing / "report.json").read_text())
    fresh = report()
    patch = frozen_sparse_patch()
    patch_path = existing / "frozen-west-sparse-patches.json"
    if not patch_path.is_file():
        raise ValueError("Sparse patch artifact missing from replay run")
    expected_patch = json.dumps(patch, indent=2) + "\n"
    if patch_path.read_text() != expected_patch:
        raise ValueError("Frozen sparse patch differs from replay output")
    fresh["frozen_sparse_patch"] = {
        "path": "frozen-west-sparse-patches.json",
        "sha256": sha256(patch_path),
        "panels": patch["panel_count"], "opaque_texels": patch["observed_texel_count"],
    }
    fresh["replay_fingerprint_sha256"] = hashlib.sha256(canonical(
        {k: v for k, v in fresh.items() if k not in {"local_runtime", "replay_fingerprint_sha256"}})).hexdigest()
    if saved["replay_fingerprint_sha256"] != fresh["replay_fingerprint_sha256"]:
        raise ValueError("Frozen-input replay fingerprint differs")
    return {"status": "replay_passed", "run": str(existing), "replay_fingerprint_sha256": fresh["replay_fingerprint_sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    if bool(args.output) == bool(args.replay):
        parser.error("Provide exactly one of --output or --replay")
    print(json.dumps(run(args.output) if args.output else replay(args.replay), indent=2))
