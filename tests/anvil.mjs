// Anvil, NBT, palette and mesher checks against a real 9-chunk Water Tower fixture
// (tests/fixtures/anvil, byte-identical chunk records from the 1.21.10 save).
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdtemp,rm} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseHeader,parsePackedHeader,parseRegionIndex,readNBT,readChunkPayload,decodeChunk,unpackIndices,blockAt,RegionClient,LongArray,hfRegionBase,regionPath,chunkIndex,chunkPresent,SECTOR} from '../dashboard/anvil.mjs';
import {blockStyle} from '../dashboard/block-colors.mjs';
import {BlockWorld,prepareChunk,meshChunk,raycast} from '../dashboard/chunk-mesh.mjs';

const region=new Uint8Array(await readFile(new URL('./fixtures/anvil/water-tower-r.0.0.region',import.meta.url)));
const sources=JSON.parse(await readFile(new URL('./fixtures/anvil/sources.json',import.meta.url),'utf8'));
assert.equal(Buffer.from(await crypto.subtle.digest('SHA-256',region)).toString('hex'),sources.file.sha256,'fixture checksum');

// Layout helpers.
assert.equal(hfRegionBase('0123abcd'),'https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/0123abcd/world/region/');
assert.throws(()=>hfRegionBase('../main'),/revision/);
assert.equal(regionPath(-1,3),'-1/r.-1.3.mca');assert.equal(regionPath(-17,0),'-2/r.-17.0.mca');assert.equal(regionPath(16,-5),'1/r.16.-5.mca');
assert.equal(chunkIndex(-1,-1),1023);assert.equal(chunkIndex(33,2),65);

// Synthetic NBT with all 13 tag types.
function nbt(){
  const out=[],u8=v=>out.push(v&255),u16=v=>{u8(v>>8);u8(v);},i32=v=>{for(const s of [24,16,8,0])u8(v>>s);},str=s=>{const b=new TextEncoder().encode(s);u16(b.length);out.push(...b);};
  const head=(t,n)=>{u8(t);str(n);};
  head(10,'root');
  head(1,'b');u8(-5);head(2,'s');u16(-300);head(3,'i');i32(-70000);
  head(4,'l');i32(-2);i32(-1);
  head(5,'f');out.push(...new Uint8Array(new Float32Array([1.5]).buffer).reverse());
  head(6,'d');out.push(...new Uint8Array(new Float64Array([-2.25]).buffer).reverse());
  head(7,'ba');i32(3);u8(1);u8(-1);u8(127);
  head(8,'str');str('minecraft:stone ✓');
  head(9,'list');u8(3);i32(2);i32(7);i32(8);
  head(9,'empty');u8(0);i32(0);
  head(10,'c');head(8,'Name');str('minecraft:water');head(10,'Properties');head(8,'level');str('0');u8(0);u8(0);
  head(11,'ia');i32(2);i32(1);i32(-1);
  head(12,'la');i32(2);i32(0x12345678);i32(0x9abcdef0>>>0);i32(-1);i32(-1);
  head(8,'__proto__');str('kept as data');
  u8(0);return new Uint8Array(out);
}
const parsed=readNBT(nbt()),v=parsed.value;
assert.equal(parsed.name,'root');
assert.deepEqual([v.b,v.s,v.i,v.l,v.f,v.d],[-5,-300,-70000,-4294967297n,1.5,-2.25]);
assert.deepEqual([...v.ba],[1,-1,127]);assert.equal(v.str,'minecraft:stone ✓');assert.deepEqual(v.list,[7,8]);assert.deepEqual(v.empty,[]);
assert.deepEqual(v.c,{Name:'minecraft:water',Properties:{level:'0'}});assert.deepEqual([...v.ia],[1,-1]);
assert.ok(v.la instanceof LongArray);assert.equal(v.la.at(0),0x123456789abcdef0n);assert.equal(v.la.at(1),-1n);assert.deepEqual([...v.la.toBigInt64Array()],[0x123456789abcdef0n,-1n]);
assert.equal(Object.getPrototypeOf(v),Object.prototype);assert.equal(Object.getOwnPropertyDescriptor(v,'__proto__').value,'kept as data');
assert.throws(()=>readNBT(nbt().subarray(0,40)),/ends early/);
assert.throws(()=>readNBT(new Uint8Array([8,0,0])),/root is not a compound/);
assert.throws(()=>readNBT(new Uint8Array([10,0,0,9,0,1,'x'.charCodeAt(0),3,0x7f,0xff,0xff,0xff])),/length is invalid/);
console.log('NBT reader: 13 tag types, bounds and prototype safety passed');

// Real region header and chunks.
const header=parseHeader(region);
assert.equal(header.count,9);
for(let cz=1;cz<=3;cz++)for(let cx=1;cx<=3;cx++)assert.ok(chunkPresent(header.bitmap,chunkIndex(cx,cz)),`chunk ${cx},${cz} present`);
assert.ok(!chunkPresent(header.bitmap,chunkIndex(0,0)));
const record=(cx,cz)=>{const i=chunkIndex(cx,cz),at=header.offsets[i]*SECTOR;return region.subarray(at,at+header.sectors[i]*SECTOR);};
const raw=await readChunkPayload(record(2,2)),root=readNBT(raw).value,chunk=decodeChunk(root);
assert.equal(record(2,2)[4],2,'zlib record');
assert.deepEqual([chunk.x,chunk.z,chunk.dataVersion,chunk.status],[2,2,4556,'minecraft:full']);
assert.equal(chunk.sections.length,64);assert.deepEqual([chunk.sections[0].y,chunk.sections.at(-1).y],[-4,59]);
const names=new Set(chunk.sections.flatMap(s=>s.palette.map(p=>p.name)));
for(const name of ['minecraft:stone','minecraft:stone_bricks','minecraft:gray_concrete','minecraft:grass_block','minecraft:bedrock','minecraft:air'])assert.ok(names.has(name),name);
assert.equal(blockAt(chunk,3,80,0).name,'minecraft:stone_bricks');assert.equal(blockAt(chunk,3,81,0).name,'minecraft:air');
assert.equal(blockAt(chunk,0,-64,0).name,'minecraft:bedrock');assert.equal(blockAt(chunk,15,400,15).name,'minecraft:air');
assert.deepEqual(chunk.sections.flatMap(s=>s.palette).find(p=>p.name==='minecraft:grass_block').properties,{snowy:'false'});
// Packed palette indices against an independent BigInt decoder (entries never span longs).
let checked=0;
for(const s of root.sections){
  const states=s.block_states;if(states.palette.length<2)continue;
  const bits=Math.max(4,Math.ceil(Math.log2(states.palette.length))),per=Math.floor(64/bits),longs=states.data.toBigInt64Array(),ref=new Uint16Array(4096);
  for(let i=0;i<4096;i++)ref[i]=Number((BigInt.asUintN(64,longs[Math.floor(i/per)])>>BigInt((i%per)*bits))&((1n<<BigInt(bits))-1n));
  assert.deepEqual(unpackIndices(states.data,bits,states.palette.length,new Uint16Array(4096)),ref);checked++;
}
assert.ok(checked>=5,'mixed sections checked');
// Wider palettes: 5..12 bits per entry, including widths that leave unused high bits.
for(const bits of [5,6,7,9,12]){const per=Math.floor(64/bits),n=Math.ceil(4096/per),words=new Uint32Array(2*n),want=new Uint16Array(4096),len=1<<bits;
  for(let i=0;i<4096;i++){want[i]=(i*2654435761>>>7)%len;const l=Math.floor(i/per),s=(i%per)*bits,v=BigInt(want[i])<<BigInt(s),hi=Number(v>>32n),lo=Number(v&0xffffffffn);words[2*l]|=hi;words[2*l+1]|=lo;}
  assert.deepEqual(unpackIndices(new LongArray(words),bits,len,new Uint16Array(4096)),want,`${bits}-bit entries`);}
console.log(`Real 1.21.10 chunk: header, ${chunk.sections.length} sections, ${checked} packed sections and block lookups passed`);

// Compression types: gzip, uncompressed, LZ4 and external chunks.
const pack=(type,payload)=>{const out=new Uint8Array(5+payload.length);new DataView(out.buffer).setUint32(0,payload.length+1);out[4]=type;out.set(payload,5);return out;};
const gz=new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer());
assert.deepEqual(decodeChunk(readNBT(await readChunkPayload(pack(1,gz))).value).sections.length,64);
assert.deepEqual(await readChunkPayload(pack(3,raw)),raw);
await assert.rejects(readChunkPayload(pack(4,raw)),/LZ4/);
await assert.rejects(readChunkPayload(pack(0x82,new Uint8Array(0))),/external \.mcc/);
await assert.rejects(readChunkPayload(new Uint8Array([0,0,1,0,2,0])),/exceeds its sectors/);
assert.throws(()=>decodeChunk({DataVersion:1343,Level:{}}),/1\.18/);
console.log('Compression types and unsupported formats passed');

// Range client against an in-memory host.
function host({ignoreRange=false,file=region}={}){
  const log=[];
  const fetcher=async(url,{headers,signal})=>{
    signal?.throwIfAborted();const path=new URL(url).pathname;log.push([path,headers.Range]);
    if(path!=='/world/region/0/r.0.0.mca')return new Response('Entry not found',{status:404});
    if(ignoreRange)return new Response(file,{status:200,headers:{'content-length':String(file.length)}});
    const [,a,b]=/bytes=(\d+)-(\d+)/.exec(headers.Range).map(Number);if(a>=file.length)return new Response('',{status:416});
    const end=Math.min(b,file.length-1);return new Response(file.slice(a,end+1),{status:206,headers:{'content-range':`bytes ${a}-${end}/${file.length}`}});
  };
  return {fetcher,log};
}
{
  const {fetcher,log}=host(),client=new RegionClient({base:'https://example.test/world/region',fetch:fetcher});
  const results=await client.loadChunks([[2,2],[1,1],[3,3],[1,2],[2,1],[3,1],[1,3],[2,3],[3,2],[5,5],[40,2]]);
  const by=Object.fromEntries(results.map(r=>[r.cx+','+r.cz,r.status]));
  assert.equal(results.filter(r=>r.status==='ok').length,9);assert.equal(by['5,5'],'absent');assert.equal(by['40,2'],'no-region');
  assert.ok(log.every(([,range])=>/^bytes=\d+-\d+$/.test(range)),'every request is ranged');
  assert.deepEqual(log.filter(([p])=>p.endsWith('r.0.0.mca'))[0][1],'bytes=0-8191');
  assert.ok(log.length<=4,`9 adjacent chunks coalesced (${log.length} requests)`);
  const cachedRequests=log.length,again=await client.loadChunks([[2,2]]);assert.ok(again[0].cached);assert.equal(log.length,cachedRequests);
  const tiny=new RegionClient({base:'https://example.test/world/region/',fetch:host().fetcher,gapSectors:0,maxRequestBytes:SECTOR});// raised to the 8 KiB header minimum
  assert.equal((await tiny.loadChunks([[1,1],[2,2],[3,3]])).filter(r=>r.status==='ok').length,3);assert.ok(tiny.stats.requests>=3,'per-request byte cap splits spans');
  const small=new RegionClient({base:'https://example.test/world/region/',fetch:host({ignoreRange:true}).fetcher});
  assert.equal((await small.loadChunks([[2,2]]))[0].status,'ok','small file from a host that ignores Range');
  const big=new Uint8Array(512*1024);big.set(region);
  const refused=await new RegionClient({base:'https://example.test/world/region/',fetch:host({ignoreRange:true,file:big}).fetcher}).loadChunks([[2,2]]);
  assert.equal(refused[0].status,'error');assert.match(refused[0].error.message,/ignored the Range request/);
  const controller=new AbortController();controller.abort();
  await assert.rejects(new RegionClient({base:'https://example.test/world/region/',fetch:host().fetcher}).loadChunks([[2,2]],{signal:controller.signal}));
  assert.throws(()=>new RegionClient({base:'ftp://example.test/'}),/http/);
}
console.log('Range requests: header-first reads, coalescing, caching, caps, 404s and aborts passed');

// Packed regions (scripts/region_pack.py) decode to the same chunks as the .mca they came from.
{
  const dir=await mkdtemp(join(tmpdir(),'earthcraft-pack-'));
  try{
    const source=join(dir,'r.0.0.mca'),target=join(dir,'r.0.0.mcp');await writeFile(source,region);
    execFileSync('python3',['-I',fileURLToPath(new URL('../scripts/region_pack.py',import.meta.url)),'pack',source,target]);
    const packed=new Uint8Array(await readFile(target));
    assert.throws(()=>parsePackedHeader(region),/Not a packed/);
    const header=parsePackedHeader(packed);assert.equal(header.count,parseHeader(region).count);
    const log=[],fetcher=async(url,{headers})=>{
      const path=new URL(url).pathname;log.push(path);if(path!=='/tiles/v2/0_0/region/r.0.0.mcp')return new Response('',{status:404});
      const [,a,b]=/bytes=(\d+)-(\d+)/.exec(headers.Range).map(Number),end=Math.min(b,packed.length-1);
      return new Response(packed.slice(a,end+1),{status:206,headers:{'content-range':`bytes ${a}-${end}/${packed.length}`}});
    };
    const coords=[];for(let z=0;z<32;z++)for(let x=0;x<32;x++)coords.push([x,z]);
    const fromPack=await new RegionClient({base:'https://example.test/tiles/v2/0_0/region/',layout:'flat-packed',fetch:fetcher}).loadChunks(coords);
    const fromMca=await new RegionClient({base:'https://example.test/world/region/',fetch:host().fetcher}).loadChunks(coords);
    const view=list=>Object.fromEntries(list.map(r=>[r.cx+','+r.cz,r.status==='ok'?JSON.stringify(r.chunk.sections.map(s=>[s.y,s.palette,s.indices?[...s.indices]:null])):r.status]));
    assert.deepEqual(view(fromPack),view(fromMca));assert.ok(fromPack.some(r=>r.status==='ok'));
    // flat-auto reads a packed tile, and falls back to .mca for tiles published before packing.
    const serve=(path,file)=>async(url,{headers})=>{
      if(new URL(url).pathname!==path)return new Response('',{status:404});
      const [,a,b]=/bytes=(\d+)-(\d+)/.exec(headers.Range).map(Number),end=Math.min(b,file.length-1);
      return new Response(file.slice(a,end+1),{status:206,headers:{'content-range':`bytes ${a}-${end}/${file.length}`}});
    };
    const autoPacked=new RegionClient({base:'https://example.test/tiles/v2/0_0/region/',layout:'flat-auto',fetch:serve('/tiles/v2/0_0/region/r.0.0.mcp',packed)});
    const autoMca=new RegionClient({base:'https://example.test/tiles/v2/0_0/region/',layout:'flat-auto',fetch:serve('/tiles/v2/0_0/region/r.0.0.mca',region)});
    assert.deepEqual(view(await autoPacked.loadChunks(coords)),view(fromMca));
    assert.deepEqual(view(await autoMca.loadChunks(coords)),view(fromMca));
    assert.equal(autoMca.url(0,0),'https://example.test/tiles/v2/0_0/region/r.0.0.mca');
    assert.equal(autoPacked.url(0,0),'https://example.test/tiles/v2/0_0/region/r.0.0.mcp');
  }finally{await rm(dir,{recursive:true,force:true});}
}
console.log('Packed regions: Python-packed file decodes to the same chunks over Range passed');

// Coverage index published beside the regions.
const index=parseRegionIndex({schema:'earthcraft.world-regions.v1',regions:[{x:0,z:0,path:'world/region/0/r.0.0.mca',bytes:region.length,sha256:sources.file.sha256,chunks:9,chunk_bitmap:Buffer.from(header.bitmap).toString('base64')}]});
assert.ok(chunkPresent(index[0].bitmap,chunkIndex(2,2)));assert.ok(!chunkPresent(index[0].bitmap,chunkIndex(9,9)));
assert.throws(()=>parseRegionIndex({schema:'earthcraft.world-regions.v1',regions:[{x:-1,z:0,path:'world/region/0/r.-1.0.mca',chunk_bitmap:''}]}),/Invalid region index/);
console.log('Region index bitmap passed');

// Colors cover every block state in the fixture.
for(const s of chunk.sections)for(const p of s.palette){const style=blockStyle(p.name,p.properties);if(p.name!=='minecraft:air')assert.ok(style.known&&style.kind!=='skip',p.name);}
for(const [name,kind] of [['minecraft:gray_stained_glass','glass'],['minecraft:water','water'],['minecraft:oak_leaves','leaves'],['minecraft:poppy','cross'],['minecraft:cave_air','skip'],['minecraft:white_carpet','flat'],['minecraft:stone_brick_slab','slab'],['minecraft:oak_fence','post']])assert.equal(blockStyle(name).kind,kind,name);
assert.equal(blockStyle('minecraft:stone_brick_slab',{type:'top'}).kind,'slabtop');assert.deepEqual(blockStyle('minecraft:stone_brick_stairs').rgb,blockStyle('minecraft:stone_bricks').rgb);
assert.equal(blockStyle('minecraft:totally_new_block').known,false);
console.log('Block colors and shapes passed');

// Meshing: culling inside and across chunk borders, floors, transparency.
const synthetic=(cx,cz,blocks)=>{const palette=[{name:'minecraft:air'},{name:'minecraft:stone'},{name:'minecraft:glass'}],indices=new Uint8Array(4096);for(const [x,y,z,p] of blocks)indices[(y*16+z)*16+x]=p;return {x:cx,z:cz,sections:[{y:4,palette,indices}]};};
const faces=(chunks,floorY=-64)=>{const w=new BlockWorld();for(const c of chunks)w.set(prepareChunk(c,w.registry));return chunks.reduce((n,c)=>n+meshChunk(w,c.x,c.z,{floorY}).faces,0);};
assert.equal(faces([synthetic(0,0,[[5,5,5,1]])]),6);
assert.equal(faces([synthetic(0,0,[[5,5,5,1],[6,5,5,1]])]),10);
assert.equal(faces([synthetic(0,0,[[5,5,5,2],[6,5,5,2]])]),10,'glass culls glass');
assert.equal(faces([synthetic(0,0,[[5,5,5,1],[6,5,5,2]])]),11,'glass does not hide stone; stone hides the glass face');
assert.equal(faces([synthetic(0,0,[[15,5,5,1]]),synthetic(1,0,[[0,5,5,1]])]),10,'culling across a loaded border');
assert.equal(faces([synthetic(0,0,[[15,5,5,1]])]),6,'unloaded neighbour draws the border face');
assert.equal(faces([synthetic(0,0,[[5,5,5,1],[5,6,5,1]])],70),5,'cut-away floor hides the bottom face');
const water=new BlockWorld();water.set(prepareChunk({x:0,z:0,sections:[{y:4,palette:[{name:'minecraft:air'},{name:'minecraft:water',properties:{level:'0'}}],indices:Uint8Array.from({length:4096},(_,i)=>i<256?1:0)}]},water.registry));
const wm=meshChunk(water,0,0);assert.equal(wm.opaque.vertices,0);assert.ok(wm.transparent.vertices>0);assert.equal(Math.max(...wm.transparent.positions.filter((_,i)=>i%3===1)),64.875);
// Real chunks: a 3x3 mesh and a ray from above the tower.
const world=new BlockWorld();for(let cz=1;cz<=3;cz++)for(let cx=1;cx<=3;cx++)world.set(prepareChunk(decodeChunk(readNBT(await readChunkPayload(record(cx,cz))).value),world.registry));
const m=meshChunk(world,2,2,{floorY:60});assert.ok(m.faces>1000&&m.blocks>1000,'real mesh');assert.equal(m.opaque.positions.length,m.opaque.vertices*3);assert.ok(m.opaque.indices.every(i=>i<m.opaque.vertices));
const hit=raycast(world,[35.5,300,32.5],[0,-1,0],{floorY:60});assert.deepEqual([hit.x,hit.y,hit.z,hit.name],[35,80,32,'minecraft:stone_bricks']);
assert.equal(world.top(35,32),80);
console.log(`Mesher: culling, borders, floor, water and a real ${m.faces}-face chunk passed`);
