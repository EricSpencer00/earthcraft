"""Bind the tested mod to the one closed installed save, retaining a full backup."""
import fcntl
import json
from pathlib import Path
import shutil
from live_city import ROOT,initialize,sha,atomic
from world_replay import files_snapshot


def install():
    world=ROOT/'runtime/traversal/saves/Earthcraft'
    backup=ROOT/'archives/Earthcraft-before-live-updates-20260910'
    exchange=ROOT/'runs/chicago-live-001'
    config=ROOT/'runtime/traversal/config/earthcraft-live.json'
    mod=ROOT/'vendor/live/earthcraft-live-0.1.0.jar'
    destination=ROOT/'runtime/traversal/mods'/mod.name
    if backup.exists() or exchange.exists() or config.exists() or destination.exists():raise FileExistsError('Already installed or staged; inspect existing evidence')
    verification=json.loads((ROOT/'runs/live-import-server-002/verification.json').read_text())
    if not verification['passed'] or sha(mod)!=sha(ROOT/'runs/live-import-server-002/mods'/mod.name):raise ValueError('Mod not the exact tested binary')
    with (world/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=files_snapshot(world);size=sum(p.stat().st_size for p in world.rglob('*') if p.is_file())
        if shutil.disk_usage(world).free-size<22*2**30:raise RuntimeError('Backup would cross storage reserve')
        shutil.copytree(world,backup)
        if files_snapshot(backup)!=before or files_snapshot(world)!=before:raise ValueError('Backup/source verification failed')
        binding=initialize(exchange,world,config)
        shutil.copy2(mod,destination)
        if sha(mod)!=sha(destination) or files_snapshot(world)!=before:raise ValueError('Installation verification failed')
        report={'world':str(world),'backup':str(backup),'snapshot':before,'world_files_changed':False,
                'mod_sha256':sha(mod),'protected_chunks':len(binding['protected_chunks']),
                'server_verified':True,'client_verified':False}
        atomic(exchange/'installation.json',json.dumps(report,indent=2).encode())
        print(json.dumps({k:v for k,v in report.items() if k!='snapshot'},indent=2))


if __name__=='__main__':install()
