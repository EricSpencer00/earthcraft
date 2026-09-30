"""Frozen regional Overture footprint index; footprint provenance is not LiDAR.

The 2026-09-23.1 buildings release supplements incomplete OSM outlines. The
regional crop is acquired on a compute host, stored as a metre R-tree, then
queried locally for each scan tile. Height/roof geometry still requires a valid
paired measured DSM; the ML/OSM footprints never become measured points.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from pyproj import Transformer
from shapely.geometry import box,shape,mapping
from shapely.ops import transform
from shapely import from_wkb,to_wkb

from region_expansion import atomic,sha

RELEASE='2026-09-23.1'
SOURCE='s3://overturemaps-us-west-2/release/'+RELEASE+'/theme=buildings/type=building/*'


def acquire(frame,bounds,destination,memory_mb=512,threads=2):
    import duckdb
    if not 256<=memory_mb<=8192 or not 1<=threads<=4:raise ValueError('Bounded footprint acquisition compute required')
    destination=Path(destination)
    if destination.exists():raise ValueError('Never replace a frozen regional footprint index')
    inverse=Transformer.from_crs(frame['crs'],4326,always_xy=True)
    geographic=transform(inverse.transform,box(*bounds).segmentize(5000));west,south,east,north=geographic.bounds
    project=Transformer.from_crs(4326,frame['crs'],always_xy=True)
    query=duckdb.connect();query.execute(f"SET memory_limit='{memory_mb}MB'; SET threads={threads}; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'")
    query.execute('''SELECT id,geometry,sources FROM read_parquet(?,hive_partitioning=true)
        WHERE bbox.xmin<=? AND bbox.xmax>=? AND bbox.ymin<=? AND bbox.ymax>=?''',[SOURCE,east,west,north,south])
    staging=destination.with_suffix('.building');db=sqlite3.connect(staging)
    db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE buildings(rowid INTEGER PRIMARY KEY,id TEXT UNIQUE,geometry BLOB,source TEXT); CREATE VIRTUAL TABLE bounds USING rtree(rowid,minx,maxx,miny,maxy);')
    count=0;original=hashlib.sha256();start=time.time()
    try:
        while rows:=query.fetchmany(10000):
            for identifier,wkb,sources in rows:
                geometry=from_wkb(bytes(wkb))
                if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in ('Polygon','MultiPolygon'):continue
                original.update(identifier.encode());original.update(bytes(wkb))
                metric=transform(project.transform,geometry);left,bottom,right,top=metric.bounds;count+=1
                cursor=db.execute('INSERT INTO buildings(id,geometry,source) VALUES(?,?,?)',
                    (identifier,to_wkb(metric),json.dumps(sources)))
                db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(cursor.lastrowid,left,right,bottom,top))
            db.commit()
        if not 1<=count<=8_000_000:raise ValueError('Regional footprint crop count exceeds bounds')
        record={'schema':'earthcraft-regional-building-footprints-v1','release':RELEASE,'source':SOURCE,
            'crs':frame['crs'],'geographic_bounds':[west,south,east,north],'metric_bounds':bounds,
            'count':count,'original_geometry_digest':original.hexdigest(),'geometry_measured':False,
            'license':'ODbL 1.0; Overture Maps Foundation and original source attribution retained per feature',
            'footprint_role':'Building association; includes OSM and ML-derived roofprints',
            'seconds':time.time()-start,'duckdb_version':duckdb.__version__}
        db.execute('INSERT INTO meta VALUES(?,?)',('source',json.dumps(record)));db.commit()
    finally:db.close();query.close()
    staging.rename(destination);record['sha256']=sha(destination)
    atomic(destination.with_suffix('.json'),json.dumps(record,indent=2).encode());return record


def crop(index,grid,destination):
    index=Path(index);destination=Path(destination)
    # Frozen file verified once when configuring each worker, not for every tile.
    db=sqlite3.connect('file:'+str(index.resolve())+'?mode=ro',uri=True)
    try:
        record=json.loads(db.execute("SELECT value FROM meta WHERE key='source'").fetchone()[0])
        if record['crs']!=grid['crs']:raise ValueError('Footprint index uses another world CRS')
        west,north,size=grid['west'],grid['north'],grid['size'];extent=box(west,north-size,west+size,north)
        rows=db.execute('''SELECT b.id,b.geometry,b.source FROM buildings b JOIN bounds r ON b.rowid=r.rowid
            WHERE r.minx<=? AND r.maxx>=? AND r.miny<=? AND r.maxy>=? ORDER BY b.id''',(west+size,west,north,north-size))
        features=[]
        for identifier,wkb,sources in rows:
            geometry=from_wkb(wkb)
            if geometry.intersects(extent):features.append({'type':'Feature','id':identifier,'properties':{'sources':json.loads(sources)},'geometry':mapping(geometry)})
        if len(features)>10000:raise ValueError('Footprint tile exceeds feature budget')
        result={'type':'FeatureCollection','features':features,'source':record,
            'grid':{key:grid[key] for key in ('crs','west','north','size')},'coordinate_reference':'Existing world metre CRS'}
        atomic(destination,json.dumps(result).encode());return {'count':len(features),'sha256':sha(destination),
            'release':record['release'],'source':record['source'],'license':record['license'],'geometry_measured':False}
    finally:db.close()


def load_shapes(source,grid):
    source=Path(source);path=source/'scan-footprints.geojson'
    receipt=grid.get('scan_footprint_receipt')
    if receipt is None:
        if path.exists():raise ValueError('Supplemental footprints require a source receipt')
        return []
    if sha(path)!=receipt['sha256']:raise ValueError('Frozen supplemental footprints changed')
    doc=json.loads(path.read_text())
    if doc['grid']!={key:grid[key] for key in ('crs','west','north','size')} or len(doc['features'])!=receipt['count']:
        raise ValueError('Supplemental footprints use another tile grid')
    return [(feature['id'],shape(feature['geometry'])) for feature in doc['features']]


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frame',type=Path,required=True)
    p.add_argument('--bounds',nargs=4,type=float,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--memory-mb',type=int,default=512);p.add_argument('--threads',type=int,default=2)
    a=p.parse_args();print(json.dumps(acquire(json.loads(a.frame.read_text()),a.bounds,a.output,a.memory_mb,a.threads)),flush=True)
