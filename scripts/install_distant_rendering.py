"""Add pinned LOD/client renderers to the existing Earthcraft Fabric profile.

Does not modify saves, projection metadata, launcher profiles, or options.txt.
Python 3.11+. Downloads are publisher-hash verified before any installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MINECRAFT = "1.21.10"
PROFILE = "earthcraft-traversal"
ASSETS = (
    {
        "id": "distanthorizons", "version": "3.3.3", "size": 28655057,
        "file": "DistantHorizons-3.3.3-1.21.10-fabric-neoforge.jar",
        "url": "https://cdn.modrinth.com/data/uCdwusMi/versions/dXJr1PPw/DistantHorizons-3.3.3-1.21.10-fabric-neoforge.jar",
        "sha512": "bc8f817501a6534e6776e60d33435e71801766d3b5f773f42cdbe2be73a6eec00f3542f48a63821a53bc5ea23338b5a0c2b176296ab189a481a9c2c8ec87c18e",
    },
    {
        "id": "sodium", "version": "0.7.3+mc1.21.10", "size": 1676903,
        "file": "sodium-fabric-0.7.3+mc1.21.10.jar",
        "url": "https://cdn.modrinth.com/data/AANobbMI/versions/sFfidWgd/sodium-fabric-0.7.3%2Bmc1.21.10.jar",
        "sha512": "1cccdc75d972f5c176a488dcc84cce7320b608a2d105412f2847245affbf5aa22b1995eda392132453fa6e1a9154ac99a87acaf8d7989b9f1a23ac1059a93daf",
    },
)


def digest(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def mod_metadata(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("fabric.mod.json"))


def validate_config(raw: bytes) -> dict:
    config = tomllib.loads(raw.decode())
    generation = config["common"]["worldGenerator"]
    if (config.get("_version") != 5
            or generation.get("generatorPlan") != "CHUNKS_ONLY"
            or generation.get("chunkGeneratorMode") != "PRE_EXISTING_ONLY"):
        raise ValueError("Earthcraft requires DH v5 config and existing-chunks-only generation")
    updater = config["client"]["advanced"]["autoUpdater"]
    if updater.get("enableAutoUpdater") is not False or updater.get("enableSilentUpdates") is not False:
        raise ValueError("Automatic updates would invalidate the pinned config schema")
    return config


def install(game_dir: Path, minecraft_dir: Path) -> dict:
    game_dir = game_dir.resolve(strict=True)
    profiles = json.loads((minecraft_dir / "launcher_profiles.json").read_text())
    profile = profiles["profiles"][PROFILE]
    if Path(profile["gameDir"]).resolve() != game_dir:
        raise ValueError("The existing launcher profile points to a different game directory")
    version_id = profile["lastVersionId"]
    version = json.loads((minecraft_dir / "versions" / version_id / f"{version_id}.json").read_text())
    if version.get("inheritsFrom") != MINECRAFT:
        raise ValueError("These pinned mods require Minecraft 1.21.10")
    loaders = [lib["name"].split(":")[-1] for lib in version.get("libraries", [])
               if lib.get("name", "").startswith("net.fabricmc:fabric-loader:")]
    if len(loaders) != 1:
        raise ValueError("The existing profile must use Fabric")
    if tuple(map(int, loaders[0].split("."))) < (0, 17, 3):
        raise ValueError("Distant Horizons 3.3.3 requires Fabric Loader 0.17.3 or later")
    world = game_dir / "saves" / "Earthcraft"
    if not (world / "level.dat").is_file():
        raise FileNotFoundError("Existing Earthcraft save missing; no new world will be created")

    template = (ROOT / "configs" / "distant-horizons.toml").read_bytes()
    config = validate_config(template)
    config_path = game_dir / "config" / "DistantHorizons.toml"
    if config_path.exists():
        # DH expands the config with defaults on launch. Accept an existing file
        # only when every managed setting already matches; preserve other settings.
        actual = validate_config(config_path.read_bytes())
        def contains(actual_value, expected):
            return (all(key in actual_value and contains(actual_value[key], value)
                        for key, value in expected.items()) if isinstance(expected, dict)
                    else actual_value == expected)
        if not contains(actual, config):
            raise ValueError("Existing DH settings differ; refusing to overwrite user configuration")

    installed = {}
    for path in sorted((game_dir / "mods").glob("*.jar")):
        meta = mod_metadata(path)
        if meta["id"] in installed:
            raise ValueError(f"Duplicate installed mod id: {meta['id']}")
        installed[meta["id"]] = path

    # Preflight the entire install before adding either mod.
    ready = []
    cache = ROOT / "vendor" / "distant-rendering"
    for asset in ASSETS:
        source = cache / asset["file"]
        if source.exists():
            raw = source.read_bytes()
        else:
            request = urllib.request.Request(asset["url"], headers={"User-Agent": "Earthcraft/0.1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read(asset["size"] + 1)
            if len(raw) != asset["size"] or digest(raw) != asset["sha512"]:
                raise ValueError(f"Publisher checksum/size mismatch: {asset['id']}")
            atomic_write(source, raw)
        if len(raw) != asset["size"] or digest(raw) != asset["sha512"]:
            raise ValueError(f"Publisher checksum/size mismatch: {asset['id']}")
        meta = mod_metadata(source)
        if meta["id"] != asset["id"] or meta["version"] != asset["version"]:
            raise ValueError("Unexpected Fabric mod identity")
        conflicts = set(meta.get("breaks", {})).intersection(installed)
        if conflicts:
            # Conservatively reject even version-bounded conflicts rather than
            # removing/replacing existing mods or implementing Fabric's resolver.
            raise ValueError(f"Potential incompatible installed mods: {sorted(conflicts)}")
        existing = installed.get(asset["id"])
        destination = existing or game_dir / "mods" / asset["file"]
        if existing and digest(existing.read_bytes()) != asset["sha512"]:
            raise ValueError(f"A different {asset['id']} is already installed")
        if not existing and destination.exists():
            raise FileExistsError(destination)
        ready.append((asset, raw, destination))

    if not config_path.exists():
        atomic_write(config_path, template)
    for asset, raw, destination in ready:
        if not destination.exists():
            atomic_write(destination, raw)
        if digest(destination.read_bytes()) != asset["sha512"]:
            raise ValueError("Installed mod readback failed")
    report = {
        "profile": PROFILE, "game_dir": str(game_dir), "world": str(world),
        "minecraft": MINECRAFT, "mods": [asset for asset, _, _ in ready],
        "config_path": str(config_path), "config_sha512": digest(config_path.read_bytes()),
        "lod_radius_chunks": 2048, "lod_radius_blocks": 32768,
        "existing_chunks_only": True, "save_writes": 0,
        "launcher_and_options_writes": 0, "runtime_verified": False,
        "activation": "Restart Minecraft; Fabric cannot hot-load these mods",
    }
    atomic_write(ROOT / "runs" / "distant-rendering-install.json",
                 (json.dumps(report, indent=2) + "\n").encode())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, default=ROOT / "runtime" / "traversal")
    parser.add_argument("--minecraft-dir", type=Path,
                        default=Path.home() / "Library" / "Application Support" / "minecraft")
    args = parser.parse_args()
    report = install(args.game_dir, args.minecraft_dir)
    print(json.dumps({key: report[key] for key in (
        "profile", "minecraft", "lod_radius_chunks", "existing_chunks_only", "activation")}, indent=2))


if __name__ == "__main__":
    main()
