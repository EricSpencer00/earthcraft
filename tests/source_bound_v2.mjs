import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {SourceBoundV2Client,decodeSourceBoundV2,polygonContains} from '../dashboard/source-bound-v2.mjs';
import {HF_RELEASE} from '../dashboard/hf-release.mjs';
const bytes=await Promise.all(['index','manifest','crop'].map(n=>readFile(new URL('./fixtures/source-bound-v2/'+n+'.json',import.meta.url))));
const [index,manifest,crop]=bytes.map(b=>JSON.parse(b)),tile=index.tiles[0];
const decoded=await decodeSourceBoundV2(crop,manifest);assert.equal(decoded.occupied_total,1307);assert.match(decoded.quality_disclosure,/15 direct, 6 interpolated, 24 fitted, 0 unknown/);assert(!decoded.quality_disclosure.includes('missing heights remain unfilled'));
for(const mutate of [c=>c.schema='earthcraft.source-bound-building-crop.v1',c=>c.publication_allowed=false,c=>c.generation.dataset_id_sha256='a'.repeat(64),c=>c.roof_support_columns[0].uncertainty_m=null,c=>c.roof_support_columns[0].model_id='unbound',c=>c.roof_support_columns.push(c.roof_support_columns[0]),c=>c.coverage.roof_cells_direct++,c=>c.block_source_flags=[],c=>c.blocks[0][0]=999,c=>c.wgs84_corners[0][0]=0]){const bad=structuredClone(crop);mutate(bad);await assert.rejects(()=>decodeSourceBoundV2(bad,manifest));}
const center={lng:crop.wgs84_corners.reduce((n,p)=>n+p[0],0)/4,lat:crop.wgs84_corners.reduce((n,p)=>n+p[1],0)/4};assert(polygonContains(crop.wgs84_corners,center));assert(!polygonContains(crop.wgs84_corners,{lng:0,lat:0}));
const original=globalThis.fetch;let requests=[];
globalThis.fetch=async(url,options)=>{assert.equal(options.credentials,'omit');assert.equal(options.referrerPolicy,'no-referrer');assert(url.includes('/'+HF_RELEASE.revision+'/'));requests.push(url);return new Response(url.endsWith('index.json')?bytes[0]:url.includes('/manifests/')?bytes[1]:bytes[2]);};
try{
 const client=new SourceBoundV2Client(HF_RELEASE);assert.equal(await client.connect(),15);assert.equal((await client.crop(tile)).occupied_total,1307);await client.crop(tile);assert.equal(requests.length,3);assert.equal(client.cache.size,1);assert.throws(()=>client.url('../private.json'));
 const aborted=new AbortController();aborted.abort();await assert.rejects(()=>client.crop(tile,aborted.signal));
 globalThis.fetch=async()=>new Response('{}');await assert.rejects(()=>new SourceBoundV2Client(HF_RELEASE).connect(),/checksum/);
 globalThis.fetch=async()=>new Response('denied',{status:403});await assert.rejects(()=>new SourceBoundV2Client(HF_RELEASE).connect(),/403/);
 assert.throws(()=>new SourceBoundV2Client({...HF_RELEASE,public_readback_verified:false}));
}finally{globalThis.fetch=original;}
console.log('Published v2 fixture: native dataset/tile generation, reconstructed supports, bounds, hashes, denied access, cancellation, cache and honest disclosure passed.');
