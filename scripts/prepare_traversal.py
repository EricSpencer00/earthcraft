"""Resolve and verify a small, isolated Fabric 1.21.10 traversal installation."""
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='1.21.10'


def fetch(url,limit=32*1024**2):
    request=urllib.request.Request(url,headers={'User-Agent':'Earthcraft-local-pilot/0.1'})
    with urllib.request.urlopen(request,timeout=60) as response:
        raw=response.read(limit+1)
    if len(raw)>limit:raise ValueError('Download limit exceeded')
    return raw


def prepare():
    out=ROOT/'vendor/traversal';out.mkdir(parents=True,exist_ok=True)
    records=[]
    for project in ('fly-mod-3d','cloth-config','modmenu','fabric-api'):
        query=urllib.parse.urlencode({'game_versions':json.dumps([VERSION]),'loaders':json.dumps(['fabric'])})
        versions=json.loads(fetch(f'https://api.modrinth.com/v2/project/{project}/version?{query}'))
        stable=[v for v in versions if v['version_type']=='release']
        selected=(stable or versions)[0]
        asset=next(f for f in selected['files'] if f['primary'])
        path=out/asset['filename']
        if not path.exists():path.write_bytes(fetch(asset['url']))
        if hashlib.sha512(path.read_bytes()).hexdigest()!=asset['hashes']['sha512']:
            raise ValueError(f'Hash mismatch for {project}')
        with zipfile.ZipFile(path) as archive:
            mod=json.loads(archive.read('fabric.mod.json'))
        record={'project':project,'version':selected['version_number'],'path':str(path),
            'url':asset['url'],'sha512':asset['hashes']['sha512'],'dependencies':selected['dependencies'],
            'fabric_mod':mod}
        records.append(record)
        print(project,selected['version_number'],mod.get('depends'),flush=True)
    loaders=json.loads(fetch(f'https://meta.fabricmc.net/v2/versions/loader/{VERSION}'))
    loader=next(v['loader']['version'] for v in loaders if v['loader']['stable'])
    profile=json.loads(fetch(f'https://meta.fabricmc.net/v2/versions/loader/{VERSION}/{loader}/profile/json'))
    (out/'profile.json').write_text(json.dumps(profile,indent=2))
    (out/'manifest.json').write_text(json.dumps({'minecraft':VERSION,'loader':loader,'mods':records},indent=2))
    return out


if __name__=='__main__':prepare()
