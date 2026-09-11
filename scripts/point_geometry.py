"""Observed 3D voxel occupancy; no downward extrusion or gap filling."""
import hashlib
import json
import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import transform
from shapely import contains_xy, make_valid


def voxelize(xyz, meta, offset):
    xyz=np.asarray(xyz, dtype=float)
    if xyz.ndim!=2 or xyz.shape[1]!=3 or not np.isfinite(xyz).all():
        raise ValueError('Finite Nx3 east/north/elevation points required')
    world=np.column_stack((xyz[:,0]-meta['west'],xyz[:,2]+offset,meta['north']-xyz[:,1]))
    cells=np.floor(world).astype(np.int32)
    if len(cells) and ((cells[:,[0,2]]<0).any() or (cells[:,[0,2]]>=meta['size']).any()):
        raise ValueError('Point outside frozen world')
    return np.unique(cells,axis=0)


def load_tower_points(point_source, source, meta, ground, offset):
    manifest=json.loads((point_source/'manifest.json').read_text())
    path=point_source/'points.npz'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['points_sha256']:
        raise ValueError('Frozen measured point checksum changed')
    if manifest['output_horizontal_crs']!=meta['crs'] or (meta['size'],meta['west'],meta['north'])!=(64,-32,33):
        raise ValueError('Point experiment is Water Tower only')
    county=json.loads((source/'cook-buildings-2022.json').read_text())
    feature=next(f for f in county['features'] if f['attributes']['OBJECTID']==833197)
    geometry=None
    for ring in feature['geometry']['rings']:
        polygon=make_valid(Polygon(ring))
        geometry=polygon if geometry is None else geometry.symmetric_difference(polygon)
    project=Transformer.from_crs(4326,meta['crs'],always_xy=True)
    # Association envelope, not generated geometry. Keep the original footprint
    # plus one metre for measured overhangs/registration uncertainty.
    envelope=transform(project.transform,geometry).buffer(1)
    data=np.load(path);xyz=data['xyz']
    selected=contains_xy(envelope,xyz[:,0],xyz[:,1]) & (data['withheld']==0)
    # The actual tower shaft is largely class 1, and cupola class 15.
    # This is explicit spatial/class association, not a universal classifier.
    selected &= np.isin(data['classification'],[1,6,15,19])
    cells=voxelize(xyz[selected],meta,offset)
    cells=cells[cells[:,1]>ground[cells[:,2],cells[:,0]]]
    if not len(cells) or cells[:,1].max()>512 or len(cells)>100_000:
        raise ValueError('Unexpected tower point extent or occupancy')
    report={'method':'floor observed east/elevation/south coordinates into one-metre voxels',
        'source_manifest':manifest, 'associated_county_objectid':833197,
        'association_envelope':'County footprint buffered 1 m; this is not a facade truth mask',
        'retained_classes':[1,6,15,19], 'selected_points':int(selected.sum()),
        'occupied_voxels':len(cells),'interpolation':False,'extrusion':False,'gap_filling':False,
        'material':'Unchanged neutral stone-brick A/B palette; not measured colour',
        'limitations':['Unknown gaps remain empty, not proof of actual openings.',
            'Unclassified returns inside the association envelope can include clutter.',
            'One-metre voxels cannot exactly represent sub-metre ornament.',
            'Source point coverage is not independent accuracy certification.']}
    return cells,report
