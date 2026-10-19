// Minecraft Java Anvil (.mca) reader over HTTP Range requests. It reads the
// 8 KiB location header and then only the sectors of requested chunks; it never
// downloads a whole region file from a server that honours Range.
export const SECTOR=4096,HEADER_BYTES=8192,MAX_REQUEST_BYTES=2*1024*1024,MAX_INFLATED_BYTES=16*1024*1024,MAX_WHOLE_FILE_BYTES=256*1024;
export const HF_REPO='EricSpencer00/earthcraft';
// Immutable dataset commit the site reads by default (content at a SHA never changes, so CDNs and
// browsers may cache it). Bump together with dashboard/cache/*.json via scripts/build_view_cache.mjs.
export const PINNED_REVISION='770e8308816b3443f4f66c56b378e49ff8ae0480';
// When the world at PINNED_REVISION was captured; tiles/v2 regions published later replace it.
export const WORLD_CAPTURED_UTC='2026-10-16T18:21:13Z';
export const VIEW_CACHES=['./cache/water-tower.json'];
export const MAX_PACK_BYTES=8*1024*1024;
const REVISION=/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/,REPO=/^[-\w]+\/[-\w.]+$/;
// Published layout: world/region/<bx>/r.X.Z.mca with bx = floor(X / 16) (≤ 10k files per folder).
export function hfRegionBase(revision=PINNED_REVISION,repo=HF_REPO){
  if(!REVISION.test(revision))throw Error('Invalid dataset revision.');if(!REPO.test(repo))throw Error('Invalid dataset repository.');
  return `https://huggingface.co/datasets/${repo}/resolve/${revision}/world/region/`;
}
export const regionPath=(x,z)=>`${Math.floor(x/16)}/r.${x}.${z}.mca`;
export const chunkRegion=(cx,cz)=>[cx>>5,cz>>5];
export const chunkIndex=(cx,cz)=>(cx&31)+(cz&31)*32;
export const chunkPresent=(bitmap,i)=>((bitmap[i>>3]>>(i&7))&1)===1;

export function parseHeader(bytes){
  if(!(bytes instanceof Uint8Array)||bytes.length<HEADER_BYTES)throw Error('Region header is shorter than 8 KiB.');
  const view=new DataView(bytes.buffer,bytes.byteOffset,HEADER_BYTES),offsets=new Uint32Array(1024),sectors=new Uint8Array(1024),timestamps=new Uint32Array(1024),bitmap=new Uint8Array(128);let count=0;
  for(let i=0;i<1024;i++){
    const offset=view.getUint32(i*4)>>>8,n=bytes[i*4+3];timestamps[i]=view.getUint32(SECTOR+i*4);
    if(offset>=2&&n>0){offsets[i]=offset;sectors[i]=n;bitmap[i>>3]|=1<<(i&7);count++;}
  }
  return {offsets,sectors,timestamps,bitmap,count};
}

// Coverage index published beside the regions: world/index/regions.json.
export function parseRegionIndex(value){
  if(value?.schema!=='earthcraft.world-regions.v1'||!Array.isArray(value.regions))throw Error('Unsupported region index.');
  return value.regions.map(r=>{
    if(!Number.isSafeInteger(r.x)||!Number.isSafeInteger(r.z)||r.path!=='world/region/'+regionPath(r.x,r.z))throw Error('Invalid region index entry.');
    const bitmap=Uint8Array.from(atob(r.chunk_bitmap||''),c=>c.charCodeAt(0));if(bitmap.length!==128)throw Error('Region chunk bitmap must be 128 bytes.');
    return {x:r.x,z:r.z,bytes:r.bytes,sha256:r.sha256,chunks:r.chunks,bitmap};
  });
}

// NBT: big-endian, all 13 tag types. Long arrays keep hi/lo u32 words for fast unpacking.
export class LongArray{
  constructor(words){this.words=words;this.length=words.length>>>1;}
  at(i){return BigInt.asIntN(64,BigInt(this.words[2*i])<<32n|BigInt(this.words[2*i+1]));}
  toBigInt64Array(){const out=new BigInt64Array(this.length);for(let i=0;i<this.length;i++)out[i]=this.at(i);return out;}
}
export const TAG_NAMES=['end','byte','short','int','long','float','double','byte_array','string','list','compound','int_array','long_array'];
const utf8=new TextDecoder();
export function readNBT(bytes,{maxDepth=512}={}){
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),end=bytes.length;let p=0;
  const need=n=>{if(n<0||p+n>end)throw Error('NBT data ends early.');};
  const count=size=>{need(4);const n=view.getInt32(p);p+=4;if(n<0||n*size>end-p)throw Error('NBT array length is invalid.');return n;};
  const string=()=>{need(2);const n=view.getUint16(p);p+=2;need(n);const s=utf8.decode(bytes.subarray(p,p+n));p+=n;return s;};
  function payload(type,depth){
    if(depth>maxDepth)throw Error('NBT nesting is too deep.');
    let v;
    switch(type){
      case 1:need(1);return view.getInt8(p++);
      case 2:need(2);v=view.getInt16(p);p+=2;return v;
      case 3:need(4);v=view.getInt32(p);p+=4;return v;
      case 4:need(8);v=view.getBigInt64(p);p+=8;return v;
      case 5:need(4);v=view.getFloat32(p);p+=4;return v;
      case 6:need(8);v=view.getFloat64(p);p+=8;return v;
      case 7:{const n=count(1);v=new Int8Array(bytes.slice(p,p+n).buffer);p+=n;return v;}
      case 8:return string();
      case 9:{need(1);const t=bytes[p++];const n=count(t===0?0:1);if(t>12||(t===0&&n>0))throw Error('NBT list type is invalid.');const list=new Array(n);for(let i=0;i<n;i++)list[i]=payload(t,depth+1);return list;}
      case 10:{const o={};for(;;){need(1);const t=bytes[p++];if(t===0)return o;if(t>12)throw Error('Unknown NBT tag '+t+'.');const k=string(),value=payload(t,depth+1);if(k==='__proto__')Object.defineProperty(o,k,{value,enumerable:true,writable:true,configurable:true});else o[k]=value;}}
      case 11:{const n=count(4),a=new Int32Array(n);for(let i=0;i<n;i++,p+=4)a[i]=view.getInt32(p);return a;}
      case 12:{const n=count(8),w=new Uint32Array(n*2);for(let i=0;i<n*2;i++,p+=4)w[i]=view.getUint32(p);return new LongArray(w);}
      default:throw Error('Unknown NBT tag '+type+'.');
    }
  }
  need(1);if(bytes[p++]!==10)throw Error('NBT root is not a compound.');
  const name=string();return {name,value:payload(10,0),bytesRead:p};
}

function concat(parts,total){const out=new Uint8Array(total);let o=0;for(const part of parts){out.set(part,o);o+=part.length;}return out;}
async function readLimited(stream,limit,message,signal){
  const reader=stream.getReader(),parts=[];let total=0;
  try{for(;;){signal?.throwIfAborted();const {value,done}=await reader.read();if(done)break;total+=value.byteLength;if(total>limit)throw Error(message);parts.push(value);}}
  catch(error){await reader.cancel().catch(()=>{});throw error;}
  return concat(parts,total);
}
export async function inflate(bytes,format,limit=MAX_INFLATED_BYTES){
  return readLimited(new Blob([bytes]).stream().pipeThrough(new DecompressionStream(format)),limit,`Chunk NBT exceeds ${limit>>20} MiB when inflated.`);
}
// One stored chunk: 4-byte big-endian length, 1-byte compression, payload.
export async function readChunkPayload(bytes){
  if(bytes.length<5)throw Error('Chunk record is truncated.');
  const length=new DataView(bytes.buffer,bytes.byteOffset,4).getUint32(0),type=bytes[4];
  if(length<1||length+4>bytes.length)throw Error('Chunk length exceeds its sectors.');
  if(type&128)throw Error('Chunk is stored in an external .mcc file, which this viewer does not read.');
  const data=bytes.subarray(5,4+length);
  if(type===1)return inflate(data,'gzip');
  if(type===2)return inflate(data,'deflate');
  if(type===3)return data;
  if(type===4)throw Error('LZ4 chunk compression (type 4) is not supported.');
  throw Error('Unknown chunk compression type '+type+'.');
}

// 1.18+ sections: palette indices packed into longs; entries never span longs.
export function unpackIndices(data,bits,paletteLength,out){
  const per=Math.floor(64/bits),mask=(1<<bits)-1,words=data.words;
  if(data.length<Math.ceil(4096/per))throw Error('Section block data is too short.');
  for(let i=0,l=0;i<4096;l++){
    const hi=words[2*l],lo=words[2*l+1];
    for(let j=0,s=0;j<per&&i<4096;j++,s+=bits,i++){
      const v=(s+bits<=32?lo>>>s:s>=32?hi>>>(s-32):(lo>>>s)|(hi<<(32-s)))&mask;
      if(v>=paletteLength)throw Error('Section palette index is out of range.');
      out[i]=v;
    }
  }
  return out;
}
export function decodeChunk(root){
  const c=root?.Level&&!root.sections?root.Level:root;
  if(!c||typeof c!=='object')throw Error('Chunk NBT is empty.');
  const version=Number(root.DataVersion??c.DataVersion);if(!(version>=2860))throw Error('Chunk predates the 1.18 section format (DataVersion '+(root.DataVersion??c.DataVersion??'missing')+').');
  const sections=[];
  for(const s of c.sections||[]){
    const states=s.block_states;if(!states||!Array.isArray(states.palette)||!states.palette.length)continue;
    const palette=states.palette.map(e=>({name:String(e.Name),properties:e.Properties||null}));
    if(palette.length===1){sections.push({y:s.Y,palette,indices:null});continue;}
    if(!(states.data instanceof LongArray))throw Error('Section has a palette but no block data.');
    const bits=Math.max(4,Math.ceil(Math.log2(palette.length)));
    sections.push({y:s.Y,palette,indices:unpackIndices(states.data,bits,palette.length,palette.length<=256?new Uint8Array(4096):new Uint16Array(4096))});
  }
  sections.sort((a,b)=>a.y-b.y);
  return {x:c.xPos,z:c.zPos,dataVersion:version,status:c.Status,minSectionY:c.yPos,sections};
}
export function blockAt(chunk,x,y,z){
  const s=chunk.sections.find(section=>section.y===y>>4);if(!s)return null;
  return s.palette[s.indices?s.indices[((y&15)*16+z)*16+x]:0];
}

class LRU{
  constructor(limit){this.limit=limit;this.map=new Map();}
  get(k){const v=this.map.get(k);if(v!==undefined){this.map.delete(k);this.map.set(k,v);}return v;}
  set(k,v){this.map.delete(k);this.map.set(k,v);while(this.map.size>this.limit)this.map.delete(this.map.keys().next().value);}
}

export class RegionClient{
  constructor({base,layout='bucketed',fetch:fetcher=(...a)=>globalThis.fetch(...a),packs=[],maxConcurrent=6,headerCache=64,chunkCache=320,gapSectors=8,maxRequestBytes=MAX_REQUEST_BYTES}={}){
    if(typeof base!=='string'||!base)throw Error('A region base URL is required.');
    const url=new URL(base.endsWith('/')?base:base+'/',globalThis.location?.href);
    if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw Error('Region base must be an http(s) URL without credentials.');
    if(!['bucketed','flat'].includes(layout))throw Error('Unknown region layout.');
    this.base=url.href;this.layout=layout;this.fetch=fetcher;this.max=maxConcurrent;this.active=0;this.queue=[];
    this.headers=new LRU(headerCache);this.pending=new Map();this.chunkCache=new LRU(chunkCache);this.gap=gapSectors;this.maxRequestBytes=Math.max(HEADER_BYTES,Math.min(maxRequestBytes,MAX_REQUEST_BYTES));
    this.stats={requests:0,bytes:0,packRequests:0,packBytes:0};
    // Signed CDN URL each region's resolve URL redirected to; reusing it skips one 302 per read.
    this.resolved=new Map();this.packUrls=packs;this.packs=null;
    // Per-region URL overrides (for example a newer tiles/v2 publication): "rx,rz" → absolute URL.
    this.overrides=new Map();
  }
  setOverride(rx,rz,href){
    const key=rx+','+rz,url=new URL(href,globalThis.location?.href);
    if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw Error('Region override must be an http(s) URL without credentials.');
    if(this.overrides.get(key)===url.href)return;
    this.overrides.set(key,url.href);this.headers.map.delete(key);
  }
  // Prepacked chunk records (scripts/build_view_cache.mjs). Used only when the pack was built from
  // the same repo+revision this client reads; any failure falls back to Range reads.
  loadPacks(){
    return this.packs??=Promise.all(this.packUrls.map(async href=>{
      try{
        const indexUrl=new URL(href,globalThis.location?.href),r=await this.fetch(indexUrl.href,{credentials:'omit'});this.stats.packRequests++;
        if(!r.ok){r.body?.cancel().catch(()=>{});return null;}
        const m=await r.json();
        if(m?.schema!=='earthcraft.view-cache.v1'||hfRegionBase(m.revision,m.repo)!==this.base||!(m.pack?.bytes<=MAX_PACK_BYTES))return null;
        const b=await this.fetch(new URL(m.pack.path,indexUrl).href,{credentials:'omit'});this.stats.packRequests++;
        if(!b.ok){b.body?.cancel().catch(()=>{});return null;}
        const bytes=await readLimited(b.body,MAX_PACK_BYTES,'View cache is larger than declared.');this.stats.packBytes+=bytes.length;
        if(bytes.length!==m.pack.bytes)return null;
        if(globalThis.crypto?.subtle){const hex=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');if(hex!==m.pack.sha256)return null;}
        return {bytes,chunks:new Map(Object.entries(m.chunks))};
      }catch{return null;}
    })).then(list=>list.filter(Boolean));
  }
  fromPack(packs,cx,cz){
    for(const p of packs){const e=p.chunks.get(cx+','+cz);if(e&&e[0]+e[1]<=p.bytes.length)return p.bytes.subarray(e[0],e[0]+e[1]);}
    return null;
  }
  url(rx,rz){const o=this.overrides.get(rx+','+rz);if(o)return o;return new URL(this.layout==='flat'?`r.${rx}.${rz}.mca`:regionPath(rx,rz),this.base).href;}
  slot(signal){
    signal?.throwIfAborted();
    if(this.active<this.max){this.active++;return Promise.resolve();}
    return new Promise((resolve,reject)=>{const waiter={resolve,reject};this.queue.push(waiter);signal?.addEventListener('abort',()=>{const i=this.queue.indexOf(waiter);if(i>=0){this.queue.splice(i,1);reject(signal.reason);}},{once:true});});
  }
  release(){const next=this.queue.shift();if(next)next.resolve();else this.active--;}
  async range(url,start,length,signal){
    if(length<1||length>this.maxRequestBytes)throw Error('Range request exceeds the per-request cap.');
    await this.slot(signal);
    try{
      const init={headers:{Range:`bytes=${start}-${start+length-1}`},signal,credentials:'omit',referrerPolicy:'no-referrer'},direct=this.resolved.get(url);
      let response=await this.fetch(direct||url,init);this.stats.requests++;
      if(direct&&response.status>=400&&response.status!==416){// signed URL expired: go back through the resolve URL
        response.body?.cancel().catch(()=>{});this.resolved.delete(url);response=await this.fetch(url,init);this.stats.requests++;
      }
      if(!direct&&response.redirected&&response.url&&response.url!==url&&[200,206].includes(response.status))this.resolved.set(url,response.url);
      if(response.status===404){response.body?.cancel().catch(()=>{});return null;}
      if(response.status===416){response.body?.cancel().catch(()=>{});return new Uint8Array(0);}
      if(response.status===206){
        const range=/^bytes (\d+)-(\d+)\//.exec(response.headers.get('content-range')||'');
        if(range&&Number(range[1])!==start){response.body?.cancel().catch(()=>{});throw Error('Server returned a different byte range.');}
        const bytes=await readLimited(response.body,length,'Server returned more bytes than requested.',signal);this.stats.bytes+=bytes.length;return bytes;
      }
      if(response.status===200){
        // Server ignored Range. Accept only small files (for example a 16-chunk preview region).
        const declared=Number(response.headers.get('content-length'));
        if(declared>MAX_WHOLE_FILE_BYTES){response.body?.cancel().catch(()=>{});throw Error('Region host ignored the Range request; refusing a whole-file download.');}
        const bytes=await readLimited(response.body,MAX_WHOLE_FILE_BYTES,'Region host ignored the Range request; refusing a whole-file download.',signal);this.stats.bytes+=bytes.length;
        return bytes.subarray(start,start+length);
      }
      response.body?.cancel().catch(()=>{});
      throw Error(`Region request failed (${response.status}).`);
    }finally{this.release();}
  }
  // Resolves to the parsed header, or null when the region file does not exist.
  header(rx,rz,signal){
    const key=rx+','+rz,cached=this.headers.get(key);if(cached!==undefined)return Promise.resolve(cached);
    if(this.pending.has(key))return this.pending.get(key);
    const promise=this.range(this.url(rx,rz),0,HEADER_BYTES,signal).then(bytes=>{
      const header=!bytes||bytes.length===0?null:bytes.length<HEADER_BYTES?(()=>{throw Error('Region header is shorter than 8 KiB.');})():parseHeader(bytes);
      this.headers.set(key,header);return header;
    }).finally(()=>this.pending.delete(key));
    this.pending.set(key,promise);return promise;
  }
  // Loads chunks by absolute chunk coordinate. Nearby sectors in one region are coalesced
  // into a single bounded Range request. onChunk receives {cx,cz,status,chunk?,error?}.
  async loadChunks(coords,{signal,onChunk=()=>{}}={}){
    const byRegion=new Map(),results=[];
    const report=r=>{results.push(r);onChunk(r);};
    const packs=this.packUrls.length?await this.loadPacks():[];signal?.throwIfAborted();
    for(const [cx,cz] of coords){
      const record=packs.length&&!this.overrides.has(chunkRegion(cx,cz).join(','))&&this.fromPack(packs,cx,cz);
      if(record){
        try{const chunk=decodeChunk(readNBT(await readChunkPayload(record)).value);signal?.throwIfAborted();report({cx,cz,status:'ok',chunk,packed:true});continue;}
        catch(error){if(signal?.aborted)throw error;}// fall through to a Range read
      }const [rx,rz]=chunkRegion(cx,cz),k=rx+','+rz;if(!byRegion.has(k))byRegion.set(k,{rx,rz,items:[]});byRegion.get(k).items.push({cx,cz});}
    await Promise.all([...byRegion.values()].map(async({rx,rz,items})=>{
      let header;
      try{header=await this.header(rx,rz,signal);}catch(error){if(signal?.aborted)throw error;for(const {cx,cz} of items)report({cx,cz,status:'error',error});return;}
      if(!header){for(const {cx,cz} of items)report({cx,cz,status:'no-region'});return;}
      const wanted=[];
      for(const item of items){
        const i=chunkIndex(item.cx,item.cz),offset=header.offsets[i],n=header.sectors[i];
        if(!offset){report({...item,status:'absent'});continue;}
        const cached=this.chunkCache.get(this.url(rx,rz)+'|'+item.cx+','+item.cz);
        if(cached&&cached.timestamp===header.timestamps[i]){report({...item,status:'ok',chunk:cached.chunk,cached:true});continue;}
        wanted.push({...item,offset,n,timestamp:header.timestamps[i]});
      }
      wanted.sort((a,b)=>a.offset-b.offset);
      const spans=[],limit=Math.floor(this.maxRequestBytes/SECTOR);
      for(const item of wanted){
        const last=spans.at(-1);
        if(last&&item.offset-last.end<=this.gap&&item.offset+item.n-last.start<=limit){last.end=Math.max(last.end,item.offset+item.n);last.items.push(item);}
        else spans.push({start:item.offset,end:item.offset+item.n,items:[item]});
      }
      const url=this.url(rx,rz);
      await Promise.all(spans.map(async span=>{
        let bytes;
        try{bytes=await this.range(url,span.start*SECTOR,(span.end-span.start)*SECTOR,signal);if(!bytes)throw Error('Region file disappeared.');}
        catch(error){if(signal?.aborted)throw error;for(const item of span.items)report({cx:item.cx,cz:item.cz,status:'error',error});return;}
        for(const item of span.items){
          try{
            const at=(item.offset-span.start)*SECTOR,chunk=decodeChunk(readNBT(await readChunkPayload(bytes.subarray(at,at+item.n*SECTOR))).value);
            signal?.throwIfAborted();
            this.chunkCache.set(url+'|'+item.cx+','+item.cz,{chunk,timestamp:item.timestamp});report({cx:item.cx,cz:item.cz,status:'ok',chunk});
          }catch(error){if(signal?.aborted)throw error;report({cx:item.cx,cz:item.cz,status:'error',error});}
        }
      }));
    }));
    return results;
  }
}
