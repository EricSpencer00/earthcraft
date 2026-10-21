// Lake Michigan water tiles built in the browser from the published bathymetry crop
// (lake/v1 in the dataset). Mirrors scripts/lake_tiles.py: bed y = 60 + round(depth),
// a two-block sand bed, water to y=60, air above. Chunks have the shape decodeChunk returns.
import {frameInverse} from './frame.mjs?v=frame-1';

export const LAKE_SURFACE_Y=60,LAKE_MIN_Y=-64,LAKE_BED_DEPTH=2,TILE=512;
const NODATA=-32768;
const block=name=>({name:'minecraft:'+name,properties:null});
const AIR=block('air'),SAND=block('sand'),WATER=block('water');

// numpy.round: halves go to the even neighbour.
function roundHalfEven(v){const f=Math.floor(v),d=v-f;return d>0.5||d===0.5&&f%2!==0?f+1:f;}

export class LakeSource{
  // header/grid: bathymetry.json and the Int16Array of bathymetry.bin.gz; tiles: index.json tiles.
  constructor({header,grid,tiles,projection}){
    this.header=header;this.grid=grid;this.tiles=tiles;this.place=frameInverse(projection);
  }
  static async load(base,fetcher=globalThis.fetch){
    const get=async(name,kind)=>{const r=await fetcher(new URL(name,base).href,{credentials:'omit'});if(!r.ok)throw Error(`Lake ${name} unavailable (${r.status}).`);return r[kind]();};
    const gunzip=async response=>new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    const raw=async name=>{const r=await fetcher(new URL(name,base).href,{credentials:'omit'});if(!r.ok)throw Error(`Lake ${name} unavailable (${r.status}).`);return gunzip(r);};
    const [header,index,bin]=await Promise.all([get('bathymetry.json','json'),get('index.json','json'),raw('bathymetry.bin.gz')]);
    if(index.schema!=='earthcraft.lake-tiles.v1'||bin.byteLength!==header.width*header.height*2)throw Error('Unsupported lake data.');
    return new LakeSource({header,grid:new Int16Array(bin),tiles:new Set(Object.keys(index.tiles)),projection:index.projection});
  }
  hasChunk(cx,cz){return this.tiles.has(`${Math.floor(cx/32)}_${Math.floor(cz/32)}`);}
  depth(x,z){
    const {west,north,step_deg:step,width,height,scale}=this.header;
    const [lon,lat]=this.place(x,z);
    const col=(lon-west)/step-0.5,row=(north-lat)/step-0.5,c0=Math.floor(col),r0=Math.floor(row);
    if(c0<0||r0<0||c0+1>=width||r0+1>=height)return NaN;
    const fc=col-c0,fr=row-r0,g=this.grid,at=(r,c)=>{const v=g[r*width+c];return v===NODATA?NaN:v*scale;};
    return at(r0,c0)*(1-fc)*(1-fr)+at(r0,c0+1)*fc*(1-fr)+at(r0+1,c0)*(1-fc)*fr+at(r0+1,c0+1)*fc*fr;
  }
  bed(x,z){
    const d=this.depth(x+0.5,z+0.5),bed=LAKE_SURFACE_Y+roundHalfEven(Number.isNaN(d)?-1:d);
    return Math.max(LAKE_MIN_Y+LAKE_BED_DEPTH-1,Math.min(LAKE_SURFACE_Y-1,bed));
  }
  chunk(cx,cz){
    const beds=new Int16Array(256);
    for(let z=0;z<16;z++)for(let x=0;x<16;x++)beds[z*16+x]=this.bed(cx*16+x,cz*16+z);
    const sections=[];
    for(let sy=LAKE_MIN_Y>>4;sy<=LAKE_SURFACE_Y>>4;sy++){
      const indices=new Uint8Array(4096);let used=0;
      for(let ly=0;ly<16;ly++){
        const y=sy*16+ly;
        for(let i=0;i<256;i++){
          const bed=beds[i],kind=y>LAKE_SURFACE_Y?0:y>bed?2:y>bed-LAKE_BED_DEPTH?1:0;
          indices[ly*256+i]=kind;used|=1<<kind;
        }
      }
      if(used===1)continue;// all air
      sections.push(used===4?{y:sy,palette:[WATER],indices:null}:{y:sy,palette:[AIR,SAND,WATER],indices});
    }
    return {x:cx,z:cz,dataVersion:4556,status:'minecraft:full',minSectionY:LAKE_MIN_Y>>4,sections};
  }
  // Same contract as RegionClient.loadChunks.
  async loadChunks(coords,{signal,onChunk=()=>{}}={}){
    const results=[];
    for(const [cx,cz] of coords){
      signal?.throwIfAborted();
      const r=this.hasChunk(cx,cz)?{cx,cz,status:'ok',chunk:this.chunk(cx,cz)}:{cx,cz,status:'no-region'};
      results.push(r);onChunk(r);
    }
    return results;
  }
}

// RegionClient-shaped wrapper so the block viewer and cell preview can read lake tiles.
export class LakeClient{
  constructor({base,fetch:fetcher=(...a)=>globalThis.fetch(...a)}){
    this.base=new URL(base.endsWith('/')?base:base+'/',globalThis.location?.href).href;this.fetch=fetcher;
    this.headers=new Map();this.stats={requests:0,bytes:0,packRequests:0,packBytes:0};this.source=null;
  }
  url(){return new URL('bathymetry.bin.gz',this.base).href;}
  async loadChunks(coords,options={}){
    this.source??=LakeSource.load(this.base,this.fetch).then(source=>{
      this.stats.requests+=3;this.stats.bytes+=source.grid.byteLength;return source;});
    return (await this.source).loadChunks(coords,options);
  }
}
