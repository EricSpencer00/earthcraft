import assert from 'node:assert/strict';
import {decodeCrop,readBounded,PreviewController,MAX_BYTES} from '../dashboard/block-preview.mjs';
// Synthetic decoder fixture only. No real tile or published-generation claim.
const fixture={schema:'earthcraft.public-block-crop.v1',origin:[-16,0,0],size:[16,16,16],palette:['minecraft:stone','minecraft:grass_block'],blocks:[[0,0,0,0],[0,1,0,1]],coverage:'sampled-blocks',generation:{repo:'example/synthetic-fixture',tile:'-1_0',revision:'a'.repeat(40),shard_sha256:'b'.repeat(64),region_sha256:'c'.repeat(64),receipt_sha256:'d'.repeat(64)},attribution:[{source:'Synthetic test fixture',license:'Test-only authored fixture'}]};
assert.equal(decodeCrop(fixture),fixture);
for(const mutate of [v=>v.size=[512,1,512],v=>v.blocks.push([16,0,0,0]),v=>v.blocks.push([0,0,0,1]),v=>v.generation.revision='main',v=>v.attribution=[],v=>v.coverage='complete-world',v=>v.palette=['<script>']]){
 const value=structuredClone(fixture);mutate(value);assert.throws(()=>decodeCrop(value));
}
await assert.rejects(()=>readBounded(new Response(new Uint8Array(MAX_BYTES+1))),/512 KiB/);
await assert.rejects(()=>readBounded(new Response('',{status:403})),/403/);
const bytes=new TextEncoder().encode(JSON.stringify(fixture));
const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
const originalFetch=globalThis.fetch;const states=[];const controller=new PreviewController(s=>states.push(s));
try{
 globalThis.fetch=async()=>new Response(bytes);
 await controller.select({url:'https://example.test/crop.json',sha256:sha});assert.equal(states.at(-1).status,'ready');
 await controller.select({url:'https://example.test/crop.json',sha256:'0'.repeat(64)});assert.equal(states.at(-1).status,'error');
 globalThis.fetch=async()=>new Response('',{status:404});
 await controller.select({url:'https://example.test/crop.json',sha256:sha});assert.match(states.at(-1).message,/404/);
 let resolveFirst;globalThis.fetch=()=>new Promise(resolve=>{resolveFirst=resolve;});
 const stale=controller.select({url:'https://example.test/crop.json',sha256:sha});
 await controller.select(null);resolveFirst(new Response(bytes));await stale;assert.equal(states.at(-1).status,'unavailable');
 globalThis.fetch=async(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Stopped','AbortError'))));
 const cancelled=controller.select({url:'https://example.test/crop.json',sha256:sha});controller.cancel();await cancelled;assert.notEqual(states.at(-1).status,'ready');
}finally{globalThis.fetch=originalFetch;controller.cancel();}
console.log('Synthetic block-crop bounds, provenance, checksum, errors, stale responses and cancellation passed.');
