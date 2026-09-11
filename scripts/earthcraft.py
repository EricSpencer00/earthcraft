"""Build, validate and install a populated Earthcraft pilot with one invocation."""
import argparse
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import nbtlib

from metric_world import build
from verify_metric_world import verify
from metric_server_check import check
from travel_controls import install_controls
from geographic_quality import audit
from world_replay import input_snapshot, digest, world_payload, compare_worlds, files_snapshot, file_hash
from photo_layer import apply_layer, visual_payload

ROOT=Path(__file__).resolve().parents[1]


def publish_install(staging,destination):
    """macOS atomic directory rename that cannot replace an existing save."""
    if sys.platform!='darwin':
        raise RuntimeError('Exclusive atomic save publication currently requires macOS')
    library=ctypes.CDLL(None,use_errno=True)
    rename=library.renamex_np
    rename.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(bytes(staging),bytes(destination),4):  # RENAME_EXCL
        error=ctypes.get_errno()
        raise OSError(error,'Exclusive save publication failed',str(destination))


def region_path(value):
    scheme,relative=value.split(':',1)
    if scheme=='project':root=ROOT
    elif scheme=='bulk':
        configured=os.environ.get('EARTHCRAFT_BULK_ROOT')
        root=Path(configured).expanduser() if configured else Path('/Volumes/LaCie/Earthcraft')
        if not root.is_dir():raise ValueError(f'Bulk source root unavailable: {root}; no internal fallback')
    else:raise ValueError('Unknown source location')
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('Source path leaves configured root')
    if not path.is_dir():raise ValueError(f'Frozen regional source unavailable: {path}')
    return path


def install(world,name):
    traversal=ROOT/'runtime/traversal'
    # World-local travel controls do not change the launcher's game directory.
    # The prepared Geographic Explorer profile has its own saves directory.
    use_traversal=(traversal/'config/flymod.json').is_file()
    saves=traversal/'saves' if use_traversal else Path.home()/'Library/Application Support/minecraft/saves'
    destination=saves/name
    if Path(name).name!=name or name in ('.','..'):
        raise ValueError('World name must be one directory name')
    if destination.exists() or destination.is_symlink():raise FileExistsError(destination)
    if not (world/'block-verification.json').exists():raise ValueError('Block verification must pass first')
    frozen=files_snapshot(world)
    saves.mkdir(parents=True,exist_ok=True)
    required=sum((world/name).stat().st_size for name in frozen)
    if shutil.disk_usage(saves.parent).free<20*2**30+required:
        raise ValueError('Installation would breach the 20 GiB free reserve')
    # Outside saves: failed/interrupted copies must not appear as playable worlds.
    staging=Path(tempfile.mkdtemp(prefix='.earthcraft-install-',dir=saves.parent))
    shutil.copytree(world,staging,dirs_exist_ok=True)
    if files_snapshot(staging)!=frozen or files_snapshot(world)!=frozen:
        raise ValueError(f'Copied save or source changed; unpublished copy retained at {staging}')
    level=nbtlib.load(staging/'level.dat')
    level['Data']['LevelName']=nbtlib.String(name)
    if use_traversal and not (world/'travel-controls.json').exists():
        # The mod applies its multiplier to vanilla speed, not our fallback boost.
        level['Data']['Player']['abilities']['flySpeed']=nbtlib.Float(.05)
    level.save(staging/'level.dat')
    expected=dict(frozen);expected['level.dat']=file_hash(staging/'level.dat')
    if files_snapshot(staging)!=expected or files_snapshot(world)!=frozen:
        raise ValueError(f'Final save integrity mismatch; unpublished copy retained at {staging}')
    publish_install(staging,destination)
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,default=ROOT/'configs/atlas-regions.json',
                        help='Frozen regional source configurations; never executes commands or downloads')
    parser.add_argument('--region')
    parser.add_argument('--replay-check',action='store_true',
                        help='Build a second fresh copy and require identical geographic blocks before installation')
    parser.add_argument('--name')
    parser.add_argument('--install-existing',type=Path,help='Install an already verified project world')
    args=parser.parse_args()
    catalog=json.loads(args.catalog.read_text())
    args.region=args.region or catalog['default_region']
    if args.region not in catalog['regions']:parser.error('Unknown region in selected catalog')
    if args.install_existing and args.replay_check:parser.error('Replay requires a build, not --install-existing')
    if args.name is None:args.name='Earthcraft-'+args.region+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')
    if Path(args.name).name!=args.name or args.name in ('.','..'):
        parser.error('World name must be a simple name')
    if args.install_existing:
        world=args.install_existing
    else:
        region=catalog['regions'][args.region]
        source=region_path(region['source'])
        point_source=region_path(region['points']) if region['points'] else None
        detail=region.get('photo_layer')
        photo_world=region_path(detail['source_world']) if detail else None
        world=ROOT/'worlds'/args.name
        if world.exists():raise FileExistsError(world)
        replay=ROOT/'worlds'/(args.name+'-replay')
        if args.replay_check and replay.exists():raise FileExistsError(replay)
        print('Fingerprinting frozen inputs and build dependencies',flush=True)
        inputs=input_snapshot(ROOT,region,source,point_source,photo_world)
        # All profiles reuse frozen acquired observations. A missing source is an
        # explicit failure, never a silent city download or fabricated fallback.
        build(source,world,point_source=point_source,world_frame=region.get('world_frame'))
        install_controls(world)
        if photo_world:
            apply_layer(world,photo_world,allow_experimental=detail.get('allow_experimental') is True,
                        start_at_detail=detail.get('start_at_detail') is True)
        verify(world)
        audit(world)
        receipt={'schema_version':1,'status':'built_pending_server_check',
                 'inputs':inputs,'input_sha256':digest(inputs),
                 'geographic_payload':world_payload(world),
                 'photo_payload':visual_payload(world),
                 'replay_check':None,'client_visual_verified':False}
        receipt_path=world/'build-receipt.json'
        receipt_path.write_text(json.dumps(receipt,indent=2))
        if args.replay_check:
            print('Rebuilding identical frozen inputs for geographic replay check',flush=True)
            build(source,replay,point_source=point_source,world_frame=region.get('world_frame'))
            install_controls(replay)
            if photo_world:
                apply_layer(replay,photo_world,allow_experimental=detail.get('allow_experimental') is True,
                            start_at_detail=detail.get('start_at_detail') is True)
            verify(replay)
            audit(replay)
            receipt['replay_check']=compare_worlds(world,replay)
            receipt['photo_replay_equal']=visual_payload(world)==visual_payload(replay)
            receipt['replay_world']=str(replay)
            receipt_path.write_text(json.dumps(receipt,indent=2))
            if not receipt['replay_check']['equal'] or not receipt['photo_replay_equal']:
                raise ValueError('Replay changed geographic blocks or photo assets; both outputs preserved, installation stopped')
        check(world,ROOT/'runs'/f'{args.name}-server-check',travel_probe=True,photo_probe=photo_world is not None)
        if input_snapshot(ROOT,region,source,point_source,photo_world)!=inputs:
            raise ValueError('Frozen inputs changed during build; installation stopped')
        receipt['status']='server_verified_ready_to_install'
        receipt_path.write_text(json.dumps(receipt,indent=2))
    installed=install(world,args.name)
    modded=installed.parent.parent==ROOT/'runtime/traversal'
    native=(installed/'travel-controls.json').exists()
    print(json.dumps({'world':str(installed),'open_in':
        'Earthcraft — Geographic Explorer → Singleplayer' if modded else 'Minecraft Java 1.21.10 → Singleplayer',
        'build_commands_required':False,'travel': 'Press G or Pause → Travel for player size, walking speed and fast flight.' if native else
        'Double-tap Space to fly; B toggles Fly Mod. Configure speed under Mods → Fly Mod.' if modded
        else 'Double-tap jump for creative flight; initial speed is four times vanilla.'},indent=2))


if __name__=='__main__':main()
