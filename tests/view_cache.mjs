// Water Tower view cache: integrity of dashboard/cache/water-tower.* and RegionClient's
// pack-first / Range-fallback behaviour (no network).
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {RegionClient,hfRegionBase,PINNED_REVISION,MAX_PACK_BYTES,readChunkPayload,readNBT,decodeChunk,HEADER_BYTES,SECTOR} from '../dashboard/anvil.mjs';

const dir=new URL('../dashboard/cache/',import.meta.url);
const manifest=JSON.parse(await readFile(new URL('water-tower.json',dir),'utf8')),pack=new Uint8Array(await readFile(new URL('water-tower.bin',dir)));
assert.equal(manifest.schema,'earthcraft.view-cache.v1');
assert.equal(manifest.revision,PINNED_REVISION,'pack and the viewer must pin the same HF commit');
assert.match(manifest.revision,/^[0-9a-f]{40}$/);
assert.equal(pack.length,manifest.pack.bytes);assert.ok(pack.length<=MAX_PACK_BYTES&&pack.length<10*2**20);
assert.equal(createHash('sha256').update(pack).digest('hex'),manifest.pack.sha256);
assert.deepEqual(manifest.center_chunk,[2,2]);assert.equal(manifest.radius,8);
assert.equal(Object.keys(manifest.chunks).length,17*17);
for(const r of Object.values(manifest.regions))assert.match(r.sha256,/^[0-9a-f]{64}$/);
let end=0;
for(const [key,[offset,length]] of Object.entries(manifest.chunks)){
  assert.equal(offset,end,'records are contiguous');end+=length;
  const chunk=decodeChunk(readNBT(await readChunkPayload(pack.subarray(offset,offset+length))).value);
  assert.equal(`${chunk.x},${chunk.z}`,key);
}
assert.equal(end,pack.length);

// Fake hosts: the site serves the pack; the dataset answers Range reads (and counts them).
const base=hfRegionBase(PINNED_REVISION);
function host({sitePack=true}={}){
  const log=[];
  const fetch=async(url,init={})=>{
    log.push(url);
    if(url.endsWith('water-tower.json'))return sitePack?new Response(JSON.stringify(manifest)):new Response('',{status:404});
    if(url.endsWith('water-tower.bin'))return new Response(pack);
    // Region host: build a region on the fly from the pack's records.
    const [,cx,cz]=/r\.(-?\d+)\.(-?\d+)\.mca$/.exec(url)||[];
    const m=/bytes=(\d+)-(\d+)/.exec(init.headers?.Range||'');const start=+m[1],stop=+m[2];
    const rx=+cx,rz=+cz,region=new Uint8Array(HEADER_BYTES+1024*2*SECTOR),view=new DataView(region.buffer);let sector=2;
    for(const [key,[o,l]] of Object.entries(manifest.chunks)){const [x,z]=key.split(',').map(Number);if(x>>5!==rx||z>>5!==rz)continue;const i=(x&31)+(z&31)*32,n=Math.ceil(l/SECTOR);view.setUint32(i*4,(sector<<8)|n);region.set(pack.subarray(o,o+l),sector*SECTOR);sector+=n;}
    const body=region.slice(start,stop+1);
    const r=new Response(body,{status:206,headers:{'content-range':`bytes ${start}-${stop}/${region.length}`}});
    return r;
  };
  return {fetch,log};
}
const coords=[];for(let dz=-8;dz<=8;dz++)for(let dx=-8;dx<=8;dx++)coords.push([2+dx,2+dz]);
{
  const h=host(),client=new RegionClient({base,fetch:h.fetch,packs:['https://site.test/cache/water-tower.json']});
  const res=await client.loadChunks(coords);
  assert.equal(res.filter(r=>r.status==='ok'&&r.packed).length,289);
  assert.equal(client.stats.requests,0,'no Range requests for the cached view');
  assert.equal(client.stats.packRequests,2);
  // A chunk just outside the pack falls back to Range reads.
  const out=await client.loadChunks([[11,2]]);assert.equal(out[0].status,'absent');assert.ok(client.stats.requests>=1,'outside chunks use Range reads');
  assert.equal(client.stats.packRequests,2,'pack fetched once');
}
{
  const h=host({sitePack:false}),client=new RegionClient({base,fetch:h.fetch,packs:['https://site.test/cache/water-tower.json']});
  const res=await client.loadChunks(coords.slice(0,20));
  assert.ok(res.every(r=>r.status==='ok'&&!r.packed),'missing pack falls back to Range reads');
}
{
  // A pack built from another revision is ignored.
  const h=host(),client=new RegionClient({base:hfRegionBase('main'),fetch:h.fetch,packs:['https://site.test/cache/water-tower.json']});
  const res=await client.loadChunks([[2,2]]);assert.ok(!res[0].packed);
}
{
  // Redirect reuse: second read of a region goes straight to the signed URL; an expired one is retried via resolve.
  const urls=[];let expired=false;
  const fetch=async(url,init)=>{urls.push(url);const signed=url.startsWith('https://cdn.test/');
    if(signed&&expired)return new Response('',{status:403});
    const body=new Uint8Array(HEADER_BYTES),r=new Response(body,{status:206,headers:{'content-range':`bytes 0-${HEADER_BYTES-1}/9999`}});
    if(!signed){Object.defineProperty(r,'redirected',{value:true});Object.defineProperty(r,'url',{value:'https://cdn.test/x?sig=1'});}
    return r;};
  const client=new RegionClient({base,fetch});
  await client.range(client.url(0,0),0,HEADER_BYTES);await client.range(client.url(0,0),0,HEADER_BYTES);
  assert.equal(urls[1],'https://cdn.test/x?sig=1');
  expired=true;await client.range(client.url(0,0),0,HEADER_BYTES);
  assert.deepEqual(urls.slice(2),['https://cdn.test/x?sig=1',client.url(0,0)]);
}
console.log('view cache ok:',pack.length,'bytes,',Object.keys(manifest.chunks).length,'chunks');
