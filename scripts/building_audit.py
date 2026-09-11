"""Repeatable per-building evidence analysis, not a universal accuracy certificate.

Reports every building intersecting the requested source grid, including failures
and missing layers. It does not promote sparse points into complete facades.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from affine import Affine
from pyproj import Transformer
from rasterio.features import rasterize
from shapely import make_valid, contains_xy
from shapely.geometry import Polygon, shape, box, mapping
from shapely.ops import transform


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def county_objects(source, meta):
    project=Transformer.from_crs(4326,meta['crs'],always_xy=True)
    objects=[]
    for feature in json.loads((source/'cook-buildings-2022.json').read_text())['features']:
        geometry=None
        for ring in feature['geometry']['rings']:
            polygon=make_valid(Polygon(ring))
            geometry=polygon if geometry is None else geometry.symmetric_difference(polygon)
        attrs=feature['attributes']
        objects.append({'id':f'cook-2022-{attrs["OBJECTID"]}',
            'geometry':transform(project.transform,geometry),
            'ground_m':attrs['Ground_Z']*(1200/3937),
            'height_m':attrs['Height']*(1200/3937),
            'height_role':'Provider maximum-minus-ground attribute, not complete 3D shape',
            'source_id':attrs['OBJECTID'],'capture_year':2022})
    return objects


def objects_from_geojson(path, meta):
    """Portable input boundary for future municipal/OSM/Overture adapters."""
    project=Transformer.from_crs(4326,meta['crs'],always_xy=True)
    result=[];seen=set()
    for feature in json.loads(path.read_text())['features']:
        props=feature['properties'];identifier=str(feature.get('id',props.get('id','')))
        if not identifier or identifier in seen:raise ValueError('Unique stable building IDs required')
        seen.add(identifier)
        result.append({'id':identifier,'geometry':transform(project.transform,make_valid(shape(feature['geometry']))),
            'ground_m':props.get('ground_m'),'height_m':props.get('height_m'),
            'height_role':props.get('height_role','unknown'),'capture_year':props.get('capture_year'),
            'source_id':props.get('source_id',identifier)})
    return result


def footprint_matches(geometry, alternatives):
    results=[]
    for other in alternatives:
        if not geometry.intersects(other['geometry']):continue
        intersection=geometry.intersection(other['geometry']).area
        union=geometry.union(other['geometry']).area
        if union and intersection/union>.05:
            results.append({'osm_way':other['id'],'intersection_over_union':intersection/union,
                'osm_area_m2':other['geometry'].area,'name':other['tags'].get('name'),
                'tags':other['tags'],'independent_accuracy_reference':False})
    return sorted(results,key=lambda r:-r['intersection_over_union'])[:3]


def point_metrics(geometry, ground_m, data, coverage, reference_height_m=None):
    fraction=float(np.clip(geometry.intersection(coverage).area/geometry.area,0,1))
    result={'available_footprint_fraction':fraction,'selected_returns':0,
        'selection':'Footprint + 1 m, non-withheld; class statistics retained separately',
        'complete_facade_observation':False}
    if fraction==0:return result
    xyz=data['xyz'];envelope=geometry.buffer(1)
    a,b,c,d=envelope.bounds
    possible=np.flatnonzero((xyz[:,0]>=a)&(xyz[:,0]<=c)&(xyz[:,1]>=b)&(xyz[:,1]<=d)&(data['withheld']==0))
    indices=possible[contains_xy(envelope,xyz[possible,0],xyz[possible,1])]
    result['selected_returns']=len(indices)
    classes,counts=np.unique(data['classification'][indices],return_counts=True)
    result['classes']=dict(zip(map(str,classes),map(int,counts)))
    if ground_m is None:return result
    valid=indices[np.isin(data['classification'][indices],[1,6,15,19]) & (xyz[indices,2]>ground_m+1)]
    result['candidate_above_ground_returns']=len(valid)
    result['candidate_policy']='Spatial association of classes 1/6/15/19; not universal building classification or automatic admission'
    if not len(valid):return result
    result['candidate_height_quantiles_m']=np.quantile(xyz[valid,2]-ground_m,[0,.5,.95,1]).tolist()
    core=contains_xy(geometry,xyz[valid,0],xyz[valid,1])
    heights=xyz[valid,2]-ground_m
    result['core_candidate_max_height_m']=float(heights[core].max()) if core.any() else None
    result['context_ring_candidate_max_height_m']=float(heights[~core].max()) if (~core).any() else None
    result['core_candidate_returns']=int(core.sum())
    result['association_caveat']='Context ring can contain neighbouring tall facades; do not assign ring maximum as this building height'
    result['candidate_class_max_heights_m']={str(label):float(heights[data['classification'][valid]==label].max())
        for label in np.unique(data['classification'][valid])}
    if reference_height_m is not None and '6' in result['candidate_class_max_heights_m']:
        result['class6_max_minus_provider_height_m']=result['candidate_class_max_heights_m']['6']-reference_height_m
        result['class6_comparison_role']='Lineage/classification diagnostic; agreement is not independent building-height accuracy'
    # Geographic grid origin is integral in this chart, preserving one-metre
    # occupancy. Split whole source strips, never random points from the same strip.
    strips=np.unique(data['point_source_id'][valid]);split=len(strips)//2
    if split:
        training_strips=strips[:split];heldout_strips=strips[split:]
        cells=np.floor(xyz[valid]*[1,-1,1]).astype(np.int64)
        train=np.isin(data['point_source_id'][valid],training_strips)
        training=set(map(tuple,cells[train]));heldout=set(map(tuple,cells[~train]))
        result['strip_holdout']={'training_strips':training_strips.tolist(),'heldout_strips':heldout_strips.tolist(),
            'heldout_cells':len(heldout),'covered_by_training_cells':len(training&heldout),
            'coverage_ratio':len(training&heldout)/len(heldout),
            'interpretation':'Acquisition-strip consistency, not independent physical accuracy'}
        if reference_height_m is not None:
            high=core&(heights>reference_height_m+10)
            high_training=set(map(tuple,cells[train&high]));high_heldout=set(map(tuple,cells[~train&high]))
            result['strip_holdout']['height_conflicting_core_cells']={
                'threshold_m':reference_height_m+10,'heldout_cells':len(high_heldout),
                'covered_by_training_cells':len(high_training&high_heldout),
                'coverage_ratio':len(high_training&high_heldout)/len(high_heldout) if high_heldout else None,
                'interpretation':'Checks whether high returns repeat across source groups; does not resolve physical identity or certify accuracy'}
    return result


def run(source, output, surfaces=None, points=None, point_grid=None, features=None):
    source,output=Path(source),Path(output)
    if output.exists():raise FileExistsError(output)
    meta=json.loads((source/'sources.json').read_text())
    if not isinstance(meta['size'],int) or not 16<=meta['size']<=1024:
        raise ValueError('Bounded audit grid required')
    extent=box(meta['west'],meta['north']-meta['size'],meta['west']+meta['size'],meta['north'])
    vector=Path(features) if features else source/'cook-buildings-2022.json'
    objects=objects_from_geojson(vector,meta) if features else county_objects(source,meta)
    if len(objects)>1000:raise ValueError('At most 1,000 source objects per audit batch')
    alternatives=[];project=Transformer.from_crs(4326,meta['crs'],always_xy=True)
    for way in json.loads((source/'osm-ways.json').read_text()):
        if way['closed'] and 'building' in way['tags']:
            alternatives.append({**way,'geometry':transform(project.transform,make_valid(Polygon(way['coordinates'])))})
    dsm=dtm=point_data=coverage=None
    inputs={str(p.resolve()):sha(p) for p in [source/'sources.json',vector,source/'osm-ways.json']}
    if surfaces:
        surfaces=Path(surfaces);probe=json.loads((surfaces/'probe.json').read_text())
        if probe['grid']!={k:meta[k] for k in ('crs','west','north','size')}:raise ValueError('DSM grid mismatch')
        data=np.load(surfaces/'metric-surfaces.npz');dsm,dtm=data['dsm'],data['dtm']
        if dsm.shape!=(meta['size'],meta['size']) or dtm.shape!=dsm.shape:raise ValueError('DSM dimensions mismatch')
        inputs.update({str(p.resolve()):sha(p) for p in [surfaces/'probe.json',surfaces/'metric-surfaces.npz']})
    if points:
        points=Path(points)
        manifest=json.loads((points/'manifest.json').read_text())
        if manifest['output_horizontal_crs']!=meta['crs']:raise ValueError('Point CRS mismatch')
        if sha(points/'points.npz')!=manifest['points_sha256']:raise ValueError('Point checksum mismatch')
        if 'coverage_geometry' in manifest:
            coverage=shape(manifest['coverage_geometry'])
        else:
            if not point_grid:raise ValueError('Explicit acquisition crop required; point bounding box is not coverage')
            point_grid=Path(point_grid);crop=json.loads(point_grid.read_text())
            if crop['crs']!=meta['crs']:raise ValueError('Point crop CRS mismatch')
            coverage=box(crop['west'],crop['north']-crop['size'],crop['west']+crop['size'],crop['north'])
            inputs[str(point_grid.resolve())]=sha(point_grid)
        # NpzFile is lazy: loading once avoids decompressing a large point crop
        # again for every building and every queried field.
        with np.load(points/'points.npz') as arrays:
            point_data={key:arrays[key] for key in ('xyz','withheld','classification','point_source_id')}
        inputs.update({str(p.resolve()):sha(p) for p in [points/'manifest.json',points/'points.npz']})
    output.mkdir(parents=True);records=[]
    for obj in objects:
        geometry=obj['geometry']
        if geometry.area<=0 or not geometry.intersects(extent):continue
        if geometry.intersection(extent).area==0:continue
        record={k:v for k,v in obj.items() if k!='geometry'}
        record.update(footprint_area_m2=geometry.area,footprint_bounds_m=list(geometry.bounds),
            footprint_fraction_in_audit=geometry.intersection(extent).area/geometry.area,
            footprint_geometry=mapping(geometry),crs=meta['crs'],
            osm_matches=footprint_matches(geometry,alternatives),
            state='review_required',independent_accuracy_verified=False,
            facade_images='unavailable',material='unknown unless supported by matched source tags',
            next_actions=['Acquire or register permitted facade observations','Check independent geometry controls'])
        record['object_identity']='Provider footprint feature; may be a roof part or podium, not necessarily one whole physical building'
        if dsm is not None:
            mask=rasterize([(geometry,1)],out_shape=dsm.shape,
                transform=Affine(1,0,meta['west'],0,-1,meta['north'])).astype(bool)
            valid=mask&np.isfinite(dsm)&np.isfinite(dtm)
            heights=dsm[valid]-dtm[valid]
            record['surface_evidence']={'footprint_cells':int(mask.sum()),'valid_cells':int(valid.sum()),
                'height_quantiles_m':np.quantile(heights,[0,.5,.95,1]).tolist() if heights.size else None,
                'role':'DSM-DTM roof/canopy heightfield, not observed vertical facade',
                'independent_accuracy_reference':False}
        if point_data is not None:
            record['point_evidence']=point_metrics(geometry,obj['ground_m'],point_data,coverage,obj['height_m'])
            pe=record['point_evidence'];core=pe.get('core_candidate_max_height_m');ring=pe.get('context_ring_candidate_max_height_m')
            record['height_conflict_flags']=[]
            if core is not None and ring is not None and ring>core+10:
                record['height_conflict_flags'].append('tall_context_returns_not_core_geometry')
            if core is not None and obj['height_m'] is not None and abs(core-obj['height_m'])>10:
                record['height_conflict_flags'].append('core_returns_disagree_with_provider_feature_height')
            if record['height_conflict_flags']:
                record['next_actions'].insert(0,'Resolve feature/roof-part identity and neighbour association before assigning a whole-building height')
        fraction=record.get('point_evidence',{}).get('available_footprint_fraction',0)
        if fraction<.999:
            record['next_actions'].insert(0,'Acquire missing original 3D observations; do not extrude roof maxima')
        record['geometry_route']='3d_candidate_requires_review' if fraction>=.999 else 'insufficient_3d_coverage'
        records.append(record)
        filename=hashlib.sha256(obj['id'].encode()).hexdigest()[:20]+'.json'
        (output/filename).write_text(json.dumps(record,indent=2))
        record['report_file']=filename
    summary={'buildings_considered':len(records),'count_unit':'Source footprint features, not deduplicated physical buildings',
        'complete_point_crop_coverage':sum(r['geometry_route']=='3d_candidate_requires_review' for r in records),
        'independently_accuracy_verified':0,'per_building_inference':'none; deterministic local geometry/statistics',
        'inputs':inputs,'all_missing_buildings_retained_in_denominator':True,
        'records':[{k:r[k] for k in ('id','report_file','geometry_route','footprint_area_m2','next_actions')} for r in records],
        'scope':'This bounded batch only; not all Earth buildings or verified global coverage'}
    (output/'index.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('records','inputs')},indent=2))
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--surfaces',type=Path);p.add_argument('--points',type=Path);p.add_argument('--point-grid',type=Path)
    p.add_argument('--features',type=Path,help='Optional WGS84 GeoJSON with stable IDs; replaces county adapter')
    a=p.parse_args();run(a.source,a.output,a.surfaces,a.points,a.point_grid,a.features)
