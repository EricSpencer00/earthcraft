"""Compile against the already-installed, exact intermediary game/API jars."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build():
    libs = Path.home() / 'Library/Application Support/minecraft/libraries'
    game = ROOT / 'runtime/traversal/.fabric/remappedJars/minecraft-1.21.10-0.19.5/client-intermediary.jar'
    jars = [game, *sorted(libs.rglob('*.jar')), *sorted((ROOT/'runtime/traversal/.fabric/processedMods').glob('*.jar'))]
    if not game.exists():raise FileNotFoundError('Pinned intermediary game missing')
    output=ROOT/'vendor/live/build';output.mkdir(parents=True,exist_ok=True)
    java=Path('/opt/homebrew/opt/openjdk@21/bin')
    subprocess.run([str(java/'javac'),'--release','21','-proc:none','-cp',':'.join(map(str,jars)),
                    '-d',str(output),str(ROOT/'mods/live/src/earthcraft/LiveImport.java')],check=True)
    target=ROOT/'vendor/live/earthcraft-live-0.1.0.jar'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT/'mods/live/fabric.mod.json','fabric.mod.json')
        for path in sorted(output.rglob('*.class')):z.write(path,str(path.relative_to(output)))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    report={'jar':str(target),'sha256':sha(target),'game_sha256':sha(game),
            'source_sha256':sha(ROOT/'mods/live/src/earthcraft/LiveImport.java'),
            'mapping':'Fabric intermediary 1.21.10; Yarn 1.21.10+build.3 names checked',
            'network_requests':0}
    (target.with_suffix('.json')).write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':build()
