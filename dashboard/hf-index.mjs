// Generated-tile v1 adapter. Callers must supply an audited immutable release pin.
export const INDEX_LIMIT=8*1024*1024,SHARD_LIMIT=8*1024*1024,REGION_LIMIT=8*1024*1024;
export const sha256=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
export async function boundedBytes(response,limit,signal){
 if(!response.ok)throw Error(`HF data unavailable (${response.status}); access or coverage may be restricted.`);
 if(Number(response.headers.get('content-length'))>limit)throw Error('Selected data exceeds this viewer’s mobile download limit.');
 const reader=response.body?.getReader();if(!reader)throw Error('HF response has no readable body.');
 const chunks=[];let length=0;
 try{while(true){signal?.throwIfAborted();const {value,done}=await reader.read();if(done)break;length+=value.byteLength;if(length>limit)throw Error('Selected data exceeds this viewer’s mobile download limit.');chunks.push(value);}}
 catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
 const result=new Uint8Array(length);let offset=0;for(const chunk of chunks){result.set(chunk,offset);offset+=chunk.byteLength;}return result;
}
export function releaseURL(pin,path){
 if(!/^[\w.-]+\/[\w.-]+$/.test(pin.repo)||!/^[a-f0-9]{40}$/.test(pin.revision)||!/^generated-tiles\/v1\/(?:index\.json|shards\/[a-f0-9]{2}\/[a-f0-9]{64}\.tar)$/.test(path))throw Error('Invalid immutable HF reference.');
 return `https://huggingface.co/datasets/${pin.repo}/resolve/${pin.revision}/${path}`;
}
export function validateIndex(index){
 if(index?.schema!=='earthcraft.generated-tile-index.v1'||!/^[a-f0-9]{64}$/.test(index.plan_sha256))throw Error('Unsupported HF tile index.');
 const frame=index.frame;if(!frame||typeof frame.crs!=='string'||frame.crs.length>16384||!Number.isInteger(frame.dimension_min_y)||!Number.isInteger(frame.dimension_height)||frame.dimension_height<16||frame.dimension_height>1024)throw Error('Invalid frozen world frame.');
 if(!index.tiles||!index.shards||Object.keys(index.tiles).length>20000||Object.keys(index.shards).length>20000)throw Error('HF index exceeds the tile limit.');
 for(const [id,tile] of Object.entries(index.tiles)){
  const [x,z]=id.split('_').map(Number),shard=index.shards[tile.shard_sha256];
  if(!/^-?\d+_-?\d+$/.test(id)||!Number.isSafeInteger(x)||!Number.isSafeInteger(z)||!shard||tile.size_m!==512||tile.metres_per_block!==1||tile.chunks!==1024||tile.world_offset_xz?.[0]!==x*512||tile.world_offset_xz?.[1]!==z*512||tile.region_file!==`r.${x}.${z}.mca`||!['region_sha256','receipt_sha256','shard_sha256'].every(k=>/^[a-f0-9]{64}$/.test(tile[k])))throw Error('Invalid tile geometry or provenance.');
  if(shard.sha256!==tile.shard_sha256||shard.path!==`generated-tiles/v1/shards/${shard.sha256.slice(0,2)}/${shard.sha256}.tar`||!Number.isSafeInteger(shard.bytes)||shard.bytes<=0||shard.bytes>64*1024*1024)throw Error('Invalid shard reference.');
 }
 return index;
}
export function validatePublicRelease(pin){
 if(pin?.publication_allowed!==true||pin.public_readback_verified!==true||typeof pin.rights_decision_id!=='string'||!pin.rights_decision_id)throw Error('HF public release has not cleared the producer audit.');
 if(pin.geometry_qa_passed!==true||!['geometry_reference_sha256','geometry_report_sha256'].every(key=>/^[a-f0-9]{64}$/.test(pin[key])))throw Error('HF replacement geometry has not passed the source-reference quality gate.');
 if(!Array.isArray(pin.content_layers)||!['terrain','buildings'].every(layer=>pin.content_layers.includes(layer)))throw Error('The canonical viewer release must include both terrain and buildings.');
 if(!['frame_sha256','plan_sha256','provenance_sha256'].every(key=>/^[a-f0-9]{64}$/.test(pin[key]))||typeof pin.distribution_license!=='string'||!pin.distribution_license||typeof pin.coverage_note!=='string'||!pin.coverage_note)throw Error('Missing terrain/building frame, provenance, license or coverage contract.');
 releaseURL(pin,'generated-tiles/v1/index.json');return pin;
}
const canonical=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
export async function loadIndex(pin,signal){
 if(!/^[a-f0-9]{64}$/.test(pin.index_sha256))throw Error('Missing pinned index checksum.');
 const bytes=await boundedBytes(await fetch(releaseURL(pin,'generated-tiles/v1/index.json'),{signal,credentials:'omit',referrerPolicy:'no-referrer'}),INDEX_LIMIT,signal);
 if(await sha256(bytes)!==pin.index_sha256)throw Error('HF index checksum mismatch.');
 const index=validateIndex(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)));
 if(pin.frame_sha256&&await sha256(new TextEncoder().encode(canonical(index.frame)))!==pin.frame_sha256)throw Error('HF frozen frame does not match the release contract.');
 if(pin.plan_sha256&&index.plan_sha256!==pin.plan_sha256)throw Error('HF generation plan does not match the release contract.');
 return index;
}
export function selectTile(index,x,z){
 if(!Number.isSafeInteger(x)||!Number.isSafeInteger(z))throw Error('Invalid world coordinates.');
 const id=Math.floor(x/512)+'_'+Math.floor(z/512),tile=index.tiles[id];
 if(!tile)return null;const shard=index.shards[tile.shard_sha256];
 return {id,tile,shard,frame:index.frame,plan_sha256:index.plan_sha256};
}
