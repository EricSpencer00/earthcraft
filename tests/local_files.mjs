import assert from 'node:assert/strict';
import {readLocalCrop} from '../dashboard/local-files.mjs';
const crop={schema:'earthcraft.private-review-block-crop.v1',publication_allowed:false,origin:[0,0,0],size:[16,16,16],palette:['minecraft:stone'],blocks:[[0,0,0,0]],coverage:'sampled-blocks',generation:{repo:'example/synthetic-fixture',tile:'0_0',revision:'a'.repeat(40),shard_sha256:'b'.repeat(64),region_sha256:'c'.repeat(64),receipt_sha256:'d'.repeat(64)},attribution:[{source:'Synthetic test only',license:'Test only'}]};
const bytes=new TextEncoder().encode(JSON.stringify(crop));const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
const manifest={origin:crop.origin,size:crop.size,generation:crop.generation,crop:{bytes:bytes.length,sha256:sha}};
const file=new File([bytes],'crop.json'),pair=new File([JSON.stringify(manifest)],'manifest.json');
const original=globalThis.fetch;globalThis.fetch=()=>{throw Error('Local files must not use fetch');};
try{
 assert.equal((await readLocalCrop(file,pair)).digest,sha);
 await assert.rejects(()=>readLocalCrop(null,pair),/Choose both/);
 await assert.rejects(()=>readLocalCrop(new File([new Uint8Array(524289)],'large.json'),pair),/limit/);
 await assert.rejects(()=>readLocalCrop(file,new File(['{}'],'wrong.json')),/checksum/);
 const wrong=structuredClone(manifest);wrong.origin=[1,0,0];await assert.rejects(()=>readLocalCrop(file,new File([JSON.stringify(wrong)],'wrong.json')),/origin/);
}finally{globalThis.fetch=original;}
console.log('Local file validation, bounds, checksum and no-fetch checks passed.');
