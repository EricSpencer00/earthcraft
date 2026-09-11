"""Bounded public terrain + OSM observations for a new location, no inference."""
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request

from pyproj import Transformer
from aws_terrain import prepare as terrain
from metric_chart import chart
from osm_json_to_kml import metres,building_tag

ENDPOINT='https://overpass-api.de/api/interpreter'


def frozen_terrain(source):
    """Only terrain products are frozen here; map products belong to the next stage."""
    return {name:hashlib.sha256((source/name).read_bytes()).hexdigest()
            for name in ('elevation.tif','rasters.npz','cook-buildings-2022.json')}


def normalize(data):
    if data.get('remark') or 'elements' not in data:raise ValueError('Incomplete Overpass response')
    if len(data['elements'])>10000:raise ValueError('More than 10,000 map features')
    ways=[];omitted=[]
    for element in data['elements']:
        kind=element.get('type');identifier=element.get('id')
        if kind!='way':
            omitted.append({'type':kind,'id':identifier,'reason':'Only complete way geometry supported'})
            continue
        geometry=element.get('geometry',[]);nodes=element.get('nodes',[])
        if len(geometry)!=len(nodes) or len(nodes)<2 or any(p is None for p in geometry):
            raise ValueError('Incomplete way geometry; no fragment substitution')
        if len(nodes)>10000:raise ValueError('Way vertex budget exceeded')
        coordinates=[]
        for p in geometry:
            lon,lat=float(p['lon']),float(p['lat'])
            if not math.isfinite(lon+lat) or not -180<=lon<=180 or not -90<=lat<=90:
                raise ValueError('Invalid WGS84 observation')
            coordinates.append([lon,lat])
        ways.append({'id':identifier,'tags':element.get('tags',{}),'coordinates':coordinates,
                     'closed':nodes[0]==nodes[-1]})
    buildings=[w for w in ways if building_tag(w['tags'])]
    known=[w for w in buildings if (metres(w['tags'].get('height','')) or 0)>0]
    return ways,{'way_features':len(ways),'building_way_features':len(buildings),
                 'explicit_height_features':len(known),'height_unknown_features':len(buildings)-len(known),
                 'omitted_features':omitted,'coverage_is_complete':False}


def prepare(lon,lat,size,destination,resume=False):
    destination=Path(destination)
    if destination.exists() and not resume:raise FileExistsError(destination)
    meta=chart(lon,lat,size)
    inverse=Transformer.from_crs(meta['crs'],4326,always_xy=True)
    corners=[inverse.transform(x,y) for x in (meta['west']-16,meta['west']+size+16)
             for y in (meta['north']+16,meta['north']-size-16)]
    west,east=min(p[0] for p in corners),max(p[0] for p in corners)
    south,north=min(p[1] for p in corners),max(p[1] for p in corners)
    if east-west>1 or north-south>1:raise ValueError('Split dateline or overly broad query before acquisition')
    bbox=f'{south:.8f},{west:.8f},{north:.8f},{east:.8f}'
    selectors=('building','building:part','highway','landuse','leisure','natural','water')
    query='[out:json][timeout:30][maxsize:16777216];('+''.join(
        f'way["{tag}"]({bbox});' for tag in selectors)+f'relation["building"]({bbox}););out body geom;'
    state_path=destination.parent/(destination.name+'-acquisition.json')
    identity={'location_wgs84':[lon,lat],'size':size,'query':query,'endpoint':ENDPOINT}
    if destination.exists():
        state=json.loads(state_path.read_text())
        if state['request']!=identity or state['terrain_sha256']!=frozen_terrain(destination):
            raise ValueError('Resume request or frozen terrain changed')
        source=destination
        saved=json.loads((source/'sources.json').read_text())
        if any(saved[k]!=meta[k] for k in ('crs','west','north','size')):
            raise ValueError('Resume source chart changed')
        if state['status']=='complete':
            actual={name:hashlib.sha256((source/name).read_bytes()).hexdigest()
                    for name in state['completed_sha256']}
            if actual!=state['completed_sha256']:raise ValueError('Completed public source changed')
            return source
    else:
        if state_path.exists():raise FileExistsError(state_path)
        source=terrain(lon,lat,size,destination)
        state={'request':identity,'terrain_sha256':frozen_terrain(source),'status':'terrain_ready'}
        state_path.write_text(json.dumps(state,indent=2))
    request=urllib.request.Request(ENDPOINT,data=urllib.parse.urlencode({'data':query}).encode(),
                                  headers={'User-Agent':'EarthcraftResearch/0.1 (bounded local geographic conversion)'})
    raw_path=source/'osm-response.json'
    if raw_path.exists():
        raw=raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=state.get('response_sha256'):
            raise ValueError('Uncommitted or changed map response; preserved for inspection')
    else:
        # A resumed run never hammers a public endpoint after a recent failure.
        last=state.get('last_request_utc')
        if last and (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()<30:
            raise ValueError('Public endpoint cooldown: wait at least 30 seconds before retrying')
        state.update(status='map_requested',last_request_utc=datetime.now(timezone.utc).isoformat())
        state_path.write_text(json.dumps(state,indent=2))
        with urllib.request.urlopen(request,timeout=45) as response:
            raw=response.read(8*2**20+1)
        if len(raw)>8*2**20:raise ValueError('OSM response exceeds 8 MiB')
        raw_path.write_bytes(raw)
        state.update(status='map_received',response_sha256=hashlib.sha256(raw).hexdigest())
        state_path.write_text(json.dumps(state,indent=2))
    if len(raw)>8*2**20:raise ValueError('OSM response exceeds 8 MiB')
    data=json.loads(raw);ways,inventory=normalize(data)
    record={'provider':'OpenStreetMap contributors via Overpass','endpoint':ENDPOINT,'query':query,
            'retrieved_utc':datetime.now(timezone.utc).isoformat(),
            'database_snapshot_utc':data.get('osm3s',{}).get('timestamp_osm_base'),
            'capture_date':None,'capture_date_note':'Database timestamp is not a physical capture date',
            'license':'ODbL 1.0','attribution':'© OpenStreetMap contributors',
            'license_url':'https://www.openstreetmap.org/copyright',
            'response_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
            'coordinates':'WGS84 longitude, latitude','inventory':inventory,
            'height_policy':'Explicit metre heights only; no levels-to-height conversion',
            'shape_policy':'Mapped footprints with shell/flat-cap approximation, not scanned facade geometry',
            'llm_used':False,'google_imagery_used':False}
    (source/'osm-acquisition.json').write_text(json.dumps(record,indent=2))
    (source/'osm-ways.json').write_text(json.dumps(ways))
    meta=json.loads((source/'sources.json').read_text())
    meta.update(buildings_available=True,building_source_kind='osm-explicit',osm_source=record,
                cover_source='Sparse explicit OSM area tags; other surfaces remain neutral',
                missing_layers=['complete building heights','facade imagery','complete ground cover','bathymetry'],
                purpose='Public terrain and mapped ways; partial mapped buildings, not LiDAR reconstruction')
    (source/'sources.json').write_text(json.dumps(meta,indent=2))
    state.update(status='complete',completed_sha256={name:hashlib.sha256((source/name).read_bytes()).hexdigest()
                 for name in ('osm-response.json','osm-acquisition.json','osm-ways.json','sources.json')})
    state_path.write_text(json.dumps(state,indent=2))
    print(json.dumps({'source':str(source),'osm_bytes':len(raw),**inventory},indent=2),flush=True)
    return source
