"""Compare the closed-save CAS writer to the already native-saved fixture.

This is a read-only cross-implementation check: source chunks are transformed
in memory by the closed writer, then compared with the same staged patches
after two real Fabric/Minecraft save cycles.
"""
import argparse
import gzip
import json
from pathlib import Path

import numpy as np

from apply_building_stage_closed import mutate_chunk
from check_live_import import block_volume
from city_save_update import read_region
from live_city import atomic, sha, validate


def check(stage,native,output):
    stage,native,output=map(Path,(stage,native,output))
    if output.exists():raise FileExistsError(output)
    manifest=json.loads((stage/'manifest.json').read_text())
    native_proof=json.loads((native/'verification.json').read_text())
    if not native_proof.get('passed') or native_proof.get('staged_manifest_sha256')!=sha(stage/'manifest.json'):
        raise ValueError('Native proof does not admit this immutable stage')
    source=Path(manifest['source_world']);saved=native/'world'
    expected_regions={path.name:sha(path) for path in sorted((source/'region').glob('r.*.*.mca'))}
    records=[];compared=0;original_regions={};persisted_regions={}
    for item in manifest['patches']:
        patch_file=stage/'inbox'/(item['patch']+'.json.gz')
        if sha(patch_file)!=item['patch']:raise ValueError('Staged patch changed')
        patch=validate(json.loads(gzip.decompress(patch_file.read_bytes())))
        region=f"r.{patch['cx']//32}.{patch['cz']//32}.mca"
        # The district has many chunks in one region.  Decode each immutable
        # region once; per-patch reopening would multiply both I/O and NBT
        # parsing without increasing the strength of this comparison.
        original_regions.setdefault(region,read_region(source/'region'/region))
        persisted_regions.setdefault(region,read_region(saved/'region'/region))
        original=original_regions[region].get((patch['cx'],patch['cz']))
        persisted=persisted_regions[region].get((patch['cx'],patch['cz']))
        if original is None or persisted is None:raise ValueError('Missing staged chunk in source or native save')
        closed,counts=mutate_chunk(original,patch)
        if counts['written']!=item['cells'] or counts['conflicts'] or counts['already_target']:
            raise ValueError('Closed writer does not exactly admit immutable baseline')
        np.testing.assert_array_equal(block_volume(persisted),block_volume(closed),
                                      err_msg=f"Native/closed block mismatch at {patch['cx']},{patch['cz']}")
        if persisted.get('block_entities')!=closed.get('block_entities'):raise ValueError('Native/closed block entity mismatch')
        compared+=262144;records.append({'patch':item['patch'],'chunk':item['chunk'],'cells':item['cells']})
    for name,value in expected_regions.items():
        if sha(source/'region'/name)!=value:raise ValueError('Immutable source changed during check')
    result={'passed':True,'stage_manifest_sha256':sha(stage/'manifest.json'),
            'native_verification':str((native/'verification.json').resolve()),
            'native_mod_sha256':native_proof['mod_sha256'],'patches':len(records),
            'changed_cells':sum(item['cells'] for item in records),'block_cells_compared':compared,
            'two_native_save_cycles_previously_verified':native_proof['two_load_save_cycles_verified'],
            'closed_writer_matches_native_blocks_and_block_entities':True,'llm_used':False,'records':records}
    output.mkdir(parents=True);atomic(output/'verification.json',json.dumps(result,indent=2).encode())
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--native',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=check(a.stage,a.native,a.output)
    print(json.dumps({key:value for key,value in result.items() if key!='records'},indent=2))
