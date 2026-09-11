"""Create a new local Java world with an Earthcraft datapack enabled.

It copies only template level metadata, never template chunks, and refuses to
overwrite a save.  The generated building function remains a deliberate first
run command so opening the world cannot silently alter it.
"""
import argparse
import shutil
import zipfile
from pathlib import Path

import nbtlib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', type=Path, required=True)
    parser.add_argument('--template-level', type=Path, required=True)
    parser.add_argument('--saves', type=Path, required=True)
    parser.add_argument('--world-name', required=True)
    args = parser.parse_args()
    if not args.pack.is_file() or not args.template_level.is_file() or not args.saves.is_dir():
        parser.error('pack, template level.dat, and saves directory must exist')
    with zipfile.ZipFile(args.pack) as archive:
        if 'pack.mcmeta' not in archive.namelist() or 'data/earthcraft/function/build.mcfunction' not in archive.namelist():
            parser.error('pack is not an Earthcraft datapack')
    destination = args.saves / args.world_name
    if destination.exists():
        raise FileExistsError(f'refusing to overwrite existing world: {destination}')
    datapacks = destination / 'datapacks'
    datapacks.mkdir(parents=True)
    shutil.copy2(args.template_level, destination / 'level.dat')
    shutil.copy2(args.pack, datapacks / 'earthcraft-location.zip')
    level = nbtlib.load(destination / 'level.dat')
    data = level['Data']
    data['LevelName'] = nbtlib.String(args.world_name)
    data['GameType'] = nbtlib.Int(1)  # Creative: immediately inspectable.
    data['allowCommands'] = nbtlib.Byte(1)
    packs = data['DataPacks']
    enabled = [str(value) for value in packs['Enabled'] if str(value) != 'file/photo']
    if 'vanilla' not in enabled:
        enabled.insert(0, 'vanilla')
    enabled.append('file/earthcraft-location')
    packs['Enabled'] = nbtlib.List[nbtlib.String]([nbtlib.String(value) for value in enabled])
    packs['Disabled'] = nbtlib.List[nbtlib.String]([])
    level.save(destination / 'level.dat')
    print(destination)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
