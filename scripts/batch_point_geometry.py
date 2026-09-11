"""Provider-scoped 3D building candidates, with explicit acquisition coverage."""
import hashlib
import json
import numpy as np
from shapely import contains_xy
from shapely.geometry import shape,box
from building_audit import county_objects
from point_geometry import voxelize


def load_batch_points(point_source,source,meta,ground,offset):
    manifest=json.loads((point_source/'manifest.json').read_text())
    if 'coverage_geometry' not in manifest:raise ValueError('Explicit point acquisition coverage required')
    if manifest['output_horizontal_crs']!=meta['crs']:raise ValueError('Point/world CRS mismatch')
    path=point_source/'points.npz'
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=manifest['points_sha256']:raise ValueError('Point-source checksum mismatch')
    if meta['size']>512:raise ValueError('First multi-building export capped at 512 m')
    coverage=shape(manifest['coverage_geometry'])
    extent=box(meta['west'],meta['north']-meta['size'],meta['west']+meta['size'],meta['north'])
    if extent.intersection(coverage).area/extent.area<.999999:
        raise ValueError('Acquire missing original point tiles before exporting this extent')
    with np.load(path) as arrays:
        xyz=arrays['xyz'];labels=arrays['classification'];withheld=arrays['withheld']
    # Explicit Cook-provider candidate policy. It remains uncalibrated outside
    # this source and is not represented as a global classifier.
    possible=np.flatnonzero((xyz[:,0]>=meta['west'])&(xyz[:,0]<meta['west']+meta['size'])&
        (xyz[:,1]>meta['north']-meta['size'])&(xyz[:,1]<=meta['north'])&
        (withheld==0)&np.isin(labels,[1,6,15,19]))
    xyz=xyz[possible];owners=np.zeros(len(xyz),np.uint16);records=[]
    for obj in county_objects(source,meta):
        geometry=obj['geometry']
        if geometry.intersection(extent).area<=0:continue
        envelope=geometry.buffer(1)
        a,b,c,d=envelope.bounds
        candidates=np.flatnonzero((xyz[:,0]>=a)&(xyz[:,0]<=c)&(xyz[:,1]>=b)&(xyz[:,1]<=d))
        selected=candidates[contains_xy(envelope,xyz[candidates,0],xyz[candidates,1])]
        owners[selected]+=1
        record={'id':obj['id'],'associated_returns':len(selected),
            'footprint_fraction_in_export':geometry.intersection(extent).area/geometry.area,
            'independently_verified':False}
        if len(selected):
            maximum=float(xyz[selected,2].max()-obj['ground_m'])
            record.update(observed_candidate_max_height_m=maximum,provider_height_m=obj['height_m'],
                height_conflict=abs(maximum-obj['height_m'])>10)
        records.append(record)
    cells=voxelize(xyz[owners>0],meta,offset)
    cells=cells[cells[:,1]>ground[cells[:,2],cells[:,0]]]
    if len(cells)>2_000_000 or (not len(cells) and records):raise ValueError('Unexpected occupied-cell count')
    report={'method':'Direct observed 3D voxel occupancy, no vertical extrusion or gap filling',
        'source_manifest':manifest,'buildings':records,'retained_classes':[1,6,15,19],
        'association_envelope_m':1,'ambiguous_association_returns':int((owners>1).sum()),
        'occupied_voxels':len(cells),'interpolation':False,'extrusion':False,
        'source_accuracy_verified':False,'llm_used':False,
        'limitations':['Cook-specific class/spatial association is a candidate, not a universal classifier.',
            'Unknown surface gaps remain empty; missing returns do not establish actual openings.',
            'Unclassified clutter can survive association; conflicting heights need independent review.',
            'Overlapping association envelopes do not duplicate occupied blocks; semantic ownership can remain ambiguous.',
            'Facade appearance and metre-grid detail are not independently verified.']}
    return cells,report
