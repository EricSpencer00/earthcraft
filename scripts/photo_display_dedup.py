"""Prepare a backed-up photo-display deduplication plan. No world writer exists.

The CLI only inventories, backs up and verifies. Hypothetical removal/restoration
is performed on copied NBT in memory, never on a save or replacement region file.
Any panel edit beyond its UUID, foreign panel, changed anchor or ambiguous
references block the plan. Different face models can share a world position.
"""
import argparse
from collections import Counter, defaultdict
import copy
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import time
import uuid
import zipfile

import nbtlib as n

from inspect_world import chunks
from playable_acceptance_snapshot import sha

MAX_ENTITY_BYTES = 64*2**20
MAX_REFERENCE_BYTES = 16*2**20
TAG = 'earthcraft_photo_skin'


def regular_save_file(world,path):
    if path.is_symlink() or not path.resolve().is_relative_to(Path(world).resolve()):
        raise ValueError('Input must be a regular file inside the bound save')


def canonical_tag(value):
    if isinstance(value,n.Compound):
        return n.Compound({key:canonical_tag(value[key]) for key in sorted(value)})
    if isinstance(value,n.List):
        return type(value)([canonical_tag(item) for item in value])
    return copy.deepcopy(value)


def tag_sha(value, exclude_uuid=False):
    value=copy.deepcopy(value)
    if exclude_uuid:value.pop('UUID',None)
    stream=io.BytesIO();n.File({'value':canonical_tag(value)}).write(stream)
    return hashlib.sha256(stream.getvalue()).hexdigest()


def entity_uuid(entity):
    value=entity.get('UUID')
    if not isinstance(value,n.IntArray) or len(value)!=4:
        raise ValueError('Photo displays need a native four-int UUID')
    raw=b''.join((int(v)&0xffffffff).to_bytes(4,'big') for v in value)
    return str(uuid.UUID(bytes=raw))


def references(value, identities, root_entity=False):
    """Conservatively recognize nested UUID arrays and UUIDs in saved strings."""
    found=set()
    if isinstance(value,n.Compound):
        for key,child in value.items():
            if root_entity and key=='UUID':continue
            found.update(references(child,identities))
    elif isinstance(value,n.IntArray) and len(value)==4:
        identity=entity_uuid({'UUID':value})
        if identity in identities:found.add(identity)
    elif isinstance(value,n.List):
        for child in value:found.update(references(child,identities))
    elif isinstance(value,n.String):
        found.update(identity for identity in identities if identity in str(value).lower())
    return found


def expected_panels(world):
    for relative in ('resources.zip','photo-skin.json'):regular_save_file(world,world/relative)
    with zipfile.ZipFile(world/'resources.zip') as archive:
        member=archive.getinfo('earthcraft-skin-manifest.json')
        if member.file_size>2**20:raise ValueError('Photo manifest exceeds bound')
        records=json.loads(archive.read(member))
    if not isinstance(records,list) or not 1<=len(records)<=512:
        raise ValueError('Unsupported photo panel manifest')
    expected={}
    for record in records:
        index=record['id'];cell=record['cell']
        if (type(index) is not int or not 0<=index<512 or
            len(cell)!=3 or any(type(v) is not int for v in cell)):
            raise ValueError('Invalid declared panel identity or anchor')
        model=f'earthcraft_skin:face_{index}'
        if model in expected:raise ValueError('Duplicate declared model identity')
        expected[model]=[v+.5 for v in cell]
    report=json.loads((world/'photo-skin.json').read_text())
    if report['display_entities']!=len(expected):raise ValueError('Declared photo totals disagree')
    return expected


def bounded_nbt(path):
    raw=path.read_bytes()
    if raw[:2]==b'\x1f\x8b':
        import zlib
        raw=zlib.decompressobj(16+zlib.MAX_WBITS).decompress(raw,8*2**20+1)
    if len(raw)>8*2**20:raise ValueError('Saved reference NBT exceeds bound')
    return n.File.parse(io.BytesIO(raw))


def keeper_preference(baseline,expected):
    """Prefer original UUIDs from a matching one-panel-per-model saved baseline."""
    baseline=Path(baseline).resolve()
    if expected_panels(baseline)!=expected:
        raise ValueError('Historical keeper baseline uses other panel models or anchors')
    result={};files={}
    paths=sorted((baseline/'entities').glob('r.*.*.mca'))
    if sum(p.stat().st_size for p in paths)>MAX_ENTITY_BYTES:
        raise ValueError('Historical keeper inventory exceeds bound')
    for path in paths:
        regular_save_file(baseline,path)
        if not path.stat().st_size:continue
        files[path.relative_to(baseline).as_posix()]=sha(path)
        for _,tag,_ in chunks(path):
            for entity in tag.get('Entities',[]):
                if TAG not in entity.get('Tags',[]):continue
                model=str(entity['item']['components']['minecraft:item_model'])
                if model not in expected or model in result or list(map(float,entity['Pos']))!=expected[model]:
                    raise ValueError('Historical keeper baseline is not unique and anchor-matched')
                result[model]=entity_uuid(entity)
    if result.keys()!=expected.keys():raise ValueError('Historical keeper baseline is incomplete')
    return result,{'world':str(baseline),'entity_region_sha256':files,
                   'resources_sha256':sha(baseline/'resources.zip'),'matched_panels':len(result)}


def analyze(world,preferred_keepers=None):
    world=Path(world).resolve();expected=expected_panels(world)
    paths=sorted((world/'entities').glob('r.*.*.mca'))
    if len(paths)>20000 or sum(p.stat().st_size for p in paths)>MAX_ENTITY_BYTES:
        raise ValueError('Entity inventory exceeds fixed 64 MiB bound')
    for path in paths:regular_save_file(world,path)
    groups=defaultdict(list);all_entities=[];other=Counter();regions={};conflicts=[];seen_uuids=set()
    for path in paths:
        if not path.stat().st_size:continue
        name=path.relative_to(world).as_posix();regions[name]=sha(path)
        match=re.fullmatch(r'r\.(-?\d+)\.(-?\d+)\.mca',path.name)
        rx,rz=map(int,match.groups())
        for slot,tag,_ in chunks(path):
            if 'Position' in tag:
                wanted=[rx*32+slot%32,rz*32+slot//32]
                if list(map(int,tag['Position']))!=wanted:raise ValueError('Entity chunk position differs from its native slot')
            for ordinal,entity in enumerate(tag.get('Entities',[])):
                all_entities.append(entity)
                if TAG not in entity.get('Tags',[]):
                    other[str(entity.get('id'))]+=1;continue
                try:
                    if str(entity.get('id'))!='minecraft:item_display':raise ValueError('Tagged entity is not an item display')
                    identity=entity_uuid(entity)
                    if identity in seen_uuids:raise ValueError('Repeated saved UUID requires separate repair')
                    seen_uuids.add(identity)
                    model=str(entity['item']['components']['minecraft:item_model'])
                    if model not in expected:raise ValueError('Unknown tagged panel model')
                    position=list(map(float,entity['Pos']))
                    if position!=expected[model]:raise ValueError('Panel anchor differs from declared observation')
                    row={'region':name,'chunk_slot':slot,'entity_index':ordinal,'uuid':identity,
                         'model':model,'position':position,'entity_sha256':tag_sha(entity),
                         'payload_without_uuid_sha256':tag_sha(entity,True)}
                    groups[model].append(row)
                except (ValueError,KeyError,TypeError) as error:
                    conflicts.append({'region':name,'chunk_slot':slot,'entity_index':ordinal,'reason':str(error)})
    missing=sorted(set(expected)-groups.keys())
    if missing:conflicts.append({'reason':'Declared panels absent from save','models':missing})
    duplicate_ids={row['uuid'] for rows in groups.values() if len(rows)>1 for row in rows}
    referenced=set()
    for entity in all_entities:referenced.update(references(entity,duplicate_ids,root_entity=True))
    reference_files=sorted({world/'level.dat',*(world/'playerdata').glob('*.dat'),*(world/'data').glob('*.dat')})
    reference_files=[p for p in reference_files if p.is_file()]
    if sum(p.stat().st_size for p in reference_files)>MAX_REFERENCE_BYTES:
        raise ValueError('Saved reference-file inventory exceeds 16 MiB bound')
    guards={}
    for path in reference_files:
        regular_save_file(world,path)
        guards[path.relative_to(world).as_posix()]=sha(path)
        referenced.update(references(bounded_nbt(path),duplicate_ids))
    operations=[]
    for model,rows in sorted(groups.items()):
        if len({row['payload_without_uuid_sha256'] for row in rows})!=1:
            conflicts.append({'model':model,'reason':'Panel payloads differ beyond UUID; preserve all variants'});continue
        protected=[row for row in rows if row['uuid'] in referenced]
        if len(protected)>1:
            conflicts.append({'model':model,'reason':'Multiple panel UUIDs have saved references'});continue
        preferred=[row for row in rows if row['uuid']==(preferred_keepers or {}).get(model)]
        keep=protected[0] if protected else (preferred[0] if preferred else rows[0])
        reason='saved_reference' if protected else ('matching_historical_uuid' if preferred else 'original_list_order')
        for row in rows:
            if row is keep:continue
            operations.append(dict(row,operation='propose_remove_exact_duplicate',
                keep_uuid=keep['uuid'],keep_entity_sha256=keep['entity_sha256'],keep_reason=reason))
    for relative in ('resources.zip','photo-skin.json','photo-layer-verification.json',
                     'city-coverage.json','regional-quality.json','earthcraft.json'):
        path=world/relative
        if path.is_file():
            regular_save_file(world,path)
            guards[relative]=sha(path)
    for path in sorted((world/'datapacks/earthcraft_photo_skin').rglob('*')):
        if path.is_file():
            regular_save_file(world,path)
            guards[path.relative_to(world).as_posix()]=sha(path)
    count=sum(TAG in e.get('Tags',[]) for e in all_entities)
    report={'schema':'earthcraft.photo-display-inventory.v1','declared_panels':len(expected),
        'tagged_photo_displays':count,'recognized_models':len(groups),
        'model_multiplicity':dict(Counter(len(rows) for rows in groups.values())),
        'unique_declared_positions':len({tuple(p) for p in expected.values()}),
        'other_entity_types':dict(other),'groups':dict(groups),'conflicts':conflicts,
        'referenced_photo_uuids':sorted(referenced),'entity_regions':regions,'guards':guards,
        'eligible_exact_duplicates':len(operations),'source_world_modified':False}
    plan={'schema':'earthcraft.photo-display-dedup-plan.v1','world':str(world),
        'status':'blocked' if conflicts else ('already_unique' if not operations else 'prepared_not_applied'),
        'expected_entity_regions':regions,'expected_guard_files':guards,
        'before_photo_displays':count,'declared_panels':len(expected),
        'planned_after_photo_displays':count if conflicts else count-len(operations),
        'operations':[] if conflicts else operations,'conflicts':conflicts,
        'selection_policy':'Keep the saved referenced UUID if unique; otherwise prefer a matching historical UUID when supplied, then the first current list entry. Require equal full typed NBT except UUID.',
        'applied':False,'world_entities_deleted':0,'writer_available':False,
        'requires_before_apply':['Separate explicit authorization to change world entities',
            'Closed world and revalidated guards/backup','Independent post-change native and client checks'],
        'rollback_policy':'Retain the exact original region bytes. Any later authorized writer must record its post-write checksum; restoring a whole region over newer edits is forbidden.'}
    return report,plan


def preview_chunk(tag,operations):
    """Pure in-memory hypothesis, also used for synthetic regression fixtures."""
    original=list(tag.get('Entities',[]));by_uuid={entity_uuid(e):e for e in original if 'UUID' in e}
    remove=set()
    for operation in operations:
        ordinal=operation['entity_index'];identity=operation['uuid'];keep=operation['keep_uuid']
        if (not 0<=ordinal<len(original) or entity_uuid(original[ordinal])!=identity or
            tag_sha(original[ordinal])!=operation['entity_sha256'] or keep not in by_uuid or
            tag_sha(by_uuid[keep])!=operation['keep_entity_sha256'] or
            tag_sha(original[ordinal],True)!=tag_sha(by_uuid[keep],True)):
            raise ValueError('Exact duplicate precondition changed')
        remove.add(ordinal)
    result=copy.deepcopy(tag)
    result['Entities']=n.List[n.Compound]([copy.deepcopy(e) for i,e in enumerate(original) if i not in remove])
    return result


def restore_preview(preview,original,operations):
    """Reverse the hypothesis in memory without replacing any current-world bytes."""
    if not operations:
        if tag_sha(preview)!=tag_sha(original):raise ValueError('Unchanged chunk differs in the hypothesis')
        return copy.deepcopy(original)
    remove={operation['entity_index'] for operation in operations}
    retained=iter(preview['Entities']);restored=[]
    for ordinal,entity in enumerate(original['Entities']):
        restored.append(copy.deepcopy(entity) if ordinal in remove else next(retained))
    if list(retained):raise ValueError('Hypothesis contains unexpected new entities')
    result=copy.deepcopy(preview);result['Entities']=n.List[n.Compound](restored)
    if tag_sha(result)!=tag_sha(original):raise ValueError('Hypothetical reversal was not exact')
    return result


def preview_plan(backup,plan):
    per_chunk=defaultdict(list)
    for operation in plan['operations']:
        per_chunk[(operation['region'],operation['chunk_slot'])].append(operation)
    before=after=0;verified=0
    for relative in plan['expected_entity_regions']:
        for slot,tag,_ in chunks(backup/relative):
            operations=per_chunk.get((relative,slot),[])
            hypothesis=preview_chunk(tag,operations) if operations else copy.deepcopy(tag)
            restore_preview(hypothesis,tag,operations)
            before+=sum(TAG in e.get('Tags',[]) for e in tag.get('Entities',[]))
            after+=sum(TAG in e.get('Tags',[]) for e in hypothesis.get('Entities',[]))
            if operations:verified+=1
    if before!=plan['before_photo_displays'] or after!=plan['planned_after_photo_displays']:
        raise ValueError('In-memory hypothesis does not match the dry-run totals')
    return {'in_memory_only':True,'before':before,'hypothetical_after':after,
            'exact_nbt_reversal_verified':True,'chunks_with_proposed_changes':verified,
            'replacement_region_files_created':False,'world_entities_deleted':0}


def prepare(world,output,keeper_baseline=None):
    world,output=Path(world).resolve(),Path(output).resolve()
    if output.exists():raise FileExistsError('Use a new immutable preparation directory')
    if output.is_relative_to(world):raise ValueError('Preparation must stay outside the save')
    with (world/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        preferred,provenance=keeper_preference(keeper_baseline,expected_panels(world)) if keeper_baseline else ({},None)
        inventory,plan=analyze(world,preferred)
        if provenance:
            inventory['keeper_baseline']=provenance
            plan['keeper_baseline']=provenance
        output.mkdir(parents=True);backup=output/'backup';backup.mkdir()
        files={}
        for relative,expected in plan['expected_entity_regions'].items():
            target=backup/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(world/relative,target)
            if sha(target)!=expected or sha(world/relative)!=expected:
                raise ValueError('Entity backup or source changed during save fence')
            files['backup/'+relative]=expected
        for relative,expected in plan['expected_guard_files'].items():
            if sha(world/relative)!=expected:raise ValueError('Guarded save changed during preparation')
        plan['backup_verified']=True
        plan['dry_run']=preview_plan(backup,plan)
        inventory['backed_up_entity_bytes']=sum((backup/r).stat().st_size for r in plan['expected_entity_regions'])
        for name,value in (('inventory.json',inventory),('patch.json',plan)):
            path=output/name;path.write_text(json.dumps(value,indent=2)+'\n');files[name]=sha(path)
        proof={'schema':'earthcraft.photo-display-preparation.v1','created':time.time(),
               'source_world':str(world),'files':files,'backup_verified':True,'source_world_modified':False,
               'entities_deleted':0,'applied':False,'status':plan['status']}
        (output/'preparation.json').write_text(json.dumps(proof,indent=2)+'\n')
    verify(output,world)
    return proof


def verify(output,world=None):
    output=Path(output).resolve();proof=json.loads((output/'preparation.json').read_text())
    for relative,expected in proof['files'].items():
        path=output/relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink() or not path.resolve().is_relative_to(output):
            raise ValueError('Preparation file escaped its artifact directory')
        if sha(path)!=expected:raise ValueError('Preparation or backup checksum changed')
    plan=json.loads((output/'patch.json').read_text())
    preview_plan(output/'backup',plan)
    if world:
        world=Path(world).resolve()
        if str(world)!=proof['source_world']:raise ValueError('Verification names another owner save')
        for relative,expected in {**plan['expected_entity_regions'],**plan['expected_guard_files']}.items():
            if sha(world/relative)!=expected:raise ValueError('Owner save changed; regenerate a dry-run plan before any later action')
    return {'artifact_verified':True,'backup_verified':True,'dry_run_verified':True,
            'owner_guards_match':bool(world),'entities_deleted':0,'applied':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','verify'])
    parser.add_argument('--world',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--keeper-baseline',type=Path)
    args=parser.parse_args()
    if args.mode=='prepare' and not args.world:parser.error('prepare requires --world')
    result=prepare(args.world,args.output,args.keeper_baseline) if args.mode=='prepare' else verify(args.output,args.world)
    print(json.dumps({k:v for k,v in result.items() if k!='files'},indent=2))
