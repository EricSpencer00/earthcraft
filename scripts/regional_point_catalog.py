"""Inventory public full-density 3DEP surveys intersecting frozen Chicagoland.

This inventories acquisition candidates, never claims their points are already
downloaded. Original EPT metadata and S3 listings remain in the control plane.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from pyproj import Transformer

from ept_buildings import overlaps
from regional_scans import frozen_get
from region_expansion import atomic


def discover(plan, destination):
    destination = Path(destination)
    projects = set()
    for prefix in ('IL_', 'USGS_LPC_IL', 'IN_', 'USGS_LPC_IN', 'WI_', 'USGS_LPC_WI'):
        url = 'https://usgs-lidar-public.s3.amazonaws.com/?list-type=2&delimiter=/&prefix='+prefix
        raw = frozen_get(url, destination/(prefix+'.xml'))
        tree = ET.fromstring(raw); ns = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        if tree.findtext('s:IsTruncated', namespaces=ns) != 'false':
            raise ValueError('Truncated point-survey inventory')
        projects.update(e.text.rstrip('/') for e in tree.findall('s:CommonPrefixes/s:Prefix',ns))
    projection = Transformer.from_crs(plan['frame']['crs'], 3857, always_xy=True)
    tiles = plan['tiles']
    west=min(tile['west'] for tile in tiles); east=max(tile['west']+tile['size'] for tile in tiles)
    north=max(tile['north'] for tile in tiles); south=min(tile['north']-tile['size'] for tile in tiles)
    x,y=projection.transform([west,east]*2,[north,north,south,south])
    query=[min(x),min(y),max(x),max(y)]
    def inspect(project):
        metadata = json.loads(frozen_get('https://usgs-lidar-public.s3.amazonaws.com/'+project+'/ept.json',
                                       destination/'projects'/(project+'.json')))
        if metadata['srs'].get('horizontal') != '3857':
            return None
        if not overlaps(metadata.get('boundsConforming',metadata['bounds']),query):
            return None
        years = re.findall(r'(?:19|20)\d{2}',project)
        shorthand = re.search(r'[_-](?:D|B)(\d{2})(?:_|$)',project)
        # Names often end in a LAS publication year: Cook_2017_LAS_2019
        # describes a 2017 acquisition, not a newer 2019 scene.
        year = min(map(int,years)) if years else (2000+int(shorthand[1]) if shorthand else None)
        return {'project':project,'year_from_project_name':year,'bounds':metadata.get('boundsConforming',metadata['bounds']),
            'points':metadata['points'],'source_metadata':str((destination/'projects'/(project+'.json')).resolve()),
            'source_metadata_year_is_not_exact_capture_date':True,'points_acquired':False}
    with ThreadPoolExecutor(max_workers=4) as executor:
        surveys = [item for item in executor.map(inspect,sorted(projects)) if item]
    record={'schema':'earthcraft-regional-full-density-point-inventory-v1','surveys':surveys,
        'selection':'All intersecting public full-density EPT surveys; paired-DTM vertical check before use',
        'license':'US Government Public Domain','registry':'https://registry.opendata.aws/usgs-lidar/',
        'points_acquired':False,'llm_used':False}
    atomic(destination/'catalog.json',json.dumps(record,indent=2).encode())
    return record


def candidates(tile,frame,catalog):
    project=Transformer.from_crs(frame['crs'],3857,always_xy=True)
    x,y=project.transform([tile['west'],tile['west']+tile['size']]*2,
        [tile['north'],tile['north'],tile['north']-tile['size'],tile['north']-tile['size']])
    query=[min(x),min(y),max(x),max(y)]
    return sorted([survey for survey in catalog['surveys'] if overlaps(survey['bounds'],query)],
        key=lambda survey: (-(survey['year_from_project_name'] or 0),survey['project']))
