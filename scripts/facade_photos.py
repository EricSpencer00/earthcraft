"""Bounded Commons photo acquisition for the Water Tower registration experiment.

No imagery is sent to an inference service. Original bytes and license metadata
are preserved; descriptions and camera locations are unverified source claims.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

import requests
from appearance_adapter import capture_interval

TITLES = [
    ('west', 'File:Chicago Water Tower view from west.jpeg'),
    ('south-a', 'File:Chicago Water Tower, Chicago, Illinois (11004311056).jpg'),
    ('south-b', 'File:Chicago Water Tower, Chicago, Illinois (11004439483).jpg'),
]


def normalized_capture(value):
    """Normalize known Commons date encodings, preserving unknown raw metadata."""
    try:
        start,end=capture_interval(value)
        return [start.isoformat(),end.isoformat()]
    except ValueError:
        pass
    if isinstance(value,str):
        match=re.fullmatch(r'Taken on\s+(\d{1,2}) ([A-Za-z]+) (\d{4}), \d{2}:\d{2}',value)
        if match:
            months='January February March April May June July August September October November December'.split()
            if match[2] in months:
                try:
                    day=datetime(int(match[3]),months.index(match[2])+1,int(match[1])).date().isoformat()
                    return [day,day]
                except ValueError:
                    pass
    return None


def validate_titles(titles):
    if not isinstance(titles,(list,tuple)) or not 1<=len(titles)<=3:
        raise ValueError('Photo batches contain one to three explicit Commons files')
    names=set();files=set()
    for entry in titles:
        if not isinstance(entry,(list,tuple)) or len(entry)!=2:
            raise ValueError('Each photo requires [local_id, Commons title]')
        name,title=entry
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',name):
            raise ValueError('Photo IDs must be simple lowercase names')
        if not isinstance(title,str) or not title.startswith('File:') or '|' in title or len(title)>300:
            raise ValueError('Expected a bounded, explicit Commons File title')
        if name in names or title in files:raise ValueError('Duplicate photo ID or title')
        names.add(name);files.add(title)
    return titles


def fetch(output, titles=TITLES):
    titles=validate_titles(titles)
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if shutil.disk_usage(output.parent).free < 21 * 2**30:
        raise ValueError('Keep 20 GiB internal reserve and 1 GiB working allowance')
    session = requests.Session()
    session.headers['User-Agent'] = 'EarthcraftResearch/0.1 (local reconstruction experiment)'
    response = session.get('https://commons.wikimedia.org/w/api.php', params={
        'action':'query', 'format':'json', 'prop':'imageinfo',
        'iiprop':'url|extmetadata|metadata|size|sha1',
        'titles':'|'.join(title for _, title in titles)}, timeout=30)
    response.raise_for_status()
    metadata = response.json()
    pages = {p['title']:p for p in metadata['query']['pages'].values()}
    records = []
    for name, title in titles:
        info = pages[title]['imageinfo'][0]
        ext = info['extmetadata']
        if ext['LicenseShortName']['value'] not in ('CC BY-SA 2.0', 'CC BY-SA 4.0', 'CC BY 2.0', 'CC BY 4.0'):
            raise ValueError('Unexpected source license; review before acquisition')
        if info['size'] > 16 * 2**20 or urlsplit(info['url']).hostname != 'upload.wikimedia.org':
            raise ValueError('Unexpected photo size or origin')
        records.append((name, title, info))
    if sum(r[2]['size'] for r in records) > 40 * 2**20:
        raise ValueError('Photo batch exceeds 40 MiB budget')
    output.mkdir()
    (output/'commons-metadata.json').write_text(json.dumps(metadata, indent=2))
    manifest = {'status':'acquiring', 'llm_used':False, 'assets':[],
                'retrieved_utc':datetime.now(timezone.utc).isoformat(),
                'limits':{'max_images':3, 'max_batch_bytes':40*2**20},
                'geometry_role':'No camera pose is accepted from GPS alone.',
                'distribution':'Local experiment; attribution and share-alike retained.'}
    for name, title, info in records:
        with session.get(info['url'], stream=True, timeout=45) as r:
            r.raise_for_status()
            body = bytearray()
            for chunk in r.iter_content(65536):
                body.extend(chunk)
                if len(body) > info['size']:
                    raise ValueError('Unexpected image response size')
        if len(body) != info['size'] or hashlib.sha1(body).hexdigest() != info['sha1']:
            raise ValueError('Original photo checksum mismatch')
        (output/f'{name}.jpg').write_bytes(body)
        ext = info['extmetadata']
        manifest['assets'].append({'id':name, 'file':f'{name}.jpg', 'title':title,
            'description_url':info['descriptionurl'], 'url':info['url'],
            'sha256':hashlib.sha256(body).hexdigest(), 'bytes':len(body),
            'author_html':ext['Artist']['value'], 'license':ext['LicenseShortName']['value'],
            'license_url':ext['LicenseUrl']['value'],
            'capture_date_source':ext.get('DateTimeOriginal',{}).get('value'),
            'capture_interval_iso':normalized_capture(ext.get('DateTimeOriginal',{}).get('value')),
            'source_camera_latitude':ext.get('GPSLatitude',{}).get('value'),
            'source_camera_longitude':ext.get('GPSLongitude',{}).get('value'),
            'camera_pose_verified':False})
        (output/'manifest.json').write_text(json.dumps(manifest, indent=2))
    manifest['status'] = 'originals_verified_registration_pending'
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--west-validation',action='store_true')
    selection.add_argument('--titles',type=Path,help='JSON list of up to three [id, Commons File title] entries')
    args = parser.parse_args()
    titles = (json.loads(args.titles.read_text()) if args.titles else
              [('west-check','File:Chicago - -i---i- (29496640250).jpg')] if args.west_validation else TITLES)
    fetch(args.output,titles)
