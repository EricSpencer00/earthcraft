// Block meshing without a renderer: face culling across loaded chunk borders,
// per-face shading by normal, vertex ambient occlusion, and a separate
// transparent buffer for glass, water and leaves. Output is plain typed arrays.
import {blockStyle} from './block-colors.mjs?v=blocks-1';

export const OPAQUE=1,TRANSLUCENT=2,SHAPE=4,WATER=8,CROSS=16;
const FLOOR=1;// reserved opaque id below the cut-away floor
export class BlockRegistry{
  constructor(){this.styles=[null,{rgb:[90,84,78],top:[90,84,78],bottom:[90,84,78],alpha:1,kind:'cube'}];this.names=['minecraft:air','(floor)'];this.byKey=new Map();this.flags=new Uint8Array(65536);this.flags[FLOOR]=OPAQUE;this.unknown=new Set();}
  id({name,properties}){
    const key=properties?name+'['+Object.keys(properties).sort().map(k=>k+'='+properties[k]).join(',')+']':name;
    let id=this.byKey.get(key);if(id!==undefined)return id;
    const style=blockStyle(name,properties);
    if(style.kind==='skip')id=0;
    else{
      id=this.styles.length;if(id>=65536)throw Error('Too many distinct block states.');
      this.styles.push(style);this.names.push(key);
      this.flags[id]=style.kind==='cube'?OPAQUE:style.kind==='water'?TRANSLUCENT|WATER:style.kind==='glass'||style.kind==='leaves'?TRANSLUCENT:style.kind==='cross'?CROSS:SHAPE;
      if(!style.known)this.unknown.add(name);
    }
    this.byKey.set(key,id);return id;
  }
}

// Convert a decoded chunk into global ids per section plus per-column top heights.
export function prepareChunk(chunk,registry){
  const sections=new Map();let minY=Infinity,maxY=-Infinity;
  for(const s of chunk.sections){
    const lut=s.palette.map(p=>registry.id(p));
    if(!s.indices){if(lut[0]===0)continue;sections.set(s.y,{uniform:lut[0]});}
    else{const ids=new Uint16Array(4096);let any=0;for(let i=0;i<4096;i++)any|=ids[i]=lut[s.indices[i]];if(!any)continue;sections.set(s.y,{ids});}
    minY=Math.min(minY,s.y*16);maxY=Math.max(maxY,s.y*16+15);
  }
  const prepared={cx:chunk.x,cz:chunk.z,sections,minY,maxY,tops:new Int16Array(256).fill(-32768)};
  if(sections.size){
    const ys=[...sections.keys()].sort((a,b)=>b-a);
    for(let i=0;i<256;i++){
      search:for(const sy of ys){const s=sections.get(sy);if(s.uniform){prepared.tops[i]=sy*16+15;break;}for(let ly=15;ly>=0;ly--)if(s.ids[ly*256+i]){prepared.tops[i]=sy*16+ly;break search;}}
    }
  }
  return prepared;
}
export function idAt(prepared,x,y,z){const s=prepared?.sections.get(y>>4);return !s?0:s.uniform??s.ids[((y&15)*16+z)*16+x];}

// World of prepared chunks keyed by absolute chunk coordinate.
export class BlockWorld{
  constructor(registry=new BlockRegistry()){this.registry=registry;this.chunks=new Map();}
  set(prepared){this.chunks.set(prepared.cx+','+prepared.cz,prepared);}
  get(cx,cz){return this.chunks.get(cx+','+cz)||null;}
  delete(cx,cz){this.chunks.delete(cx+','+cz);}
  block(x,y,z){return idAt(this.get(x>>4,z>>4),x&15,y,z&15);}
  top(x,z){const c=this.get(x>>4,z>>4);const t=c?c.tops[(z&15)*16+(x&15)]:-32768;return t===-32768?null:t;}
}

class Buffer{
  constructor(){this.pos=new Float32Array(3*8192);this.col=new Uint8ClampedArray(4*8192);this.idx=new Uint32Array(6*2048);this.v=0;this.i=0;}
  grow(){if((this.v+4)*3>this.pos.length){const p=new Float32Array(this.pos.length*2);p.set(this.pos);this.pos=p;const c=new Uint8ClampedArray(this.col.length*2);c.set(this.col);this.col=c;}if(this.i+12>this.idx.length){const n=new Uint32Array(this.idx.length*2);n.set(this.idx);this.idx=n;}}
  vertex(x,y,z,r,g,b,a){const v=this.v++;this.pos[v*3]=x;this.pos[v*3+1]=y;this.pos[v*3+2]=z;this.col[v*4]=r;this.col[v*4+1]=g;this.col[v*4+2]=b;this.col[v*4+3]=a;return v;}
  quad(flip,doubleSided){const v=this.v-4,I=this.idx;let i=this.i;
    if(flip){I[i++]=v+1;I[i++]=v+2;I[i++]=v+3;I[i++]=v+1;I[i++]=v+3;I[i++]=v;}else{I[i++]=v;I[i++]=v+1;I[i++]=v+2;I[i++]=v;I[i++]=v+2;I[i++]=v+3;}
    if(doubleSided){I[i++]=v;I[i++]=v+2;I[i++]=v+1;I[i++]=v;I[i++]=v+3;I[i++]=v+2;}
    this.i=i;}
  result(){return {positions:this.pos.slice(0,this.v*3),colors:this.col.slice(0,this.v*4),indices:this.idx.slice(0,this.i),vertices:this.v};}
}

// Faces: +X, -X, +Y, -Y, +Z, -Z. Corners are counter-clockwise from outside.
const FACES=[
  {n:[1,0,0],c:[[1,0,0],[1,1,0],[1,1,1],[1,0,1]],shade:.64},
  {n:[-1,0,0],c:[[0,0,1],[0,1,1],[0,1,0],[0,0,0]],shade:.72},
  {n:[0,1,0],c:[[0,1,0],[0,1,1],[1,1,1],[1,1,0]],shade:1},
  {n:[0,-1,0],c:[[0,0,0],[1,0,0],[1,0,1],[0,0,1]],shade:.5},
  {n:[0,0,1],c:[[1,0,1],[1,1,1],[0,1,1],[0,0,1]],shade:.86},
  {n:[0,0,-1],c:[[0,0,0],[0,1,0],[1,1,0],[1,0,0]],shade:.8},
];
const AO=[.52,.68,.84,1];
const SX=18,SXZ=18*18;
const step=(x,y,z)=>y*SXZ+z*SX+x;
for(const f of FACES){
  const axis=f.n.findIndex(v=>v!==0),[t1,t2]=[0,1,2].filter(a=>a!==axis);
  f.delta=step(f.n[0],f.n[1],f.n[2]);
  f.ao=f.c.map(corner=>{const d1=[0,0,0],d2=[0,0,0];d1[t1]=corner[t1]?1:-1;d2[t2]=corner[t2]?1:-1;const b=f.n;return [step(b[0]+d1[0],b[1]+d1[1],b[2]+d1[2]),step(b[0]+d2[0],b[1]+d2[1],b[2]+d2[2]),step(b[0]+d1[0]+d2[0],b[1]+d1[1]+d2[1],b[2]+d1[2]+d2[2])];});
}
const hash=(x,y,z)=>{let h=Math.imul(x,374761393)^Math.imul(y,668265263)^Math.imul(z,2147483647);h=Math.imul(h^(h>>>13),1274126177);return ((h^(h>>>16))>>>0)/4294967296;};
function boxes(style){
  switch(style.kind){
    case 'slab':return [[0,0,0,1,.5,1]];case 'slabtop':return [[0,.5,0,1,1,1]];case 'slab15':return [[0,0,0,1,15/16,1]];case 'slab9':return [[0,0,0,1,9/16,1]];
    case 'flat':return [[0,0,0,1,1/16,1]];case 'snow':return [[0,0,0,1,(style.layers||1)/8,1]];case 'post':return [[.3125,0,.3125,.6875,1,.6875]];
    case 'stairs':{const lo=style.half==='top'?.5:0,hi=style.half==='top'?0:.5,step={north:[0,0,1,.5],south:[0,.5,1,1],east:[.5,0,1,1],west:[0,0,.5,1]}[style.facing];return [[0,lo,0,1,lo+.5,1],[step[0],hi,step[1],step[2],hi+.5,step[3]]];}
    default:return [[0,0,0,1,1,1]];
  }
}

let scratch=new Uint16Array(0);
// Mesh one chunk. Neighbours that are not loaded count as air (their border faces are drawn).
// Blocks below floorY are cut away. Positions are relative to originX/originZ in blocks.
export function meshChunk(world,cx,cz,{floorY=-64,originX=0,originZ=0}={}){
  const self=world.get(cx,cz),opaque=new Buffer(),clear=new Buffer();
  if(!self||!self.sections.size)return {opaque:opaque.result(),transparent:clear.result(),faces:0,blocks:0};
  const y0=Math.max(floorY,self.minY),y1=self.maxY+1,H=y1-y0;
  if(H<=0)return {opaque:opaque.result(),transparent:clear.result(),faces:0,blocks:0};
  const size=SXZ*(H+2);if(scratch.length<size)scratch=new Uint16Array(size);const g=scratch;g.fill(0,0,size);
  g.fill(y0>floorY?0:FLOOR,0,SXZ);// row below the cut: solid floor when cut, otherwise whatever lies below
  const fill=(chunk,x0,x1,z0,z1,dx,dz)=>{if(!chunk)return;for(let y=y0-1;y<y1;y++){const s=chunk.sections.get(y>>4);if(!s){continue;}const ly=y&15,row=(y-y0+1)*SXZ;
    for(let z=z0;z<z1;z++)for(let x=x0;x<x1;x++){const id=s.uniform??s.ids[(ly*16+z)*16+x];if(y<y0&&!(world.registry.flags[id]&OPAQUE))continue;g[row+(z+dz+1)*SX+x+dx+1]=y<floorY?FLOOR:id;}}};
  fill(self,0,16,0,16,0,0);
  const n=(dx,dz)=>world.get(cx+dx,cz+dz);
  fill(n(-1,0),15,16,0,16,-16,0);fill(n(1,0),0,1,0,16,16,0);fill(n(0,-1),0,16,15,16,0,-16);fill(n(0,1),0,16,0,1,0,16);
  fill(n(-1,-1),15,16,15,16,-16,-16);fill(n(1,-1),0,1,15,16,16,-16);fill(n(-1,1),15,16,0,1,-16,16);fill(n(1,1),0,1,0,1,16,16);
  const flags=world.registry.flags,styles=world.registry.styles,bx=cx*16-originX,bz=cz*16-originZ;let faces=0,blocks=0;
  for(let y=0;y<H;y++)for(let z=0;z<16;z++)for(let x=0;x<16;x++){
    const c=step(x+1,y+1,z+1),id=g[c];if(!id)continue;blocks++;
    const f0=flags[id],style=styles[id],wx=bx+x,wy=y0+y,wz=bz+z,jitter=.965+.07*hash(wx+originX,wy,wz+originZ);
    if(f0&(OPAQUE|TRANSLUCENT)){
      const water=f0&WATER,buf=f0&OPAQUE?opaque:clear,alpha=Math.round(style.alpha*255);
      const height=water&&!(flags[g[c+SXZ]]&WATER)?.875:1;
      for(let fi=0;fi<6;fi++){
        const F=FACES[fi],nb=g[c+F.delta],nf=flags[nb];
        if(nf&OPAQUE)continue;
        if(f0&TRANSLUCENT&&(nb===id||(water&&nf&WATER)))continue;
        if(water&&fi===3&&nf&TRANSLUCENT)continue;
        buf.grow();faces++;
        const rgb=fi===2?style.top:fi===3?style.bottom:style.rgb,shade=F.shade*jitter,ao=[0,0,0,0];
        for(let k=0;k<4;k++){
          const [a,b,d]=F.ao[k],s1=flags[g[c+a]]&OPAQUE?1:0,s2=flags[g[c+b]]&OPAQUE?1:0,s3=flags[g[c+d]]&OPAQUE?1:0;
          ao[k]=s1&&s2?0:3-s1-s2-s3;const light=shade*AO[ao[k]],p=F.c[k];
          buf.vertex(wx+p[0],wy+(p[1]?height:0),wz+p[2],rgb[0]*light,rgb[1]*light,rgb[2]*light,alpha);
        }
        buf.quad(ao[0]+ao[2]<ao[1]+ao[3]);
      }
    }else if(f0&CROSS){
      const r=style.rgb,l=jitter*.9,h=.8;
      for(const [ax,az,bx2,bz2] of [[.15,.15,.85,.85],[.15,.85,.85,.15]]){
        opaque.grow();opaque.vertex(wx+ax,wy,wz+az,r[0]*l*.8,r[1]*l*.8,r[2]*l*.8,255);opaque.vertex(wx+ax,wy+h,wz+az,r[0]*l,r[1]*l,r[2]*l,255);opaque.vertex(wx+bx2,wy+h,wz+bz2,r[0]*l,r[1]*l,r[2]*l,255);opaque.vertex(wx+bx2,wy,wz+bz2,r[0]*l*.8,r[1]*l*.8,r[2]*l*.8,255);opaque.quad(false,true);faces+=2;
      }
    }else{
      for(const [x0,y0b,z0,x1,y1b,z1] of boxes(style)){
        const lo=[x0,y0b,z0],hi=[x1,y1b,z1];
        for(let fi=0;fi<6;fi++){
          const F=FACES[fi],axis=fi>>1,positive=!(fi&1),edge=positive?hi[axis]===1:lo[axis]===0;
          if(edge&&flags[g[c+F.delta]]&OPAQUE)continue;
          opaque.grow();faces++;const rgb=fi===2?style.top:fi===3?style.bottom:style.rgb,light=F.shade*jitter;
          for(const p of F.c)opaque.vertex(wx+(p[0]?x1:x0),wy+(p[1]?y1b:y0b),wz+(p[2]?z1:z0),rgb[0]*light,rgb[1]*light,rgb[2]*light,255);
          opaque.quad(false);
        }
      }
    }
  }
  return {opaque:opaque.result(),transparent:clear.result(),faces,blocks};
}

// Voxel ray march (Amanatides & Woo). Returns the first visible block at or above floorY.
export function raycast(world,origin,dir,{maxDistance=600,floorY=-64,offsetX=0,offsetZ=0}={}){
  let x=Math.floor(origin[0]),y=Math.floor(origin[1]),z=Math.floor(origin[2]);
  const sx=Math.sign(dir[0]),sy=Math.sign(dir[1]),sz=Math.sign(dir[2]);
  const tdx=sx?Math.abs(1/dir[0]):Infinity,tdy=sy?Math.abs(1/dir[1]):Infinity,tdz=sz?Math.abs(1/dir[2]):Infinity;
  let tx=sx?((sx>0?x+1-origin[0]:origin[0]-x))*tdx:Infinity,ty=sy?((sy>0?y+1-origin[1]:origin[1]-y))*tdy:Infinity,tz=sz?((sz>0?z+1-origin[2]:origin[2]-z))*tdz:Infinity;
  let face=-1,t=0;
  for(let i=0;i<4096&&t<=maxDistance;i++){
    if(y>=floorY){const id=world.block(x+offsetX,y,z+offsetZ);if(id)return {x:x+offsetX,y,z:z+offsetZ,id,face,distance:t,name:world.registry.names[id]};}
    else if(sy<=0)return null;
    if(tx<ty&&tx<tz){x+=sx;t=tx;tx+=tdx;face=sx>0?1:0;}else if(ty<tz){y+=sy;t=ty;ty+=tdy;face=sy>0?3:2;}else{z+=sz;t=tz;tz+=tdz;face=sz>0?5:4;}
  }
  return null;
}
