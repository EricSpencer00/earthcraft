"""Retain public photo/calibration inputs near every frozen pilot building.

Camera proximity is discovery, not wall visibility. This produces no world
writer, registration transform, or admitted colors. Original bytes stay local.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request
import zlib

from pyproj import Transformer
from shapely.geometry import shape, Point
from facade_pilot import canonical_sha


def digest(raw): return hashlib.sha256(raw).hexdigest()


def download(url, limit):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if (parsed.scheme != 'https' or parsed.username or parsed.password or
            not host or not (host.endswith('.fbcdn.net') or host.endswith('.mapillary.com'))):
        raise ValueError('Expected original Mapillary content origin')
    request = urllib.request.Request(url, headers={'User-Agent':'EarthcraftResearch/0.1'})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(limit+1)
    if len(raw) > limit: raise ValueError('Bounded source download exceeded')
    return raw


def decode_cluster(raw, limit=64*2**20):
    # Mapillary's SfM records are zlib streams, not UTF-8 JSON responses.
    if raw.startswith(b'x'):
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(raw, limit+1)
        if len(decoded)>limit or not decoder.eof or decoder.unused_data:
            raise ValueError('Bounded calibration stream required')
    else: decoded = raw
    if len(decoded)>limit: raise ValueError('Bounded calibration exceeded')
    records = json.loads(decoded)
    if not isinstance(records, list): records = [records]
    if not records or any(not isinstance(r,dict) or not {'cameras','shots','points'}<=r.keys() for r in records):
        raise ValueError('Unexpected original reconstruction schema')
    return decoded, records


def acquire(pilot_path, cache, output):
    pilot = json.loads(Path(pilot_path).read_text())
    if pilot['building_count'] != 20 or canonical_sha(pilot['buildings']) != pilot['selection_sha256']:
        raise ValueError('Preserve all twenty frozen pilot buildings')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    project = Transformer.from_crs(4326, pilot['frame']['crs'], always_xy=True)
    photos = {}
    for path in sorted(Path(cache).glob('*-nearby.json')):
        for item in json.loads(path.read_text()).get('data',[]): photos[str(item['id'])] = item
    selected, per_building = {}, {}
    for building in pilot['buildings']:
        geometry = shape(building['footprint_geometry']); nearby = []
        for image_id, item in photos.items():
            geo = item.get('computed_geometry') or item.get('geometry')
            if not geo or geo.get('type') != 'Point': continue
            distance = geometry.distance(Point(*project.transform(*geo['coordinates'])))
            if distance<=75: nearby.append((distance,image_id))
        nearby.sort()
        per_building[building['pilot_id']] = {'nearby_metadata_count':len(nearby),
              'selected_image_ids':[id for _,id in nearby[:2]],
              'verified_visible_wall_area_m2':None, 'admitted_face_colors':0}
        for distance,image_id in nearby[:2]:
            if not image_id.isdigit(): raise ValueError('Numeric source image identity required')
            selected.setdefault(image_id,{'metadata':photos[image_id],'pilot_ids':[], 'distance_m':distance})['pilot_ids'].append(building['pilot_id'])
    if len(selected)>40: raise ValueError('Frozen two-image-per-building acquisition bound exceeded')
    report = {'schema':'earthcraft.pilot-photo-inputs.v1','pilot_sha256':digest(Path(pilot_path).read_bytes()),
              'selection_sha256':pilot['selection_sha256'],'frame_sha256':pilot['frame_sha256'],
              'per_building':per_building,'images':[],'calibrations':[],
              'source':'Existing Mapillary discovery metadata',
              'license_source':'https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data',
              'capture_candidates_are_not_registered_walls':True,'colors_applied':0,
              'missing':['metric registration with independent held-out controls','static wall ownership and occlusion','complete source creator attribution']}
    def checkpoint():
        report['updated']=time.time();(output/'pilot-inputs.local.json').write_text(json.dumps(report,indent=2)+'\n')
    total = 0
    for image_id, selected_item in selected.items():
        metadata = selected_item['metadata']; path=output/(image_id+'.jpg')
        record={'image_id':image_id,'pilot_ids':selected_item['pilot_ids'],
                'distance_to_footprint_m':selected_item['distance_m'],'captured_at':metadata.get('captured_at'),
                'camera_type':metadata.get('camera_type'),'registered':False,'admitted_colors':0,
                'attribution_complete':bool(metadata.get('creator')),'state':'pending'}
        try:
            raw = path.read_bytes() if path.exists() else download(metadata.get('thumb_original_url') or metadata['thumb_2048_url'],16*2**20)
            total += len(raw)
            if len(raw)>16*2**20 or total>128*2**20: raise ValueError('128 MiB photo input allowance exceeded')
            if not (raw.startswith(b'\xff\xd8') or raw.startswith(b'\x89PNG\r\n\x1a\n')): raise ValueError('Original image required')
            if not path.exists():path.write_bytes(raw)
            (output/(image_id+'.metadata.local.json')).write_text(json.dumps(metadata))
            record.update(state='retained_registration_pending',bytes=len(raw),sha256=digest(raw))
        except Exception as e:
            # Do not expose signed URLs in diagnostics.
            record.update(state='input_failure',error_type=type(e).__name__)
        report['images'].append(record);checkpoint()
    clusters={str(i['metadata']['sfm_cluster']['id']):i['metadata']['sfm_cluster']['url']
              for i in selected.values() if isinstance(i['metadata'].get('sfm_cluster'),dict)}
    total = 0
    for cluster_id, url in clusters.items():
        name=digest(cluster_id.encode())[:16];raw_path=output/(name+'.cluster.zlib')
        record={'cluster_id':cluster_id,'file':name+'.cluster.local.json','state':'pending'}
        try:
            raw=raw_path.read_bytes() if raw_path.exists() else download(url,32*2**20)
            total+=len(raw)
            if total>128*2**20:raise ValueError('128 MiB compressed calibration allowance exceeded')
            if not raw_path.exists():raw_path.write_bytes(raw)
            decoded, records=decode_cluster(raw)
            (output/record['file']).write_bytes(decoded)
            record.update(state='retained_unregistered',original_sha256=digest(raw),
                  decoded_sha256=digest(decoded),bytes=len(raw),
                  shots=sum(len(r['shots']) for r in records), points=sum(len(r['points']) for r in records),
                  reference_available=any('reference_lla' in r or 'reference' in r for r in records))
        except Exception as e:record.update(state='calibration_input_failure',error_type=type(e).__name__)
        report['calibrations'].append(record);checkpoint()
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('pilot','cache','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=acquire(a.pilot,a.cache,a.output)
    print(json.dumps({'retained_images':sum(i['state']=='retained_registration_pending' for i in r['images']),
                      'retained_calibrations':sum(i['state']=='retained_unregistered' for i in r['calibrations']),
                      'colors_applied':r['colors_applied']}))
