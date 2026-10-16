/** A restored immutable private shard is the prerequisite for removing its transport copy. */
export function cleanupIntent(record,proof,config){
 const {sha256,bytes,archive,entries}=record;
 if(!/^[a-f0-9]{64}$/.test(sha256)||!Number.isInteger(bytes)||bytes<1||bytes>(config.memory_upload?64:256)*2**20||
    archive!==config.bulk+'/hf-generated/'+sha256+'.tar'||record.plan_sha256!==config.plan_sha256||
    proof.state!=='verified'||proof.private!==true||proof.repo!==config.hf_repo||
    proof.archive_sha256!==sha256||proof.plan_sha256!==config.plan_sha256||
    !/^[a-f0-9]{40}$/.test(proof.revision)||proof.personal_save_uploaded!==false||proof.original_world_modified!==false||
    !Array.isArray(entries)||entries.length<1||entries.length>32)throw Error('Pinned verified cleanup intent required');
 const canonical=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
 if(record.tiles!==entries.length||proof.tiles!==entries.length||canonical(entries)!==canonical(proof.entries))throw Error('Export and restored upload identities must match');
 return {sha256,bytes,archive,entries,revision:proof.revision,plan_sha256:config.plan_sha256,repo:config.hf_repo};
}

export function checkedRestore(restored,intent){
 if(restored.state!=='restore_verified'||restored.archive_sha256!==intent.sha256||restored.bytes!==intent.bytes||
    restored.plan_sha256!==intent.plan_sha256||restored.repo!==intent.repo||restored.private!==true||
    restored.revision!==intent.revision||restored.tiles!==intent.entries.length||
    restored.expanded_region_hashes_verified!==true||restored.personal_save_uploaded!==false||restored.original_world_modified!==false)
  throw Error('Actual immutable private restore required before cleanup');
}

export async function completeCleanup(state,{restore,prune,persist},restored=null){
 const intent=state.cleanup_pending;if(!intent)return;
 if(intent.entries.some(e=>state.known[e.tile]!==e.receipt_sha256))throw Error('Durable tile checkpoint required before cleanup');
 checkedRestore(restored??await restore(intent),intent);
 const result=await prune(intent);
 if(!['pruned','already_absent'].includes(result.state)||result.sha256!==intent.sha256)throw Error('Verified transport-copy cleanup required');
 delete state.cleanup_pending;await persist(state);
 return result;
}

// Removes only this hash-pinned tar. Source regions, local manifests, receipts,
// logs and locks remain. A missing tar is a successful idempotent retry.
export const pruneProgram=`import hashlib,json,pathlib,re,stat,sys
r=json.load(sys.stdin);p=pathlib.Path(r['archive']);sha=r['sha256'];size=r['bytes']
assert re.fullmatch(r'[a-f0-9]{64}',sha) and type(size) is int and 0<size<=256*2**20
assert p.name==sha+'.tar' and p.parent.name=='hf-generated' and p.is_relative_to('/Volumes/LaCie/Earthcraft/runs')
assert not any(q.is_symlink() for q in [p,*p.parents])
if p.exists():
 s=p.stat();assert stat.S_ISREG(s.st_mode) and s.st_size==size
 with p.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==sha
 p.unlink();result='pruned'
else:result='already_absent'
print(json.dumps({'state':result,'sha256':sha}))`;
