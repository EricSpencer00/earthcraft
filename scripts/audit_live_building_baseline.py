"""Audit whether a building CAS stage matches the exact sparse live baseline.

The live publisher intentionally sends topology support and above-ground
structures rather than a fully filled source chunk.  A building delta must
therefore compare against that encoded base, not merely against an offline
Anvil source that contains additional support blocks.  This read-only audit
does not inspect or open a player save.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

import numpy as np

from city_save_update import read_region
from live_city import ROOT, encode_chunk, protected_base_bootstrap, sha, validate


def encoded_states(packet):
    """Return the exact default block name for every cell a live base sends."""
    names=np.asarray(packet['palette'],dtype=object)
    codes=np.full(262144,-1,np.int16)
    for start,count,code in packet['runs']:
        codes[start:start+count]=code
    return names,codes


def packet_identity(packet):
    raw=json.dumps(packet,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(gzip.compress(raw,mtime=0)).hexdigest()


def historic_packets(exchange,chunks):
    """Read archived new-chunk packets for just the requested chunk coordinates.

    A world may already contain a successfully imported packet emitted by an
    earlier compatible publisher revision.  Its content-addressed identity can
    differ when harmless provenance or sparse-base policy evolves, so the gate
    validates its actual expected cells instead of declaring it absent.
    """
    keys={f'{x},{z}' for x,z in chunks}
    if not keys:return {}
    pattern='"chunk": "(?:'+'|'.join(re.escape(key) for key in sorted(keys))+')"'
    search=subprocess.run(['rg','-l','--glob','*.json',pattern,str(exchange/'receipts')],
                          capture_output=True,text=True,check=False)
    if search.returncode not in (0,1):raise RuntimeError(search.stderr.strip())
    result={}
    for raw_path in search.stdout.splitlines():
        receipt_path=Path(raw_path);receipt=json.loads(receipt_path.read_text())
        if receipt.get('mode')!='new_chunk' or receipt.get('result')!='applied_in_memory':continue
        key=receipt.get('chunk')
        if key not in keys:continue
        coordinates=tuple(map(int,key.split(',')))
        identity=receipt.get('patch')
        if not isinstance(identity,str):continue
        source=exchange/'archive'/(identity+'.json.gz')
        if not source.is_file():source=exchange/'inbox'/(identity+'.json.gz')
        if not source.is_file():continue
        packet=validate(json.loads(gzip.decompress(source.read_bytes())))
        if (packet.get('mode')!='new_chunk' or f"{packet['cx']},{packet['cz']}"!=key):continue
        # Receipts are append-only.  Retain the newest compatible candidate
        # when a chunk has been safely imported more than once.
        if coordinates not in result or receipt.get('time','')>result[coordinates][0].get('time',''):
            result[coordinates]=(receipt,packet)
    return result


def audit(stage,exchange):
    stage,exchange=map(Path,(stage,exchange))
    manifest=json.loads((stage/'manifest.json').read_text())
    binding=json.loads((exchange/'binding.json').read_text())
    if (manifest.get('schema')!='building-delta-stage-v1' or
            manifest.get('frame')!=binding.get('frame') or manifest.get('llm_used') is not False):
        raise ValueError('Stage and live binding differ')
    source=Path(manifest['source_world'])
    report=json.loads((source/'earthcraft.json').read_text())
    elevation=np.load(source.parent/'sources/rasters.npz',allow_pickle=False)['elevation']
    size=int(report['source']['size'])
    if elevation.shape!=(size,size) or not np.isfinite(elevation).all():
        raise ValueError('Missing exact source elevation grid')
    ground=np.floor(elevation+report['vertical_offset_m']).astype(np.int32)
    source_receipt=source.parent/'geometry-receipt.json'
    if not source_receipt.is_file():raise ValueError('Missing source geometry receipt')
    source_receipt_hash=sha(source_receipt);source_record=json.loads(source_receipt.read_text())
    if (source_record.get('result')!='pass' or
            source_record.get('world_manifest_sha256')!=sha(source/'earthcraft.json')):
        raise ValueError('Source geometry receipt does not admit source world')
    patches={}
    for record in manifest['patches']:
        path=stage/'inbox'/(record['patch']+'.json.gz')
        if sha(path)!=record['patch']:raise ValueError('Staged patch changed')
        patch=validate(json.loads(gzip.decompress(path.read_bytes())))
        key=(patch['cx'],patch['cz'])
        if key in patches:raise ValueError('Duplicate staged chunk')
        patches[key]=patch
    historic=historic_packets(exchange,patches)
    regions={path.name:sha(path) for path in sorted((source/'region').glob('r.*.*.mca'))}
    if source_record.get('regions')!=regions:raise ValueError('Source receipt regions changed')
    results=[];changed=mismatched=delivered=queued=historic_ready=bootstrap_ready=0
    for region_name,region_hash in regions.items():
        chunks=read_region(source/'region'/region_name)
        for key,patch in sorted((key,value) for key,value in patches.items()
                                if f'r.{key[0]//32}.{key[1]//32}.mca'==region_name):
            original=chunks.get(key)
            if original is None:raise ValueError(f'Missing staged source chunk {key}')
            offset_x,offset_z=report['world_offset_xz']
            local_x=(key[0]-offset_x//16)*16;local_z=(key[1]-offset_z//16)*16
            chunk_ground=ground[local_z:local_z+16,local_x:local_x+16]
            if chunk_ground.shape!=(16,16):raise ValueError(f'Chunk outside source grid {key}')
            packet=encode_chunk(original,binding['frame'],{
                'tile':source_record['tile'],'receipt':str(source_receipt.resolve()),
                'receipt_sha256':source_receipt_hash,'base_receipt':str(source_receipt.resolve()),
                'base_receipt_sha256':source_receipt_hash,'detail_lane':'base',
                'region_sha256':region_hash,'physical_accuracy_verified':False,'llm_used':False},ground=chunk_ground)
            names,codes=encoded_states(packet)
            cell_count=bad=0;examples=[]
            for start,count,expected_code,_ in patch['runs']:
                live=np.full(count,'minecraft:air',dtype=object)
                sent=codes[start:start+count]
                present=sent>=0
                if present.any():live[present]=names[sent[present]]
                expected=patch['palette'][expected_code]
                failures=np.flatnonzero(live!=expected)
                bad+=len(failures);cell_count+=count
                for relative in failures[:max(0,3-len(examples))]:
                    examples.append({'index':int(start+relative),'expected':expected,'live_base':str(live[relative])})
            changed+=cell_count;mismatched+=bad
            identity=packet_identity(packet);receipt_path=exchange/'receipts'/(identity+'.json')
            inbox_path=exchange/'inbox'/(identity+'.json.gz')
            bootstrap=protected_base_bootstrap(packet);bootstrap_identity=packet_identity(bootstrap)
            bootstrap_receipt_path=exchange/'receipts'/(bootstrap_identity+'.json')
            bootstrap_inbox_path=exchange/'inbox'/(bootstrap_identity+'.json.gz')
            state='missing'
            if receipt_path.is_file():
                receipt=json.loads(receipt_path.read_text())
                if (receipt.get('mode')=='new_chunk' and receipt.get('chunk')==f'{key[0]},{key[1]}' and
                        receipt.get('result')=='applied_in_memory' and receipt.get('written')==packet['cells']):
                    state='applied';delivered+=1
                else:
                    state='receipt_not_exact'
            elif bootstrap_receipt_path.is_file():
                receipt=json.loads(bootstrap_receipt_path.read_text())
                if (receipt.get('mode')=='building_delta' and receipt.get('chunk')==f'{key[0]},{key[1]}' and
                        receipt.get('result')=='applied_in_memory' and receipt.get('written')==bootstrap['cells'] and
                        receipt.get('conflicts',0)==0 and receipt.get('already_target',0)==0):
                    state='protected_bootstrap_applied';delivered+=1;bootstrap_ready+=1
                else:
                    state='protected_bootstrap_not_exact'
            elif inbox_path.is_file():
                state='queued';queued+=1
            elif bootstrap_inbox_path.is_file():
                state='protected_bootstrap_queued';queued+=1
            elif key in historic:
                receipt,historic_packet=historic[key]
                historic_names,historic_codes=encoded_states(historic_packet)
                historic_bad=0
                for start,count,expected_code,_ in patch['runs']:
                    observed=np.full(count,'minecraft:air',dtype=object)
                    sent=historic_codes[start:start+count];present=sent>=0
                    if present.any():observed[present]=historic_names[sent[present]]
                    historic_bad+=int((observed!=patch['palette'][expected_code]).sum())
                provenance=historic_packet.get('provenance',{})
                if (historic_bad==0 and historic_packet.get('frame')==binding['frame'] and
                        provenance.get('receipt_sha256')==source_receipt_hash and
                        provenance.get('region_sha256')==region_hash):
                    state='historic_compatible';delivered+=1;historic_ready+=1
                else:
                    state='historic_not_compatible'
            results.append({'chunk':list(key),'cells':cell_count,'baseline_mismatches':bad,'examples':examples,
                            'source_region_sha256':region_hash,'base_patch':identity,'base_delivery':state,
                            'bootstrap_base_patch':bootstrap_identity,
                            'delivered_base_patch':historic[key][0]['patch'] if state=='historic_compatible' else
                            (bootstrap_identity if state=='protected_bootstrap_applied' else identity)})
        if sha(source/'region'/region_name)!=region_hash:raise ValueError('Immutable source changed during audit')
    if len(results)!=len(patches):raise ValueError('Staged patch region was not audited')
    exact=mismatched==0;delivery_complete=delivered==len(results)
    return {'passed':exact and delivery_complete,'schema':'live-building-baseline-audit-v1',
            'stage_manifest_sha256':sha(stage/'manifest.json'),'source_world':str(source.resolve()),
            'cells':changed,'baseline_mismatches':mismatched,'encoding_exact':exact,
            'base_delivery_complete':delivery_complete,'base_chunks_applied':delivered,
            'base_chunks_queued':queued,'base_chunks_missing':len(results)-delivered-queued,
            'base_chunks_historic_compatible':historic_ready,
            'base_chunks_protected_bootstrapped':bootstrap_ready,
            'ready_for_building_delta':exact and delivery_complete,
            'patches':len(results),'llm_used':False,'records':results}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True);parser.add_argument('--exchange',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=audit(args.stage,args.exchange)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({key:value for key,value in result.items() if key!='records'},indent=2))
