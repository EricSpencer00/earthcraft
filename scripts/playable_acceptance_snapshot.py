"""Freeze a bounded excerpt of the closed installed save for remote acceptance.

Preserves coordinates, player bytes, existing packs and installed scan receipts.
The source save is locked briefly, never modified. This is an excerpt rather
than a full backup or evidence that the rest of the world is playable.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import time

from pyproj import Transformer


ANCHORS = {'downtown': [-87.62443, 41.8972], 'elmhurst': [-87.9403418, 41.8994745]}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def capture(world, binding_path, output, max_bytes=512*2**20):
    world, output = Path(world), Path(output)
    if output.exists():
        raise FileExistsError('Use a new frozen acceptance directory')
    binding = json.loads(Path(binding_path).read_text())
    if Path(binding['world']).resolve() != world.resolve():
        raise ValueError('Binding names a different save')
    frame = binding['coordinate_frame']
    projection = Transformer.from_crs(4326, frame['crs'], always_xy=True)
    anchors = {}
    regions = set()
    for name, lonlat in ANCHORS.items():
        east, north = projection.transform(*lonlat)
        x, z = east-frame['west'], frame['north']-north
        anchors[name] = {'longitude_latitude': lonlat, 'world_xz': [x, z]}
        # A 256 m chart with 64 m source context. Preserve native regions.
        for rx in range(int((x-192)//512), int((x+192)//512)+1):
            for rz in range(int((z-192)//512), int((z+192)//512)+1):
                regions.add(f'r.{rx}.{rz}.mca')
    with (world/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        coverage = json.loads((world/'city-coverage.json').read_text())
        quality = json.loads((world/'regional-quality.json').read_text())
        if coverage['frame'] != frame or quality['frame'] != frame:
            raise ValueError('Save receipts and inherited frame differ')
        files = []
        for name in ('level.dat','earthcraft.json','city-coverage.json','regional-quality.json',
                     'resources.zip','photo-skin.json','photo-layer-verification.json','travel-controls.json'):
            path=world/name
            if path.is_file(): files.append(path)
        for directory in ('datapacks','playerdata','data'):
            if (world/directory).exists():
                files.extend(p for p in (world/directory).rglob('*')
                             if p.is_file() and not p.name.startswith('._'))
        for directory in ('region','entities','poi'):
            files.extend(world/directory/name for name in sorted(regions)
                         if (world/directory/name).is_file())
        size = sum(p.stat().st_size for p in files)
        if any(p.is_symlink() or not p.resolve().is_relative_to(world.resolve()) for p in files):
            raise ValueError('Save excerpt must contain regular files inside the bound world')
        if size > max_bytes or len(files)>5000:
            raise ValueError('Acceptance excerpt exceeds its fixed bounds')
        output.mkdir(parents=True)
        destination=output/'world';destination.mkdir()
        records={}
        for source in files:
            name=source.relative_to(world).as_posix()
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            before=sha(source);shutil.copyfile(source,target)
            if sha(target)!=before or sha(source)!=before:
                raise ValueError('Snapshot bytes changed inside save fence')
            records[name]={'sha256':before,'bytes':source.stat().st_size}
        (destination/'session.lock').write_bytes(b'\x00\x00\x00')
        proof={'schema':'earthcraft.playable-excerpt.v1','created':time.time(),
               'frame':frame,'binding_frame':binding['frame'],'anchors':anchors,
               'files':records,'bytes':size,'regions':sorted(regions),
               'installed_tile_receipts':len(quality['tiles']),
               'scan_upgrade_tiles':sum(t.get('quality')=='scan-points-and-roof' and
                                        t.get('upgraded_chunks',0)>0 for t in quality['tiles'].values()),
               'source_save_modified':False,'full_world_acceptance':False,
               'new_face_colors_applied':False,'client_render_tested':False}
        (output/'snapshot.json').write_text(json.dumps(proof,indent=2)+'\n')
        return {k:v for k,v in proof.items() if k not in ('files','frame')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world',type=Path,required=True)
    parser.add_argument('--binding',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(capture(args.world,args.binding,args.output)))
