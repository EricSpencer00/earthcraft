"""Cook 2022 road-surface observations -> ground material, never new geometry."""
import hashlib
import json
import numpy as np


def road_mask(xyz,labels,withheld,meta,elevation):
    xyz=np.asarray(xyz)
    if xyz.ndim!=2 or xyz.shape[1]!=3 or not np.isfinite(xyz).all():
        raise ValueError('Finite metric point observations required')
    if len(labels)!=len(xyz) or len(withheld)!=len(xyz):raise ValueError('Point field length mismatch')
    size=meta['size']
    if elevation.shape!=(size,size):raise ValueError('Road/terrain grid mismatch')
    x=np.floor(xyz[:,0]-meta['west']).astype(np.int64)
    z=np.floor(meta['north']-xyz[:,1]).astype(np.int64)
    selected=np.flatnonzero((np.asarray(labels)==11)&(np.asarray(withheld)==0)&
                           (x>=0)&(x<size)&(z>=0)&(z<size))
    residual=xyz[selected,2]-elevation[z[selected],x[selected]]
    retained=selected[np.isfinite(residual)&(np.abs(residual)<=1)]
    mask=np.zeros((size,size),bool);mask[z[retained],x[retained]]=True
    return mask,{'road_class_returns':len(selected),'accepted_returns':len(retained),
        'rejected_height_returns':len(selected)-len(retained),'candidate_ground_cells':int(mask.sum()),
        'median_absolute_terrain_residual_m':float(np.median(np.abs(residual))) if len(residual) else None,
        'height_gate_m':1,'consistency_not_independent_accuracy':True}


def load(point_source,meta,elevation,excluded):
    manifest=json.loads((point_source/'manifest.json').read_text())
    # Do not generalize one provider's classification semantics to arbitrary LAS.
    sources=manifest.get('sources') or [manifest.get('source',manifest)]
    if not all(isinstance(s,dict) and s.get('url','').startswith('https://clearinghouse.isgs.illinois.edu/distribute/district1/cook/2022/') for s in sources):
        return None,None
    if manifest['output_horizontal_crs']!=meta['crs']:raise ValueError('Road point CRS mismatch')
    path=point_source/'points.npz'
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=manifest['points_sha256']:raise ValueError('Frozen road points changed')
    with np.load(path) as data:
        mask,report=road_mask(data['xyz'],data['classification'],data['withheld'],meta,elevation)
    mask &= ~excluded
    report.update(source_manifest=manifest,classified_road_cells=int(mask.sum()),
        excluded_building_cells=report['candidate_ground_cells']-int(mask.sum()),
        method='Class 11 road surface, withheld excluded, within 1 m of existing terrain; building footprints excluded',
        geometry_changed=False,llm_used=False,block='gray_concrete',
        material_role='Pavement presentation, not measured colour or exact paving composition',
        classification_evidence='Publisher cook_2022_metadata.xml: LAS class 11 Road Surface',
        limitations=['Point classes and source date can be wrong or stale.',
                     'Metre-cell coverage of observed returns, not a surveyed road boundary.',
                     'No dilation, centreline widths, bridge deck flattening or gap filling.'])
    return mask,report
