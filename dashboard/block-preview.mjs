// Proposed public crop adapter. No private dataset URL or live feed is configured.
export const MAX_BYTES=512*1024, MAX_BLOCKS=32768;
export function decodeCrop(value){
  if(value?.schema!=='earthcraft.public-block-crop.v1')throw Error('Unsupported block preview format.');
  const {origin,size,palette,blocks,generation,attribution}=value;
  if(!Array.isArray(origin)||origin.length!==3||!origin.every(Number.isSafeInteger))throw Error('Invalid world origin.');
  if(!Array.isArray(size)||size.length!==3||![16,32].includes(size[0])||![16,32].includes(size[2])||!Number.isInteger(size[1])||size[1]<1||size[1]>384)throw Error('Invalid crop dimensions.');
  if(!Array.isArray(palette)||palette.length<1||palette.length>256||!palette.every(x=>typeof x==='string'&&/^minecraft:[a-z0-9_]+(?:\[[a-z0-9_=,]+\])?$/.test(x)&&x.length<=256))throw Error('Invalid block palette.');
  if(!Array.isArray(blocks)||blocks.length>MAX_BLOCKS)throw Error('Block preview exceeds the block limit.');
  const seen=new Set();
  for(const b of blocks){
    if(!Array.isArray(b)||b.length!==4||!b.every(Number.isInteger)||b.slice(0,3).some((n,i)=>n<0||n>=size[i])||b[3]<0||b[3]>=palette.length)throw Error('Invalid block coordinates.');
    const key=b.slice(0,3).join(',');if(seen.has(key))throw Error('Duplicate block position.');seen.add(key);
  }
  if(!generation||typeof generation.repo!=='string'||!/^[-\w]+\/[-\w.]+$/.test(generation.repo)||!/^-?\d+_-?\d+$/.test(generation.tile)||!['revision','shard_sha256','region_sha256','receipt_sha256'].every(k=>typeof generation[k]==='string'&&new RegExp(k==='revision'?'^[a-f0-9]{40}$':'^[a-f0-9]{64}$').test(generation[k])))throw Error('Missing immutable generation provenance.');
  const tile=generation.tile.split('_').map(Number);
  if([0,2].some((axis,i)=>Math.floor(origin[axis]/512)!==tile[i]||Math.floor((origin[axis]+size[axis]-1)/512)!==tile[i])||origin[1]<-64||origin[1]+size[1]>320)throw Error('Crop crosses its tile or Minecraft height bounds.');
  if(!Array.isArray(attribution)||!attribution.length||!attribution.every(a=>typeof a.source==='string'&&a.source.length<=256&&typeof a.license==='string'&&a.license.length<=256))throw Error('Missing source attribution.');
  if(!['complete-crop','sampled-blocks'].includes(value.coverage))throw Error('Missing crop coverage label.');
  return value;
}
export async function readBounded(response,signal){
  if(!response.ok)throw Error('Preview download failed ('+response.status+').');
  if(Number(response.headers.get('content-length'))>MAX_BYTES)throw Error('Preview download exceeds 512 KiB.');
  if(!response.body)throw Error('Preview download has no body.');
  const reader=response.body.getReader(),chunks=[];let count=0;
  try{
    while(true){signal?.throwIfAborted();const {value,done}=await reader.read();if(done)break;count+=value.byteLength;if(count>MAX_BYTES)throw Error('Preview download exceeds 512 KiB.');chunks.push(value);}
  }catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
  const bytes=new Uint8Array(count);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}return bytes;
}
export function drawCrop(canvas,crop,rotation=0){
  const ctx=canvas.getContext('2d');canvas.width=640;canvas.height=400;ctx.clearRect(0,0,640,400);
  if(!crop)return;
  const colors={stone:'#8b9890',grass_block:'#4e7951',dirt:'#896747',water:'#6495b7',sand:'#cab980'};
  const turn=([x,y,z,p])=>{for(let i=0;i<rotation%4;i++)[x,z]=[crop.size[2]-1-z,x];return [x,y,z,p];};
  const blocks=crop.blocks.map(turn).sort((a,b)=>(a[0]+a[2])-(b[0]+b[2])||a[1]-b[1]);
  const minY=Math.min(...blocks.map(b=>b[1]),0),maxY=Math.max(...blocks.map(b=>b[1]),1);
  const scale=Math.min(16,544/(crop.size[0]+crop.size[2]),260/(maxY-minY+crop.size[0]/2));
  for(const [x,y,z,p] of blocks){
    const sx=320+(x-z)*scale,sy=300-(y-minY)*scale+(x+z-crop.size[0])*scale/2;
    ctx.fillStyle=colors[crop.palette[p].replace('minecraft:','').split('[')[0]]||'#a7a39a';
    ctx.beginPath();ctx.moveTo(sx,sy-scale);ctx.lineTo(sx+scale,sy-scale/2);ctx.lineTo(sx,sy);ctx.lineTo(sx-scale,sy-scale/2);ctx.closePath();ctx.fill();
    ctx.fillStyle='#566159';ctx.beginPath();ctx.moveTo(sx-scale,sy-scale/2);ctx.lineTo(sx,sy);ctx.lineTo(sx,sy+scale);ctx.lineTo(sx-scale,sy+scale/2);ctx.closePath();ctx.fill();
    ctx.fillStyle='#89948c';ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(sx+scale,sy-scale/2);ctx.lineTo(sx+scale,sy+scale/2);ctx.lineTo(sx,sy+scale);ctx.closePath();ctx.fill();
  }
}
export class PreviewController{
  constructor(onState){this.onState=onState;this.sequence=0;this.active=null;}
  cancel(){this.sequence++;this.active?.abort();this.active=null;}
  async select(descriptor){
    this.cancel();const sequence=this.sequence;
    if(!descriptor){this.onState({status:'unavailable'});return;}
    const {url,sha256}=descriptor;
    if(!/^https:\/\//.test(url)||!/^[a-f0-9]{64}$/.test(sha256)){this.onState({status:'error',message:'Invalid preview reference.'});return;}
    const controller=new AbortController();this.active=controller;const timer=setTimeout(()=>controller.abort(),10000);
    this.onState({status:'loading'});
    try{
      const bytes=await readBounded(await fetch(url,{signal:controller.signal,credentials:'omit',referrerPolicy:'no-referrer'}),controller.signal);
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
      if(digest!==sha256)throw Error('Preview checksum did not match.');
      const crop=decodeCrop(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)));
      if(sequence===this.sequence)this.onState({status:'ready',crop});
    }catch(error){if(sequence===this.sequence)this.onState({status:controller.signal.aborted?'cancelled':'error',message:controller.signal.aborted?'Preview request stopped.':error.message});}
    finally{clearTimeout(timer);if(sequence===this.sequence)this.active=null;}
  }
}
