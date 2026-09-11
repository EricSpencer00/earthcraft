"""Local Chicago acquisition preparation and bounded Arnis draft generation; no AI calls."""
import argparse, collections, hashlib, json, math, os, shutil, subprocess, sys, time
from pathlib import Path
import osmium
import psutil
from pyproj import Geod, Transformer
from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep

REPO = Path(__file__).resolve().parents[1]
STATE = REPO/'runs/chicago'
GIB = 1024**3
KEYS = {'building','building:part','highway','railway','waterway','landuse','natural',
        'leisure','amenity','barrier','man_made','bridge','shop','historic','tourism','aeroway'}

def write_json(path, value):
    temporary = path.with_suffix(path.suffix+'.partial')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    temporary.replace(path)

def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def guard(root, allowance=GIB):
    mount = root.parents[1]
    if not mount.is_mount() or root.stat().st_dev != mount.stat().st_dev:
        raise RuntimeError('Expected external volume is not mounted; no internal fallback')
    if shutil.disk_usage(REPO).free < 20*GIB:
        raise RuntimeError('Internal free-space reserve reached')
    if shutil.disk_usage(root).free < 100*GIB+allowance:
        raise RuntimeError('External free-space reserve reached')

def prepare(root):
    guard(root)
    sources=root/'sources'; raw=sources/'illinois.osm.pbf'; boundary=sources/'chicago-boundary.geojson'
    if not raw.exists(): raise FileNotFoundError(raw)
    geoj=json.loads(boundary.read_text())
    city=unary_union([shape(f['geometry']) for f in geoj['features']])
    to_m=Transformer.from_crs(4326,26916,always_xy=True).transform
    from_m=Transformer.from_crs(26916,4326,always_xy=True).transform
    city_m=transform(to_m,city)
    # Context belongs to the extract; only city intersections are counted as city buildings.
    area=transform(from_m,city_m.buffer(100)); prepared=prep(area); city_prepared=prep(city)
    west,south,east,north=area.bounds
    selected_ways=set(); counts=collections.Counter(); tags_count=collections.Counter()
    heights=[]
    target=sources/'chicago.osm.pbf'; partial=sources/'chicago-preparing.osm.pbf'
    if target.exists(): raise FileExistsError('Chicago extract already exists; retain it and its manifest')
    if partial.exists(): raise FileExistsError('An incomplete extract exists; inspect before retry')
    print('Extracting Chicago features locally from the regional PBF...',flush=True)
    with osmium.BackReferenceWriter(str(partial),str(raw),remove_tags=False,relation_depth=2) as writer:
        class Select(osmium.SimpleHandler):
            def node(self,n):
                if not n.location.valid():return
                if west<=n.lon<=east and south<=n.lat<=north:
                    if prepared.covers(Point(n.lon,n.lat)):
                        writer.add_node(n);counts['selected_tagged_nodes']+=1
            def way(self,w):
                coords=[(n.lon,n.lat) for n in w.nodes if n.location.valid()]
                if len(coords)<2:return
                xs,ys=zip(*coords)
                if max(xs)<west or min(xs)>east or max(ys)<south or min(ys)>north:return
                geom=Polygon(coords) if len(coords)>3 and coords[0]==coords[-1] else LineString(coords)
                if not geom.is_valid:geom=geom.buffer(0) if geom.geom_type=='Polygon' else geom
                if not prepared.intersects(geom):return
                writer.add_way(w);selected_ways.add(w.id);counts['selected_ways']+=1
                if 'building' in w.tags or 'building:part' in w.tags:
                    counts['building_or_part_ways_with_context']+=1
                    if city_prepared.intersects(geom):
                        counts['building_or_part_ways_intersecting_city']+=1
                        if 'building' in w.tags: counts['building_outline_ways_intersecting_city']+=1
                        if 'building:part' in w.tags:counts['building_part_ways_intersecting_city']+=1
                        for key in ('height','building:levels','building:material','building:colour','roof:shape','roof:height'):
                            if key in w.tags:tags_count[key]+=1
                        if 'height' in w.tags:
                            try:heights.append(float(w.tags['height'].strip().removesuffix('m').strip()))
                            except ValueError:counts['unparsed_height_tags']+=1
                if counts['selected_ways']%50000==0:print(dict(counts),flush=True)
            def relation(self,r):
                if r.tags.get('type') not in ('multipolygon','building'):return
                if not (r.tags.get('type')=='building' or any(t.k in KEYS for t in r.tags)):return
                if any(m.type=='w' and m.ref in selected_ways for m in r.members):
                    writer.add_relation(r);counts['selected_relations']+=1
        Select().apply_file(str(raw),locations=True,idx='flex_mem',
                            filters=[osmium.filter.KeyFilter(*sorted(KEYS | {'type'}))])
    partial.replace(target)
    # XML is the supported frozen-file interface of the pinned Arnis executable.
    xml=sources/'chicago.osm'
    with osmium.SimpleWriter(str(xml)) as writer:
        osmium.apply(str(target),writer)
    reader=osmium.io.Reader(str(raw)); timestamp=reader.header().get('osmosis_replication_timestamp');reader.close()
    R=6371000.; lat=(south+north)/2
    width=2*R*math.asin(math.cos(math.radians(lat))*math.sin(math.radians((east-west)/2)))
    height=R*math.radians(north-south)
    # Compare default Arnis affine metric against WGS84 geodesics at representative rows.
    geod=Geod(ellps='WGS84'); errors=[]
    for latitude in (south,lat,north):
        _,_,distance=geod.inv(west,latitude,east,latitude)
        errors.append({'latitude':latitude,'east_west_scale_error_percent':100*(math.floor(width)/distance-1)})
    manifest={'status':'sources_prepared','scope':'Chicago municipal boundary + 100m source context',
              'generation_scope':'bounding rectangle containing entire city, including terrain outside city limits',
              'bbox_lat_lon':[south,west,north,east],'city_area_km2':city_m.area/1e6,
              'rectangle_dimensions_blocks':[math.floor(width)+1,math.floor(height)+1],
              'rectangle_area_km2':width*height/1e6,'arnis_scale_setting':1,
              'projection':'Arnis local spherical affine approximation; not surveyed 1:1',
              'east_west_projection_checks':errors,'counts':dict(counts),'tag_coverage_counts':dict(tags_count),
              'max_parsed_height_m':max(heights,default=None),'osm_timestamp':timestamp,
              'raw_sha256':sha256(raw),'extract_sha256':sha256(target),'boundary_sha256':sha256(boundary),
              'xml_sha256':sha256(xml),'ai_used':False,'landmark_overrides':False,
              'sources':[{'name':'OpenStreetMap contributors / Geofabrik','license':'ODbL',
                          'url':'https://download.geofabrik.de/north-america/us/illinois.html',
                          'attribution':'https://www.openstreetmap.org/copyright'},
                         {'name':'City of Chicago boundary','url':'https://gisapps.cityofchicago.org/arcgis/rest/services/CachedMaps/AerialCache/MapServer/0',
                          'license':'City of Chicago terms of use; no public redistribution in this run'}],
              'game_load_verified':False,'building_accuracy_verified':False}
    write_json(STATE/'inventory.json',manifest)
    print(json.dumps(manifest,indent=2),flush=True)

def run(root, pilot):
    guard(root,4*GIB)
    inventory=json.loads((STATE/'inventory.json').read_text())
    name='loop-pilot' if pilot else 'city-draft'
    run_dir=STATE/name; run_dir.mkdir(exist_ok=False)
    bbox=[41.875,-87.642,41.885,-87.629] if pilot else inventory['bbox_lat_lon']
    output=root/'worlds'/name
    if output.exists():raise FileExistsError(output)
    binary=REPO/'vendor/arnis-mac-universal'
    version=subprocess.check_output([str(binary),'--version'],text=True)
    if '3.1.0' not in version:raise RuntimeError('Pinned Arnis 3.1.0 is required')
    args=[str(binary),'--bbox='+','.join(map(str,bbox)),f'--file={root}/sources/chicago.osm',
          f'--output-dir={output}','--scale=1','--projection=local','--mode=geo-terrain',
          '--disable-height-limit','--overture=false','--canopy-height=false','--no-3d',
          '--interior=false','--max-tree-size=small','--signage=none','--map-item=false',
          '--spawn-lat=41.881','--spawn-lng=-87.635','--ground-level=-62']
    env=os.environ.copy();env.update({'RAYON_NUM_THREADS':'4','ARNIS_STREAM_TO_DISK':'1'})
    started=time.time();peak=0;swap_start=psutil.swap_memory().used
    next_size_check=0.0
    record={'status':'running','argv':args,'pid':None,'started_unix':started,'ai_used':False,
            'landmark_overrides':False,'scope':name,'maximum_seconds':1800 if pilot else 24*3600,
            'binary_sha256':sha256(binary),'input_xml_sha256':inventory['xml_sha256'],
            'limits':{'rss_gib':32,'swap_growth_gib':2,'external_project_gib':100},
            'game_load_verified':False,'building_accuracy_verified':False}
    with (run_dir/'generation.log').open('w') as log:
        child=subprocess.Popen(args,env=env,stdout=log,stderr=subprocess.STDOUT)
        # Keep unattended local generation awake; the assertion ends with this child.
        if sys.platform == 'darwin':
            subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(child.pid)],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        record['pid']=child.pid;write_json(run_dir/'status.json',record)
        try:
            while child.poll() is None:
                try:
                    process=psutil.Process(child.pid)
                    rss=sum(p.memory_info().rss for p in [process,*process.children(recursive=True)] if p.is_running())
                except psutil.NoSuchProcess:
                    if child.poll() is not None:break
                    raise
                peak=max(peak,rss);guard(root)
                if rss>32*GIB:raise RuntimeError('32 GiB process memory stop threshold')
                if psutil.swap_memory().used-swap_start>2*GIB:raise RuntimeError('2 GiB system swap-growth threshold')
                if time.time()-started>record['maximum_seconds']:raise RuntimeError('Wall-time limit reached')
                # LaCie use outside the project is untouched; check project size once per minute.
                if time.monotonic() >= next_size_check:
                    size=sum(f.stat().st_size for f in root.rglob('*') if f.is_file())
                    if size>100*GIB:raise RuntimeError('100 GiB external project budget')
                    record['external_project_bytes']=size
                    next_size_check=time.monotonic()+60
                record.update({'elapsed_seconds':round(time.time()-started),'rss_bytes':rss,'peak_rss_bytes':peak})
                write_json(run_dir/'status.json',record);time.sleep(5)
        except Exception as error:
            child.terminate()
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:child.kill();child.wait()
            record.update({'status':'stopped','reason':str(error)})
        else:
            record['status']='generated_unverified' if child.returncode==0 else 'failed'
        record.update({'returncode':child.returncode,'elapsed_seconds':round(time.time()-started,2),'peak_rss_bytes':peak})
        write_json(run_dir/'status.json',record)
    print(json.dumps(record,indent=2),flush=True)
    if record['status']!='generated_unverified':raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','pilot','generate'])
    parser.add_argument('--root',type=Path,required=True,help='Existing external Earthcraft Chicago workspace')
    args=parser.parse_args();STATE.mkdir(parents=True,exist_ok=True)
    if args.action=='prepare':prepare(args.root)
    else:run(args.root,args.action=='pilot')
