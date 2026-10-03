"""Stage measured appearance through the existing building_delta CAS codec.

Never writes a save or a watched inbox. Unregistered street-photo/Orthofacade
candidates remain candidates. This first importer uses whole solid blocks:
different observations on one voxel must agree, because a vanilla block cannot
have four independently colored walls. Textured display panels are not emitted.
"""
import argparse
from collections import Counter, defaultdict
import io
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image
from shapely.geometry import Point, shape

from facade_pilot import canonical_sha
from facade_world import BlockReader, EMPTY, MATERIALS
from live_city import publish, validate
from playable_acceptance_snapshot import sha
from playable_world_acceptance import verify_snapshot


NORMALS = {'west':(-1,0,0), 'east':(1,0,0), 'north':(0,0,-1), 'south':(0,0,1)}
MAX_OBSERVATIONS = 50_000


def array(value, shape_expected):
    result = np.asarray(value, dtype=float)
    if result.shape != shape_expected or not np.isfinite(result).all():
        raise ValueError('Invalid finite measured coordinates')
    return result


def registration_check(candidate, registration, frame):
    """Recompute held-out errors; a reported pass flag alone is insufficient."""
    if candidate.get('schema') != 'earthcraft.registered-facade-observations.v1':
        raise ValueError('Source adapter has not supplied registered facade observations')
    if registration.get('candidate_sha256') != canonical_sha(candidate) or registration.get('frame_sha256') != canonical_sha(frame):
        raise ValueError('Registration is not bound to these observations and the inherited frame')
    transform = array(registration['world_from_source'], (4,4))
    if not np.allclose(transform[3], [0,0,0,1], atol=1e-9):
        raise ValueError('Invalid homogeneous world registration')
    basis = transform[:3,:3]
    if not np.allclose(basis.T@basis, np.eye(3), atol=1e-6) or np.linalg.det(basis) < .999:
        raise ValueError('Registration must retain metre scale and orientation')
    sources = {str(s['image_id']):s for s in candidate['sources']}
    if len(sources) != len(candidate['sources']) or not sources:
        raise ValueError('Unique attributed source images required')
    for source in sources.values():
        for name in ('creator','license','attribution','captured_at','photo_sha256','source_metadata_sha256'):
            if not source.get(name):raise ValueError('Incomplete source attribution or capture provenance')
        for name in ('photo_sha256','source_metadata_sha256'):
            value=source[name]
            if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Invalid source checksum')
    fit = set(map(str, registration['fit_image_ids']))
    controls = registration['heldout_controls']
    heldout = {str(c['image_id']) for c in controls}
    if len(fit)<2 or len(controls)<6 or len(heldout)<2 or fit&heldout or not (fit|heldout)<=sources.keys():
        raise ValueError('At least six controls from separate held-out images are required')
    cameras = [array(sources[identity]['camera_world_xyz'], (3,)) for identity in sorted(fit)]
    if max(np.linalg.norm(a-b) for a in cameras for b in cameras) < 2:
        raise ValueError('Fit cameras lack a two-metre baseline')
    errors = []
    targets=[]
    for control in controls:
        source = array(control['source_xyz'], (3,))
        target = array(control['measured_world_xyz'], (3,))
        predicted = (transform@np.r_[source,1])[:3]
        errors.append(float(np.linalg.norm(predicted-target)))
        targets.append(target)
    if len({tuple(p) for p in targets})<6 or np.linalg.matrix_rank(np.array(targets)-np.mean(targets,axis=0),tol=.1)<2:
        raise ValueError('Held-out controls need distinct measured positions across a surface')
    if np.median(errors)>.5 or np.quantile(errors,.95)>1:
        raise ValueError('Held-out registration exceeds the bounded pilot tolerance')
    observations=candidate['observations']
    if not isinstance(observations,list) or len(observations)>MAX_OBSERVATIONS:
        raise ValueError('Appearance observations exceed the bounded pilot')
    return transform, sources, fit, {'heldout_controls':len(errors),
        'median_m':float(np.median(errors)), 'p95_m':float(np.quantile(errors,.95))}


def compile_packets(pilot, candidate, registration, reader, palette, protected=()):
    if pilot['building_count']!=20 or len(pilot['buildings'])!=20 or canonical_sha(pilot['buildings'])!=pilot['selection_sha256']:
        raise ValueError('The frozen twenty-building selection changed')
    transform,sources,fit,metrics=registration_check(candidate,registration,pilot['frame'])
    if not palette or not set(palette)<=MATERIALS:
        raise ValueError('Palette must contain only supported full solid materials')
    names=sorted(palette)
    colors=np.stack([array(palette[name],(3,)) for name in names])
    if np.any((colors<0)|(colors>255)):raise ValueError('Invalid measured texture palette')
    buildings={b['pilot_id']:shape(b['footprint_geometry']) for b in pilot['buildings']}
    protected=set(map(tuple,protected))
    proposed=defaultdict(list); rejects=Counter(); per_building=Counter()
    for observation in candidate['observations']:
        try:
            building=observation['building_id']; image=str(observation['image_id']); face=observation['face']
            if building not in buildings or image not in fit or face not in NORMALS:
                raise ValueError('source_or_building_or_face')
            if observation.get('observed') is not True or observation.get('static_surface') is not True:
                raise ValueError('unknown_or_nonstatic')
            residual=float(observation['depth_residual_m']); facing=float(observation['facing_cosine'])
            if not np.isfinite([residual,facing]).all() or not 0<=residual<=.15 or not .25<=facing<=1:
                raise ValueError('visibility_or_depth')
            point=(transform@np.r_[array(observation['source_xyz'],(3,)),1])[:3]
            cell=tuple(int(v) for v in np.floor(point))
            if not -64<=cell[1]<960 or cell in protected:raise ValueError('height_or_existing_photo')
            # The cell's metric centre must be near the selected source boundary.
            metric=Point(cell[0]+.5+pilot['frame']['west'],pilot['frame']['north']-cell[2]-.5)
            if buildings[building].boundary.distance(metric)>1.5:raise ValueError('footprint_association')
            state=reader.state(cell)
            if state is None or str(state['Name']) not in MATERIALS or state.get('Properties') or reader.has_entity(cell):
                raise ValueError('unsupported_or_protected_world_block')
            neighbor=tuple(a+b for a,b in zip(cell,NORMALS[face]))
            if reader.name(neighbor) not in EMPTY:raise ValueError('covered_or_missing_neighbor')
            rgb=array(observation['rgb'],(3,))
            if np.any((rgb<0)|(rgb>255)) or not np.equal(rgb,np.rint(rgb)).all():raise ValueError('invalid_rgb')
            target=names[int(np.argmin(np.sum((colors-rgb)**2,axis=1)))]
            proposed[cell].append((str(state['Name']),target,building,image,face))
        except (ValueError,KeyError,TypeError) as error:
            rejects[str(error)]+=1
    changes=defaultdict(list); unchanged=0
    for cell, observations in sorted(proposed.items()):
        if len({(o[0],o[1],o[2]) for o in observations})!=1:
            rejects['conflicting_face_or_building_colors']+=len(observations);continue
        expected,target,building,_,_=observations[0]
        if expected==target:unchanged+=1;continue
        x,y,z=cell
        changes[(x//16,z//16)].append(((y+64)*256+(z%16)*16+x%16,expected,target))
        per_building[building]+=1
    packets=[]
    for (cx,cz), cells in sorted(changes.items()):
        material_names=sorted({value for _,old,new in cells for value in (old,new)})
        runs=[]
        for index,old,new in sorted(cells):
            codes=[material_names.index(old),material_names.index(new)]
            if runs and runs[-1][0]+runs[-1][1]==index and runs[-1][2:]==codes:runs[-1][1]+=1
            else:runs.append([index,1,*codes])
        packet=dict(version=1,frame=candidate['binding_frame'],cx=cx,cz=cz,mode='building_delta',
            palette=material_names,runs=runs,cells=len(cells),provenance={
                'appearance_only':True,'candidate_sha256':canonical_sha(candidate),
                'registration_sha256':canonical_sha(registration),'pilot_sha256':pilot['selection_sha256'],
                'source_images':sorted({o[3] for c,values in proposed.items() if (c[0]//16,c[2]//16)==(cx,cz) for o in values}),
                'geometry_changed':False,'new_blocks':0,'independent_face_textures':False})
        packets.append(validate(packet))
    return packets, {'registration':metrics,'proposed_color_cells':sum(p['cells'] for p in packets),
        'already_same_material':unchanged,'rejected_observations':dict(rejects),
        'per_building':{identity:per_building[identity] for identity in buildings},
        'new_face_colors_applied':0,'source_world_modified':False}


def texture_palette(jar):
    palette={}
    with zipfile.ZipFile(jar) as archive:
        for name in sorted(MATERIALS):
            with Image.open(io.BytesIO(archive.read('assets/minecraft/textures/block/'+name[10:]+'.png'))) as image:
                palette[name]=np.asarray(image.convert('RGB'),dtype=float).mean(axis=(0,1)).tolist()
    return palette


def protected_photo_cells(world):
    path=world/'resources.zip'
    if not path.exists():return set()
    with zipfile.ZipFile(path) as archive:
        records=json.loads(archive.read('earthcraft-skin-manifest.json'))
    return {tuple(r['cell']) for r in records}


def verify_source_files(candidate, directory):
    """Verify retained local bytes, not merely the shape of a claimed checksum."""
    directory=Path(directory).resolve()
    for source in candidate['sources']:
        for name,checksum in (('photo_path','photo_sha256'),('metadata_path','source_metadata_sha256')):
            relative=Path(source[name])
            path=directory/relative
            if relative.is_absolute() or '..' in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(directory):
                raise ValueError('Source files must remain inside the retained private artifact')
            if not path.is_file() or path.stat().st_size>32*2**20 or sha(path)!=source[checksum]:
                raise ValueError('Retained source bytes differ from appearance provenance')
        metadata=json.loads((directory/source['metadata_path']).read_text())
        if str(metadata['id'])!=str(source['image_id']) or metadata['captured_at']!=source['captured_at']:
            raise ValueError('Retained capture metadata differs from source attribution')


def stage(excerpt,pilot_path,output,candidate_path=None,registration_path=None,client_jar=None):
    excerpt,output=Path(excerpt),Path(output)
    proof=verify_snapshot(excerpt);pilot=json.loads(Path(pilot_path).read_text())
    if (proof['frame']!=pilot['frame'] or canonical_sha(pilot['buildings'])!=pilot['selection_sha256']
        or pilot['building_count']!=20 or len(pilot['buildings'])!=20):
        raise ValueError('Frozen pilot and save frames or selection differ')
    if output.exists():raise FileExistsError('Use a new immutable appearance stage')
    packets=[]
    report={'status':'registration_pending','proposed_color_cells':0,'new_face_colors_applied':0,
            'source_world_modified':False,'per_building':{b['pilot_id']:0 for b in pilot['buildings']}}
    if candidate_path and registration_path:
        candidate=json.loads(Path(candidate_path).read_text());registration=json.loads(Path(registration_path).read_text())
        if candidate.get('binding_frame')!=proof['binding_frame']:raise ValueError('Wrong save binding')
        if not client_jar:raise ValueError('Existing client texture palette required')
        verify_source_files(candidate,Path(candidate_path).parent)
        packets,report=compile_packets(pilot,candidate,registration,BlockReader(excerpt/'world'),
                                       texture_palette(client_jar),protected_photo_cells(excerpt/'world'))
        report.update(status='staged_for_isolated_native_verification',
                      client_palette_sha256=sha(client_jar),candidate_sha256=sha(candidate_path),registration_file_sha256=sha(registration_path))
    elif candidate_path or registration_path:
        raise ValueError('Both registered observations and independent registration are required')
    verify_snapshot(excerpt)
    output.mkdir(parents=True);(output/'exchange/inbox').mkdir(parents=True)
    records=[]
    for packet in packets:
        identity=publish(output/'exchange',packet)
        records.append({'patch':identity,'cells':packet['cells'],'chunk':[packet['cx'],packet['cz']]})
    report.update(schema='earthcraft.facade-material-stage.v1',frame=proof['binding_frame'],
        snapshot_sha256=sha(excerpt/'snapshot.json'),pilot_sha256=pilot['selection_sha256'],patches=records,
        deployed=False,native_stage_verified=False,client_render_tested=False,
        delivery_status='Do not publish: matching native and closed-writer stage proof is required')
    (output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('excerpt','pilot','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('candidate','registration','client-jar'):p.add_argument('--'+name,type=Path)
    a=p.parse_args();print(json.dumps(stage(a.excerpt,a.pilot,a.output,a.candidate,a.registration,a.client_jar),indent=2))
