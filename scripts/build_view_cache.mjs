#!/usr/bin/env node
// Builds a prepacked chunk cache for one default viewer spot (the Chicago Water Tower) so the
// most-visited view costs two static fetches from GitHub Pages instead of ~150 HF Range reads.
// Chunk records are copied byte-for-byte from the raw region files at a pinned HF commit
// (4-byte length + 1-byte compression + payload; no re-encoding).
//   node scripts/build_view_cache.mjs --revision <40-hex HF commit> [--center 2,2] [--radius 8] [--out dashboard/cache/water-tower]
import {createHash} from 'node:crypto';
import {writeFile,statfs,mkdir} from 'node:fs/promises';
import {dirname} from 'node:path';
import {HF_REPO,HEADER_BYTES,SECTOR,parseHeader,regionPath,chunkRegion,chunkIndex,hfRegionBase} from '../dashboard/anvil.mjs';

export const PACK_SCHEMA='earthcraft.view-cache.v1',MAX_PACK_BYTES=8*2**20,MIN_FREE_BYTES=2*2**30;
const arg=(name,fallback)=>{const i=process.argv.indexOf('--'+name);return i>0?process.argv[i+1]:fallback;};

async function range(url,start,length){
  for(let attempt=0;;attempt++){
    const r=await fetch(url,{headers:{Range:`bytes=${start}-${start+length-1}`}});
    if(r.status===206){const b=new Uint8Array(await r.arrayBuffer());if(b.length!==length)throw Error(`short read ${url}`);return b;}
    await r.body?.cancel();if(attempt>=3)throw Error(`${url}: HTTP ${r.status}`);await new Promise(s=>setTimeout(s,1000*2**attempt));
  }
}

export async function buildViewCache({revision,center=[2,2],radius=8,repo=HF_REPO,name='water-tower'}){
  if(!/^[0-9a-f]{40}$/.test(revision))throw Error('--revision must be a 40-hex HF commit SHA (immutable).');
  const base=hfRegionBase(revision,repo);
  const index=await (await fetch(`https://huggingface.co/datasets/${repo}/resolve/${revision}/world/index/regions.json`)).json();
  const regionMeta=new Map(index.regions.map(r=>[`${r.x},${r.z}`,r]));
  const coords=[];for(let dz=-radius;dz<=radius;dz++)for(let dx=-radius;dx<=radius;dx++)coords.push([center[0]+dx,center[1]+dz]);
  const headers=new Map(),parts=[],chunks={},regions={};let offset=0;
  for(const [cx,cz] of coords){
    const [rx,rz]=chunkRegion(cx,cz),key=`${rx},${rz}`,url=base+regionPath(rx,rz);
    if(!headers.has(key)){
      const meta=regionMeta.get(key);if(!meta)throw Error(`region ${key} missing from regions.json`);
      regions[`r.${rx}.${rz}`]={path:meta.path,bytes:meta.bytes,sha256:meta.sha256};
      headers.set(key,parseHeader(await range(url,0,HEADER_BYTES)));
    }
    const h=headers.get(key),i=chunkIndex(cx,cz);if(!h.offsets[i])continue;
    const raw=await range(url,h.offsets[i]*SECTOR,h.sectors[i]*SECTOR),length=new DataView(raw.buffer).getUint32(0)+4;
    if(length<6||length>raw.length)throw Error(`bad chunk record ${cx},${cz}`);
    const record=raw.subarray(0,length);
    if(offset+length>MAX_PACK_BYTES)throw Error('pack exceeds the byte cap');
    chunks[`${cx},${cz}`]=[offset,length,h.timestamps[i]];parts.push(record);offset+=length;
  }
  const pack=Buffer.concat(parts);
  const manifest={schema:PACK_SCHEMA,name,repo,revision,center_chunk:center,radius,
    note:'Chunk records copied byte-identical from world/region/*.mca at this HF dataset revision. chunks: "cx,cz" -> [offset, length, timestamp].',
    pack:{path:`${name}.bin`,bytes:pack.length,sha256:createHash('sha256').update(pack).digest('hex')},regions,chunks};
  return {pack,manifest};
}

if(import.meta.url===`file://${process.argv[1]}`){
  const out=arg('out','dashboard/cache/water-tower'),center=arg('center','2,2').split(',').map(Number);
  const {pack,manifest}=await buildViewCache({revision:arg('revision'),center,radius:Number(arg('radius','8')),name:out.split('/').pop()});
  await mkdir(dirname(out),{recursive:true});
  const fs=await statfs(dirname(out));if(fs.bavail*fs.bsize-pack.length<MIN_FREE_BYTES)throw Error('refusing to write: free-space floor');
  await writeFile(out+'.bin',pack);await writeFile(out+'.json',JSON.stringify(manifest)+'\n');
  console.log(`${out}.bin ${pack.length} bytes, ${Object.keys(manifest.chunks).length} chunks @ ${manifest.revision}`);
}
