"""Stage additive compare-and-set building patches; never opens a player save.

Expected states come from the immutable original scan, targets from a verified
building candidate. Conflicting current states are preserved by the game mod.
Matching states cannot establish a player's historical intent; patches are
also deferred near players and require prior Earthcraft chunk ownership.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from city_save_update import read_region
from live_city import atomic, encode_chunk, publish, sha, validate
from verify_metric_world import verify


def native_states(tag):
    """Decode using the existing bounded importer codec, including default air."""
    encoded=encode_chunk(tag,'decode',{})
    palette=['minecraft:air',*encoded['palette']]
    values=np.zeros(262144,np.int16)
    for start,count,code in encoded['runs']:
        values[start:start+count]=code+1
    return palette,values


def encode_delta(original,target,frame,provenance,protected_cells=()):
    if any(int(original[k])!=int(target[k]) for k in ('xPos','zPos')):
        raise ValueError('Delta chunk coordinates differ')
    before_palette,before=native_states(original);after_palette,after=native_states(target)
    palette=sorted(set(before_palette+after_palette));codes={name:i for i,name in enumerate(palette)}
    before=np.asarray([codes[s] for s in before_palette],np.int16)[before]
    after=np.asarray([codes[s] for s in after_palette],np.int16)[after]
    different=before!=after
    cx,cz=int(original['xPos']),int(original['zPos'])
    for x,y,z in protected_cells:
        if x//16==cx and z//16==cz and -64<=y<960:
            different[(y+64)*256+(z%16)*16+x%16]=False
    changed=np.flatnonzero(different)
    if not len(changed):return None
    if (after[changed]==codes['minecraft:air']).any():raise ValueError('Building update removes source blocks')
    used=sorted(set(before[changed])|set(after[changed]));remap=np.full(len(palette),-1,int)
    remap[used]=np.arange(len(used));palette=[palette[i] for i in used]
    before,after=remap[before[changed]],remap[after[changed]]
    breaks=np.r_[0,np.flatnonzero((np.diff(changed)!=1)|(np.diff(before)!=0)|(np.diff(after)!=0))+1,len(changed)]
    runs=[[int(changed[a]),int(b-a),int(before[a]),int(after[a])] for a,b in zip(breaks[:-1],breaks[1:])]
    return validate(dict(version=1,frame=frame,cx=int(original['xPos']),cz=int(original['zPos']),
                         mode='building_delta',palette=palette,runs=runs,cells=len(changed),provenance=provenance))


def patches(original,candidate,binding):
    original,candidate=Path(original),Path(candidate)
    style=json.loads((candidate/'building-layer.json').read_text())
    if style['schema']!='styled-shell-v1' or style['llm_used'] is not False:
        raise ValueError('Explicit non-LLM building adaptation required')
    if Path(style['source_world']).resolve()!=original.resolve():raise ValueError('Wrong immutable baseline')
    verify(candidate)
    before=json.loads((original/'earthcraft.json').read_text());after=json.loads((candidate/'earthcraft.json').read_text())
    if any(before[k]!=after[k] for k in ('source','world_offset_xz','vertical_offset_m','dimension_height','dimension_min_y')):
        raise ValueError('Source/candidate coordinate frames differ')
    ox,oz=before['world_offset_xz'];frame=binding['coordinate_frame']
    if (before['source']['crs']!=frame['crs'] or before['source']['west']!=frame['west']+ox or
        before['source']['north']!=frame['north']-oz or before['vertical_offset_m']!=frame['vertical_offset_m'] or
        before['dimension_height']!=1024 or before['dimension_min_y']!=-64):raise ValueError('Binding frame mismatch')
    source_regions=style['source_regions']
    from building_layer import photo_protection
    installed=Path(binding['world']) if binding.get('world') else None
    anchors,exposed,_=photo_protection(installed) if installed else ([],[],None)
    protected=[*anchors,*exposed]
    photo_hash=sha(installed/'resources.zip') if anchors else None
    style_hash=sha(candidate/'building-layer.json')
    if set(source_regions)!={p.name for p in (candidate/'region').glob('r.*.*.mca')}:
        raise ValueError('Candidate region set differs from original')
    for name,expected in sorted(source_regions.items()):
        source=original/'region'/name;target=candidate/'region'/name
        if sha(source)!=expected:raise ValueError('Original region changed since styling')
        target_hash=sha(target)
        before_chunks,after_chunks=read_region(source),read_region(target)
        if before_chunks.keys()!=after_chunks.keys():raise ValueError('Candidate chunk set differs from original')
        for key in sorted(before_chunks):
            patch=encode_delta(before_chunks[key],after_chunks[key],binding['frame'],
                {'source_region_sha256':expected,'target_region_sha256':target_hash,
                 'building_manifest_sha256':style_hash,
                 'building_ids':[b['id'] for b in style['buildings']],
                 'protected_photo_resource_sha256':photo_hash,'photo_anchors_and_exposed_cells':len(protected),
                 'geometry_changed':style['geometry_changed'],'llm_used':False,
                 'policy':'Exact current-state compare-and-set; no removals; no guarantee of historical edit intent'},protected)
            if patch is not None:yield patch
        if sha(source)!=expected or sha(target)!=target_hash:raise ValueError('Input region changed while encoding')
    if sha(candidate/'building-layer.json')!=style_hash or (photo_hash and sha(installed/'resources.zip')!=photo_hash):
        raise ValueError('Building or accepted photo manifest changed while encoding')


def stage(original,candidate,binding_path,output):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    binding=json.loads(Path(binding_path).read_text())
    output.mkdir(parents=True);(output/'inbox').mkdir();(output/'receipts').mkdir()
    records=[]
    for patch in patches(original,candidate,binding):
        identity=publish(output,patch)
        records.append({'patch':identity,'chunk':[patch['cx'],patch['cz']],'cells':patch['cells']})
    result={'schema':'building-delta-stage-v1','frame':binding['frame'],
            'source_world':str(Path(original).resolve()),'candidate_world':str(Path(candidate).resolve()),
            'candidate_manifest_sha256':sha(Path(candidate)/'building-layer.json'),
            'patches':records,'changed_cells':sum(p['cells'] for p in records),
            'installed':False,'llm_used':False}
    atomic(output/'manifest.json',json.dumps(result,indent=2).encode())
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',type=Path,required=True);parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--binding',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();result=stage(a.original,a.candidate,a.binding,a.output)
    print(json.dumps({'staged_patches':len(result['patches']),'changed_cells':result['changed_cells'],'installed':False}))
