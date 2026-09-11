"""Stream one public Cook LAS tile; retain only the frozen Water Tower 64 m crop.

The ZIP directory identifies one 1.1 GB compressed member. No county archive or
full uncompressed tile is stored. All coordinates/classes in the crop survive.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import laspy
import numpy as np
from pyproj import CRS, Transformer
from http_zip_range import RangeReader

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://clearinghouse.isgs.illinois.edu/distribute/district1/cook/2022/cook-las5.zip'
MEMBER = 'cook-las5/17509050.las'


def main():
    out = ROOT/'runs/water-tower-points-2022'
    if out.exists() and any(out.iterdir()): raise FileExistsError(out)
    if shutil.disk_usage(ROOT).free < 22*1024**3:
        raise ValueError('Preserve 20 GiB reserve plus 2 GiB working allowance')
    meta = json.loads((ROOT/'runs/water-tower-focus-64/sources.json').read_text())
    if (meta['west'], meta['north'], meta['size']) != (-32,33,64):
        raise ValueError('Only frozen Water Tower focus is authorized')
    native = Transformer.from_crs(meta['crs'], 6455, always_xy=True)
    east, north = native.transform([-32,32,-32,32],[-31,-31,33,33])
    bounds = [min(east)-1,min(north)-1,max(east)+1,max(north)+1]
    project = Transformer.from_crs(6455, meta['crs'], always_xy=True)
    remote = RangeReader(URL, budget=1200*1024**2)
    kept, processed = [], 0
    out.mkdir(exist_ok=True)
    with zipfile.ZipFile(remote) as archive:
        info = archive.getinfo(MEMBER)
        if info.file_size > 2*1024**3 or info.compress_size > 1150*1024**2:
            raise ValueError('Single tile budget exceeded')
        with archive.open(info) as stream:
            with laspy.open(stream, read_evlrs=False, closefd=False) as reader:
                header = reader.header
                crs = header.parse_crs()
                crs_origin = 'LAS header'
                if crs is None:
                    # This delivered tile has no VLR CRS. The publisher's paired
                    # metadata and tile index explicitly specify EPSG:6455.
                    np.testing.assert_allclose(header.mins[:2], [1175000,1905000], atol=.01, rtol=0)
                    np.testing.assert_allclose(header.maxs[:2], [1177500,1907500], atol=.01, rtol=0)
                    crs = CRS.from_epsg(6455)
                    crs_origin = 'Publisher paired XML + tile index; LAS header has no CRS; tile XY bounds cross-checked'
                if crs.to_2d().to_epsg() != 6455:
                    raise ValueError('LAS horizontal CRS does not match tile index')
                print(json.dumps({'header_crs':crs.to_wkt(), 'points':header.point_count,
                    'point_format':header.point_format.id,'bounds':list(header.mins)+list(header.maxs)}), flush=True)
                for points in reader.chunk_iterator(400_000):
                    xs, ys = np.asarray(points.x), np.asarray(points.y)
                    select = (xs>=bounds[0])&(xs<=bounds[2])&(ys>=bounds[1])&(ys<=bounds[3])
                    if select.any():
                        x, y = project.transform(xs[select],ys[select])
                        valid = (x>=-32)&(x<32)&(y>-31)&(y<=33)
                        selected = points[select][valid]
                        kept.append({'xyz':np.column_stack((x[valid],y[valid],np.asarray(selected.z)*(1200/3937))),
                            'classification':np.asarray(selected.classification),
                            'withheld':np.asarray(selected.withheld),
                            'intensity':np.asarray(selected.intensity),
                            'return_number':np.asarray(selected.return_number),
                            'point_source_id':np.asarray(selected.point_source_id),
                            'gps_time':np.asarray(selected.gps_time)})
                    processed += len(points)
                    if processed % 4_000_000 == 0:
                        print(f'{processed}/{header.point_count} tile points scanned; {remote.transferred//1024**2} MiB transferred',flush=True)
                # Drain any extended records so ZipExtFile validates the member CRC.
                while stream.read(1024**2): pass
                if processed != header.point_count: raise ValueError('Incomplete LAS')
    if not kept: raise ValueError('No Water Tower points')
    data = {key:np.concatenate([part[key] for part in kept]) for key in kept[0]}
    np.savez_compressed(out/'points.npz', **data)
    labels, counts = np.unique(data['classification'], return_counts=True)
    report = {'source_page':'https://clearinghouse.isgs.illinois.edu/node/1879',
        'url':URL, 'archive_etag':remote.etag, 'member':MEMBER,
        'member_crc32_verified':f'{info.CRC:08x}', 'member_uncompressed_bytes':info.file_size,
        'network_bytes':remote.transferred, 'source_point_count':processed,
        'crop_point_count':len(data['xyz']), 'class_counts':dict(zip(map(str,labels),map(int,counts))),
        'native_crs_wkt':crs.to_wkt(), 'crs_evidence':crs_origin, 'output_horizontal_crs':meta['crs'],
        'vertical_datum':'NAVD88, Geoid18; source feet normalized using 1200/3937',
        'capture_interval':['2022-04-05','2022-06-29'],
        'retrieved_utc':datetime.now(timezone.utc).isoformat(),
        'metadata':'runs/water-tower-reference/cook_TileIndex_metadata.zip',
        'access_and_use_constraints':'None, per publisher metadata',
        'points_sha256':hashlib.sha256((out/'points.npz').read_bytes()).hexdigest(),
        'scope':'64 m Water Tower crop only; no interpolation, completion or geographic scaling',
        'limitations':['Airborne points do not establish coverage of unseen surfaces.',
            'Publisher XML lists class 6 as high vegetation despite LAS standard building class; inspect before use.',
            'No per-building accuracy certification. Ground checkpoint specification is not facade accuracy.'],
        'llm_used':False, 'world_modified':False}
    (out/'manifest.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
