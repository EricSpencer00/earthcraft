"""Cache one original public LAS tile on the bulk volume, then crop it for batch QA.

The acquisition is intentionally the already identified Cook tile, not a county
download. Caching the original avoids re-downloading it for every building.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import laspy
import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon,box,mapping
from http_zip_range import RangeReader
from local_paths import bulk_path,bulk_root

ROOT=Path(__file__).resolve().parents[1]
BULK=bulk_path('chicago','lidar-2022')
URL='https://clearinghouse.isgs.illinois.edu/distribute/district1/cook/2022/cook-las5.zip'
MEMBER='cook-las5/17509050.las'


def cache_tile():
    volume=bulk_root()
    if not volume.exists():raise ValueError('Bulk volume unavailable; no internal fallback')
    if shutil.disk_usage(volume).free<105*1024**3:raise ValueError('Preserve external free-space reserve')
    BULK.mkdir(parents=True,exist_ok=True)
    path=BULK/'17509050.las';manifest=path.with_suffix('.json')
    if path.exists() or manifest.exists():
        record=json.loads(manifest.read_text())
        with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=record['sha256']:raise ValueError('Cached original LAS checksum mismatch')
        return path,record
    partial=path.with_suffix('.las.part')
    if partial.exists():raise ValueError('Incomplete previous tile preserved; inspect before retrying')
    remote=RangeReader(URL,budget=1200*1024**2);digest=hashlib.sha256();written=0
    with zipfile.ZipFile(remote) as archive:
        info=archive.getinfo(MEMBER)
        if info.file_size>2*1024**3 or info.compress_size>1150*1024**2:raise ValueError('Tile budget exceeded')
        with archive.open(info) as source,partial.open('xb') as target:
            while block:=source.read(8*1024**2):
                target.write(block);digest.update(block);written+=len(block)
                if written//(128*1024**2)!=(written-len(block))//(128*1024**2):
                    print(f'Original tile cached: {written//1024**2} MiB; transferred {remote.transferred//1024**2} MiB',flush=True)
        if written!=info.file_size:raise ValueError('Incomplete original LAS')
    partial.rename(path)
    record={'url':URL,'member':MEMBER,'archive_etag':remote.etag,'bytes':written,
        'sha256':digest.hexdigest(),'zip_crc32_verified':f'{info.CRC:08x}',
        'network_bytes':remote.transferred,'capture_interval':['2022-04-05','2022-06-29'],
        'retrieved_utc':datetime.now(timezone.utc).isoformat(),
        'license':'No access or use restrictions in publisher metadata',
        'metadata_file':str(ROOT/'runs/water-tower-reference/cook_TileIndex_metadata.zip'),
        'metadata_sha256':hashlib.sha256((ROOT/'runs/water-tower-reference/cook_TileIndex_metadata.zip').read_bytes()).hexdigest(),
        'source_page':'https://clearinghouse.isgs.illinois.edu/node/1879'}
    manifest.write_text(json.dumps(record,indent=2))
    return path,record


def crop(path,record,source,output):
    if output.exists():raise FileExistsError(output)
    meta=json.loads((source/'sources.json').read_text())
    if meta['size']>512:raise ValueError('First batch capped at 512 m')
    native=Transformer.from_crs(meta['crs'],6455,always_xy=True)
    project=Transformer.from_crs(6455,meta['crs'],always_xy=True)
    west,north,size=meta['west'],meta['north'],meta['size']
    ex,ny=native.transform([west,west+size,west,west+size],[north,north,north-size,north-size])
    bounds=[min(ex)-1,min(ny)-1,max(ex)+1,max(ny)+1];kept=[];count=0
    with laspy.open(path,read_evlrs=False) as reader:
        np.testing.assert_allclose(reader.header.mins[:2],[1175000,1905000],atol=.01,rtol=0)
        np.testing.assert_allclose(reader.header.maxs[:2],[1177500,1907500],atol=.01,rtol=0)
        for points in reader.chunk_iterator(400_000):
            x,y=np.asarray(points.x),np.asarray(points.y)
            selected=(x>=bounds[0])&(x<=bounds[2])&(y>=bounds[1])&(y<=bounds[3])
            if selected.any():
                xx,yy=project.transform(x[selected],y[selected]);p=points[selected]
                valid=(xx>=west)&(xx<west+size)&(yy>north-size)&(yy<=north)
                p=p[valid]
                kept.append({'xyz':np.column_stack((xx[valid],yy[valid],np.asarray(p.z)*(1200/3937))),
                    'classification':np.asarray(p.classification),'withheld':np.asarray(p.withheld),
                    'point_source_id':np.asarray(p.point_source_id),'intensity':np.asarray(p.intensity),
                    'return_number':np.asarray(p.return_number),'gps_time':np.asarray(p.gps_time)})
            count+=len(points)
            if count%8_000_000==0:print(f'{count} original returns inspected for batch crop',flush=True)
    if not kept:raise ValueError('No point observations in audit area')
    data={key:np.concatenate([chunk[key] for chunk in kept]) for key in kept[0]}
    # Acquisition coverage is the indexed tile polygon, not an assumed full AOI.
    edge=np.linspace(0,1,33);tx=np.r_[1175000+2500*edge,np.full(33,1177500),1177500-2500*edge,np.full(33,1175000)]
    ty=np.r_[np.full(33,1905000),1905000+2500*edge,np.full(33,1907500),1907500-2500*edge]
    px,py=project.transform(tx,ty)
    coverage=Polygon(zip(px,py)).intersection(box(west,north-size,west+size,north))
    output.mkdir(parents=True)
    np.savez_compressed(output/'points.npz',**data)
    labels,counts=np.unique(data['classification'],return_counts=True)
    report={'source':record,'source_las':str(path),'output_horizontal_crs':meta['crs'],
        'coverage_geometry':mapping(coverage),'coverage_role':'Actual indexed acquisition tile clipped to AOI; eastern missing area is not filled',
        'aoi_fraction_covered':coverage.area/size**2,'crop_point_count':len(data['xyz']),
        'class_counts':dict(zip(map(str,labels),map(int,counts))),
        'native_horizontal_crs':'EPSG:6455 from publisher XML/index; LAS has no header CRS',
        'vertical_datum':'NAVD88, Geoid18, US survey feet normalized by 1200/3937',
        'points_sha256':hashlib.sha256((output/'points.npz').read_bytes()).hexdigest(),
        'inference_used':False,'interpolation':False,'original_preserved':True}
    (output/'manifest.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('coverage_geometry','source','output_horizontal_crs')},indent=2))


if __name__=='__main__':
    path,record=cache_tile()
    crop(path,record,ROOT/'runs/metric-water-tower-local',BULK/'chicago-batch-512')
