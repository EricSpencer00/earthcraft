"""Install verified traversal mods into a new isolated official-launcher profile."""
import hashlib
import json
from pathlib import Path
import shutil
import nbtlib

ROOT=Path(__file__).resolve().parents[1]


def install():
    staged=ROOT/'vendor/traversal'
    manifest=json.loads((staged/'manifest.json').read_text())
    minecraft=Path.home()/'Library/Application Support/minecraft'
    runtime=ROOT/'runtime/traversal'
    version_id='earthcraft-traversal-1.21.10'
    version_dir=minecraft/'versions'/version_id
    launcher_path=minecraft/'launcher_profiles.json'
    original=launcher_path.read_bytes();launcher=json.loads(original)
    if 'earthcraft-traversal' in launcher['profiles'] or runtime.exists() or version_dir.exists():
        raise FileExistsError('Traversal installation already exists; inspect before changing it')
    for mod in manifest['mods']:
        if hashlib.sha512(Path(mod['path']).read_bytes()).hexdigest()!=mod['sha512']:
            raise ValueError('Staged mod checksum mismatch')
    (runtime/'mods').mkdir(parents=True)
    (runtime/'config').mkdir()
    for mod in manifest['mods']:
        shutil.copy2(mod['path'],runtime/'mods'/Path(mod['path']).name)
    config={'mouse_control':False,'only_for_creative':True,'fly_up_down_blocks':.1,
        'fly_speed_multiplier':4.0,'run_speed_multiplier':1.0,'multiply_up_down':True,
        'fade_movement':True,'override_exhaustion':False,'active_in_multiplayer':False,
        'active_in_singleplayer':True}
    (runtime/'config/flymod.json').write_text(json.dumps(config,indent=2))
    source=ROOT/'worlds/Earthcraft-Metric-Water-Tower-v3'
    destination=runtime/'saves/Earthcraft-Chicago-Measured-512m'
    shutil.copytree(source,destination)
    level=nbtlib.load(destination/'level.dat')
    level['Data']['LevelName']=nbtlib.String('Earthcraft Chicago — Measured 512m')
    level['Data']['Player']['abilities']['flySpeed']=nbtlib.Float(.05)
    level.save(destination/'level.dat')
    profile=json.loads((staged/'profile.json').read_text());profile['id']=version_id
    version_dir.mkdir(parents=True)
    (version_dir/f'{version_id}.json').write_text(json.dumps(profile,indent=2))
    backup=ROOT/'runs/launcher_profiles-before-traversal.json'
    with backup.open('xb') as file:file.write(original)
    backup.chmod(0o600)
    launcher['profiles']['earthcraft-traversal']={'name':'Earthcraft — Geographic Explorer',
        'type':'custom','lastVersionId':version_id,'gameDir':str(runtime),
        'javaArgs':'-Xmx4G -Xms512M','icon':'Grass'}
    if launcher_path.read_bytes()!=original:
        raise RuntimeError('Launcher profiles changed during installation; backup preserved, integration not written')
    temporary=launcher_path.with_suffix('.earthcraft.tmp')
    temporary.write_text(json.dumps(launcher,indent=2));temporary.replace(launcher_path)
    report={'profile':'Earthcraft — Geographic Explorer','version':version_id,'runtime':str(runtime),
        'world':str(destination),'mods':[{k:mod[k] for k in ('project','version','sha512')} for mod in manifest['mods']],
        'loader':manifest['loader'],'config':config,'client_runtime_verified':False,
        'controls':'Double-tap Space to fly, B toggles Fly Mod; Mods → Fly Mod → Configure for speed.',
        'geographic_scale_changed':False}
    (ROOT/'runs/traversal-install.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':install()
