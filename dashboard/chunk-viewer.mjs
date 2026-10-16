// 3D chunk viewer: reads Anvil region files over HTTP Range (anvil.mjs), meshes
// them (chunk-mesh.mjs) and draws with the vendored three.js build.
// blocks.html?base=<…/world/region/>&chunk=cx,cz&radius=r&view=orbit|iso|top|street&yaw=deg
// qa=1 adds a fixed-size, chrome-free deterministic capture that sets window.__earthcraftRender.
import * as THREE from './assets/vendor/three/three.module.min.js';
import {RegionClient,HF_REPO,hfRegionBase,chunkRegion} from './anvil.mjs?v=blocks-1';
import {BlockWorld,BlockRegistry,prepareChunk,meshChunk,raycast} from './chunk-mesh.mjs?v=blocks-1';

export const WATER_TOWER_BLOCK=[32,33];// Inherited frame: easting 0, northing 0 → X 32, Z 33.
export const MAX_RADIUS=8,VIEWS=['orbit','iso','top','street'];
const ISO_PITCH=35.264,D2R=Math.PI/180,SKY=0xc9d9e3;
const DEFAULT_PITCH={orbit:40,iso:ISO_PITCH,top:90,street:-10};
const DEFAULT_YAW={orbit:45,iso:45,top:0,street:0};

export function parseViewerParams(search,href=globalThis.location?.href||'http://localhost/'){
  const q=new URLSearchParams(search),pair=name=>{const v=q.get(name);if(v===null||v==='')return null;const parts=v.split(',').map(s=>s.trim());if(parts.length!==2||!parts.every(s=>/^-?\d{1,8}$/.test(s)))throw Error(`${name} must be two integers, such as ${name}=2,2.`);return parts.map(Number);};
  const number=(name,lo,hi)=>{const v=q.get(name);if(v===null||v==='')return null;const n=Number(v);if(!Number.isFinite(n))throw Error(name+' must be a number.');return Math.max(lo,Math.min(hi,n));};
  const revision=q.get('revision')||'main',custom=q.get('base');
  const base=custom?new URL(custom,href).href:hfRegionBase(revision);
  const region=pair('region'),chunk=pair('chunk'),block=pair('block');
  let center;
  if(chunk){
    center=chunk;
    // chunk is absolute; a 0–31 pair outside the named region is read as an offset inside it.
    if(region&&(chunk[0]>>5!==region[0]||chunk[1]>>5!==region[1])&&chunk.every(v=>v>=0&&v<32))center=[region[0]*32+chunk[0],region[1]*32+chunk[1]];
  }else if(block)center=[block[0]>>4,block[1]>>4];
  else if(region)center=[region[0]*32+16,region[1]*32+16];
  else center=[WATER_TOWER_BLOCK[0]>>4,WATER_TOWER_BLOCK[1]>>4];
  const qa=q.get('qa')==='1',viewName=q.get('view'),view=VIEWS.includes(viewName)?viewName:qa?'iso':'orbit';
  const radius=Math.round(number('radius',0,MAX_RADIUS)??(qa?2:3)),floor=number('floor',-64,960);
  return {base,custom:!!custom,revision,center,block,radius,view,yaw:number('yaw',-3600,3600)??DEFAULT_YAW[view],pitch:number('pitch',-89,90),
    width:Math.round(number('w',64,4096)??960),height:Math.round(number('h',64,4096)??640),qa,floor:floor===null?null:Math.round(floor)};
}
export function spiral([cx,cz],radius){
  const out=[];for(let dz=-radius;dz<=radius;dz++)for(let dx=-radius;dx<=radius;dx++)out.push([cx+dx,cz+dz]);
  return out.sort((a,b)=>Math.hypot(a[0]-cx,a[1]-cz)-Math.hypot(b[0]-cx,b[1]-cz)||a[1]-b[1]||a[0]-b[0]);
}
// Cut-away floor: a few blocks below the low (2nd percentile) surface of loaded columns.
export function autoFloor(world){
  const tops=[];for(const c of world.chunks.values())for(const t of c.tops)if(t!==-32768)tops.push(t);
  if(!tops.length)return -64;tops.sort((a,b)=>a-b);return Math.max(-64,tops[Math.floor(tops.length*.02)]-4);
}

export class ChunkViewer{
  constructor(canvas,{qa=false}={}){
    this.canvas=canvas;this.qa=qa;
    THREE.ColorManagement.enabled=false;// Vertex colors are display sRGB already.
    this.renderer=new THREE.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:qa,powerPreference:'high-performance'});
    this.renderer.outputColorSpace=THREE.LinearSRGBColorSpace;
    this.renderer.setPixelRatio(qa?1:Math.min(2,globalThis.devicePixelRatio||1));
    this.scene=new THREE.Scene();this.scene.background=new THREE.Color(SKY);
    this.perspective=new THREE.PerspectiveCamera(55,1,.1,4000);this.ortho=new THREE.OrthographicCamera(-1,1,1,-1,1,8000);
    this.opaque=new THREE.MeshBasicMaterial({vertexColors:true});
    this.clear=new THREE.MeshBasicMaterial({vertexColors:true,transparent:true,depthWrite:false});
    this.root=new THREE.Group();this.scene.add(this.root);this.meshes=new Map();
    this.highlight=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1.03,1.03,1.03)),new THREE.LineBasicMaterial({color:0x101410}));
    this.highlight.visible=false;this.scene.add(this.highlight);
    this.view='orbit';this.yaw=45;this.pitch=40;this.distance=160;this.zoom=1;this.orthoHeight=200;
    this.target=new THREE.Vector3(8,64,8);this.eye=new THREE.Vector3(8,66,8);
    this.bounds=new THREE.Box3(new THREE.Vector3(0,60,0),new THREE.Vector3(16,80,16));
    this.width=canvas.clientWidth||960;this.height=canvas.clientHeight||640;this.pending=false;this.onRender=null;
  }
  setSize(w,h){this.width=Math.max(1,w);this.height=Math.max(1,h);this.renderer.setSize(this.width,this.height,false);this.requestRender();}
  camera(){return this.view==='iso'||this.view==='top'?this.ortho:this.perspective;}
  forward(yaw=this.yaw,pitch=this.pitch){const h=yaw*D2R,p=pitch*D2R;return new THREE.Vector3(Math.sin(h)*Math.cos(p),-Math.sin(p),-Math.cos(h)*Math.cos(p));}
  axes(){
    const dir=this.forward(),up=this.view==='top'?new THREE.Vector3(Math.sin(this.yaw*D2R),0,-Math.cos(this.yaw*D2R)):new THREE.Vector3(0,1,0);
    const right=new THREE.Vector3().crossVectors(dir,up).normalize(),screenUp=new THREE.Vector3().crossVectors(right,dir).normalize();return {dir,up,right,screenUp};
  }
  spanRadius(){return this.bounds.getSize(new THREE.Vector3()).length()/2;}
  setView(view,{yaw=this.yaw,pitch=DEFAULT_PITCH[view]}={}){this.view=view;this.yaw=yaw;this.pitch=pitch;}
  // Fit the camera to the loaded content for the current view and heading. points: optional
  // [x,y,z,…] samples of visible content in scene coordinates (falls back to the bounds corners).
  frame(street=null,points=null){
    const b=this.bounds,center=b.getCenter(new THREE.Vector3()),size=b.getSize(new THREE.Vector3()),aspect=this.width/this.height;
    if(this.view==='street'){if(street)this.eye.copy(street);return;}
    if(this.view==='orbit'){
      // Bounding-sphere fit; very tall towers count up to 1.2× the horizontal half-extent.
      const horizontal=Math.hypot(size.x,size.z)/2,height=Math.min(size.y,horizontal*1.2),vfov=this.perspective.fov*D2R,fov=Math.min(vfov,2*Math.atan(Math.tan(vfov/2)*aspect));
      this.target.set(center.x,b.min.y+height*.35,center.z);
      this.distance=Math.max(24,Math.hypot(horizontal,height/2)/Math.sin(fov/2)*.92);return;
    }
    this.zoom=1;const {right,screenUp}=this.axes();
    if(!points?.length){points=[];for(let i=0;i<8;i++)points.push(i&1?b.max.x:b.min.x,i&2?b.max.y:b.min.y,i&4?b.max.z:b.min.z);}
    let x0=Infinity,x1=-Infinity,y0=Infinity,y1=-Infinity;const p=new THREE.Vector3();
    for(let i=0;i<points.length;i+=3){p.set(points[i],points[i+1],points[i+2]).sub(center);const u=p.dot(right),v=p.dot(screenUp);x0=Math.min(x0,u);x1=Math.max(x1,u);y0=Math.min(y0,v);y1=Math.max(y1,v);}
    this.target.copy(center).addScaledVector(right,(x0+x1)/2).addScaledVector(screenUp,(y0+y1)/2);
    this.orthoHeight=Math.max(y1-y0,(x1-x0)/aspect)*1.06;
  }
  updateCamera(){
    const aspect=this.width/this.height,span=this.spanRadius();
    if(this.view==='orbit'||this.view==='street'){
      const cam=this.perspective;cam.aspect=aspect;cam.up.set(0,1,0);
      if(this.view==='orbit'){
        cam.fov=55;const dir=this.forward();cam.position.copy(this.target).addScaledVector(dir,-this.distance);cam.lookAt(this.target);
        cam.near=Math.max(.2,this.distance/400);cam.far=this.distance+span*3+200;
        this.scene.fog=new THREE.Fog(SKY,this.distance+span*.6,this.distance+span*2.6);
      }else{
        cam.fov=72;cam.position.copy(this.eye);cam.lookAt(this.eye.clone().add(this.forward()));cam.near=.1;cam.far=span*3+200;
        this.scene.fog=new THREE.Fog(SKY,Math.max(96,span*.8),Math.max(320,span*2.6));
      }
      cam.updateProjectionMatrix();
    }else{
      const cam=this.ortho,{dir,up}=this.axes(),half=this.orthoHeight/this.zoom/2;
      cam.up.copy(up);cam.position.copy(this.target).addScaledVector(dir,-(span+400));cam.lookAt(this.target);
      cam.left=-half*aspect;cam.right=half*aspect;cam.top=half;cam.bottom=-half;cam.near=1;cam.far=2*span+800;cam.updateProjectionMatrix();
      this.scene.fog=null;
    }
  }
  requestRender(){if(this.pending)return;this.pending=true;requestAnimationFrame(()=>{this.pending=false;this.renderNow();});}
  renderNow(){this.updateCamera();this.renderer.render(this.scene,this.camera());this.onRender?.();}
  setChunk(key,data){
    this.removeChunk(key);const list=[];
    for(const [buf,material,order] of [[data.opaque,this.opaque,0],[data.transparent,this.clear,1]]){
      if(!buf.vertices)continue;
      const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(buf.positions,3));g.setAttribute('color',new THREE.BufferAttribute(buf.colors,4,true));g.setIndex(new THREE.BufferAttribute(buf.indices,1));g.computeBoundingSphere();
      const mesh=new THREE.Mesh(g,material);mesh.matrixAutoUpdate=false;mesh.renderOrder=order;this.root.add(mesh);list.push(mesh);
    }
    this.meshes.set(key,list);
  }
  removeChunk(key){for(const m of this.meshes.get(key)||[]){this.root.remove(m);m.geometry.dispose();}this.meshes.delete(key);}
  clearChunks(){for(const key of [...this.meshes.keys()])this.removeChunk(key);}
  ray(clientX,clientY){
    const rect=this.canvas.getBoundingClientRect(),ndc=new THREE.Vector2((clientX-rect.left)/rect.width*2-1,-(clientY-rect.top)/rect.height*2+1);
    this.updateCamera();const caster=new THREE.Raycaster();caster.setFromCamera(ndc,this.camera());return caster.ray;
  }
  pick(world,clientX,clientY,{floorY,originX,originZ}){
    const ray=this.ray(clientX,clientY),box=this.bounds.clone().expandByScalar(1);let start=ray.origin.clone();
    if(!box.containsPoint(start)){const hit=ray.intersectBox(box,new THREE.Vector3());if(!hit)return null;start=hit.addScaledVector(ray.direction,-.01);}
    return raycast(world,[start.x,start.y,start.z],[ray.direction.x,ray.direction.y,ray.direction.z],{maxDistance:box.getSize(new THREE.Vector3()).length()+4,floorY,offsetX:originX,offsetZ:originZ});
  }
  showHighlight(hit,originX,originZ){this.highlight.visible=!!hit;if(hit)this.highlight.position.set(hit.x-originX+.5,hit.y+.5,hit.z-originZ+.5);this.requestRender();}
  // Pointer and keyboard camera controls.
  drag(dx,dy,mode){
    if(this.view==='orbit'){
      if(mode==='rotate'){this.yaw-=dx*.3;this.pitch=Math.max(5,Math.min(89,this.pitch+dy*.3));}
      else{const {right}=this.axes(),ground=new THREE.Vector3(Math.sin(this.yaw*D2R),0,-Math.cos(this.yaw*D2R)),k=this.distance*.0016;this.target.addScaledVector(right,-dx*k).addScaledVector(ground,dy*k);}
    }else if(this.view==='street'){this.yaw-=dx*.25;this.pitch=Math.max(-85,Math.min(85,this.pitch-dy*.25));}
    else if(mode==='rotate'){this.yaw-=dx*.3;}
    else{const {right}=this.axes(),ground=new THREE.Vector3(Math.sin(this.yaw*D2R),0,-Math.cos(this.yaw*D2R)),k=this.orthoHeight/this.zoom/this.height;this.target.addScaledVector(right,-dx*k).addScaledVector(ground,dy*k/Math.max(.2,Math.sin(this.pitch*D2R)));}
    this.requestRender();
  }
  zoomBy(factor){
    if(this.view==='orbit')this.distance=Math.max(6,Math.min(4000,this.distance*factor));
    else if(this.view==='street')this.walk((1-factor)*40,0,0);
    else this.zoom=Math.max(.2,Math.min(40,this.zoom/factor));
    this.requestRender();
  }
  walk(forward,strafe,up){
    const h=this.yaw*D2R,f=new THREE.Vector3(Math.sin(h),0,-Math.cos(h)),r=new THREE.Vector3(Math.cos(h),0,Math.sin(h));
    if(this.view==='street')this.eye.addScaledVector(f,forward).addScaledVector(r,strafe).add(new THREE.Vector3(0,up,0));
    else{const k=this.view==='orbit'?this.distance/40:this.orthoHeight/this.zoom/24;this.target.addScaledVector(f,forward*k).addScaledVector(r,strafe*k);}
    this.requestRender();
  }
}

function installControls(viewer,canvas,{onHover,onTap,onKey}){
  const pointers=new Map();let mode='rotate',moved=0,last=null,pinch=null,hoverPending=null;
  canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('pointerdown',e=>{
    canvas.setPointerCapture?.(e.pointerId);pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});moved=0;
    mode=e.button===2||e.shiftKey?'pan':viewer.view==='iso'||viewer.view==='top'?'pan':'rotate';
    if(e.button===2&&(viewer.view==='iso'||viewer.view==='top'))mode='rotate';
    if(pointers.size===2){const [a,b]=[...pointers.values()];pinch={d:Math.hypot(a.x-b.x,a.y-b.y),x:(a.x+b.x)/2,y:(a.y+b.y)/2};}
  });
  canvas.addEventListener('pointermove',e=>{
    const p=pointers.get(e.pointerId);
    if(!p){if(e.pointerType==='mouse'){last=e;if(!hoverPending)hoverPending=requestAnimationFrame(()=>{hoverPending=null;onHover(last.clientX,last.clientY);});}return;}
    const dx=e.clientX-p.x,dy=e.clientY-p.y;p.x=e.clientX;p.y=e.clientY;moved+=Math.abs(dx)+Math.abs(dy);
    if(pointers.size===1)viewer.drag(dx,dy,mode);
    else if(pointers.size===2&&pinch){const [a,b]=[...pointers.values()],d=Math.hypot(a.x-b.x,a.y-b.y),x=(a.x+b.x)/2,y=(a.y+b.y)/2;if(d>0)viewer.zoomBy(pinch.d/d);if(viewer.view!=='street')viewer.drag(x-pinch.x,y-pinch.y,'pan');pinch={d,x,y};}
  });
  const end=e=>{if(!pointers.has(e.pointerId))return;pointers.delete(e.pointerId);if(pointers.size<2)pinch=null;if(moved<5&&pointers.size===0)onTap(e.clientX,e.clientY);};
  canvas.addEventListener('pointerup',end);canvas.addEventListener('pointercancel',e=>{pointers.delete(e.pointerId);pinch=null;});
  canvas.addEventListener('pointerleave',e=>{if(e.pointerType==='mouse'&&!pointers.size)onHover(null);});
  canvas.addEventListener('wheel',e=>{e.preventDefault();viewer.zoomBy(Math.pow(1.0015,Math.max(-400,Math.min(400,e.deltaY*(e.deltaMode===1?33:1)))));},{passive:false});
  canvas.addEventListener('keydown',e=>{
    if(e.metaKey||e.ctrlKey||e.altKey)return;
    const k=e.key.length===1?e.key.toLowerCase():e.key,fast=e.shiftKey?4:1,v=viewer.view;let handled=true;
    if(onKey(k))handled=true;
    else if(v==='street'){
      if(k==='w'||k==='ArrowUp')viewer.walk(2*fast,0,0);else if(k==='s'||k==='ArrowDown')viewer.walk(-2*fast,0,0);else if(k==='a')viewer.walk(0,-2*fast,0);else if(k==='d')viewer.walk(0,2*fast,0);
      else if(k==='ArrowLeft')viewer.drag(40,0);else if(k==='ArrowRight')viewer.drag(-40,0);else if(k==='e')viewer.walk(0,0,fast);else if(k==='q')viewer.walk(0,0,-fast);
      else if(k==='PageUp')viewer.drag(0,20);else if(k==='PageDown')viewer.drag(0,-20);else handled=false;
    }else{
      if(k==='w')viewer.walk(fast,0,0);else if(k==='s')viewer.walk(-fast,0,0);else if(k==='a')viewer.walk(0,-fast,0);else if(k==='d')viewer.walk(0,fast,0);
      else if(k==='ArrowLeft')v==='orbit'?viewer.drag(-30,0,'rotate'):viewer.walk(0,-fast,0);else if(k==='ArrowRight')v==='orbit'?viewer.drag(30,0,'rotate'):viewer.walk(0,fast,0);
      else if(k==='ArrowUp')v==='orbit'?viewer.drag(0,15,'rotate'):viewer.walk(fast,0,0);else if(k==='ArrowDown')v==='orbit'?viewer.drag(0,-15,'rotate'):viewer.walk(-fast,0,0);
      else if(k==='q')viewer.drag(-50,0,'rotate');else if(k==='e')viewer.drag(50,0,'rotate');
      else if(k==='+'||k==='=')viewer.zoomBy(1/1.25);else if(k==='-'||k==='_')viewer.zoomBy(1.25);else handled=false;
    }
    if(handled)e.preventDefault();
  });
}

const fmt=n=>Number(n).toLocaleString('en-US');
const bytes=n=>n>=1048576?(n/1048576).toFixed(2)+' MiB':(n/1024).toFixed(1)+' KiB';
const nextFrame=()=>new Promise(resolve=>requestAnimationFrame(()=>resolve()));
const COVER={loaded:'#285b3e',absent:'#ecebe3',pending:'#98691e',error:'#a24432'};

export function installBlocksPage(doc=document){
  const $=id=>doc.getElementById(id);
  let params;
  const report=value=>{window.__earthcraftRender=value;doc.body.dataset.render=value.status;};
  try{params=parseViewerParams(location.search);}catch(error){$('viewer-status').textContent=error.message;report({status:'error',error:error.message,chunks_loaded:0,blocks:0,missing:0});return;}
  const canvas=$('viewer'),wrap=$('viewer-wrap');let viewer;
  if(params.qa){canvas.style.width=params.width+'px';canvas.style.height=params.height+'px';}
  try{viewer=new ChunkViewer(canvas,{qa:params.qa});}
  catch(error){const message='WebGL is unavailable, so the 3D block view cannot be drawn.';$('viewer-status').textContent=message;wrap.setAttribute('aria-busy','false');report({status:'error',error:message,chunks_loaded:0,blocks:0,missing:0});return;}
  if(params.qa)viewer.setSize(params.width,params.height);
  else{
    new ResizeObserver(()=>viewer.setSize(wrap.clientWidth,wrap.clientHeight)).observe(wrap);viewer.setSize(wrap.clientWidth,wrap.clientHeight);
    new ResizeObserver(entries=>doc.documentElement.style.setProperty('--tools-height',entries[0].target.getBoundingClientRect().height+'px')).observe(doc.querySelector('.viewer-tools'));
  }
  canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();$('viewer-status').textContent='The graphics context was lost. Reload the page to draw the blocks again.';});
  const client=new RegionClient({base:params.base}),registry=new BlockRegistry();
  let world=new BlockWorld(registry),state={center:params.center,radius:params.radius,floor:params.floor,floorY:-64,originX:0,originZ:0,statuses:new Map(),errors:[]},session=null,hover=null,pinned=null;
  viewer.setView(params.view,{yaw:params.yaw,pitch:params.pitch??DEFAULT_PITCH[params.view]});
  const setStatus=text=>{$('viewer-status').textContent=text;};
  const regionName=([rx,rz])=>`r.${rx}.${rz}.mca`;
  if(params.custom)$('source-note').textContent='Blocks are read from a custom source: '+params.base;
  else{
    const link=doc.createElement('a');link.href='https://huggingface.co/datasets/'+HF_REPO;link.textContent='Earthcraft dataset on Hugging Face';
    $('source-note').replaceChildren('Blocks are read straight from the ',link,params.revision==='main'?', one chunk at a time.':` (revision ${params.revision}), one chunk at a time.`);
  }

  function updateCamera(){
    const v=viewer,ox=state.originX,oz=state.originZ,heading=((Math.round(v.yaw)%360)+360)%360;
    $('readout-camera').textContent=v.view==='street'?`Eye X ${Math.floor(v.eye.x+ox)} · Y ${Math.floor(v.eye.y)} · Z ${Math.floor(v.eye.z+oz)} · heading ${heading}°`:`Target X ${Math.floor(v.target.x+ox)} · Z ${Math.floor(v.target.z+oz)} · heading ${heading}°`;
  }
  viewer.onRender=updateCamera;
  function describe(hit){return hit?`${hit.name} · X ${hit.x}, Y ${hit.y}, Z ${hit.z}`:'No block under the pointer';}
  function onHover(x,y){if(x===null){hover=null;if(!pinned){viewer.showHighlight(null);$('readout-block').textContent='Point at a block to name it';}return;}hover=viewer.pick(world,x,y,state);if(!pinned){viewer.showHighlight(hover,state.originX,state.originZ);$('readout-block').textContent=describe(hover);}}
  function onTap(x,y){pinned=viewer.pick(world,x,y,state);viewer.showHighlight(pinned,state.originX,state.originZ);$('readout-block').textContent=pinned?'Selected '+describe(pinned):'No block under the pointer';canvas.focus({preventScroll:true});}

  function drawCoverage(){
    const c=$('coverage'),r=state.radius,n=2*r+1,cell=Math.max(4,Math.floor(204/n)),ctx=c.getContext('2d');c.width=c.height=n*cell;
    ctx.fillStyle='#f4f5f3';ctx.fillRect(0,0,c.width,c.height);const counts={loaded:0,absent:0,'no-region':0,error:0,pending:0};
    for(let dz=-r;dz<=r;dz++)for(let dx=-r;dx<=r;dx++){
      const s=state.statuses.get((state.center[0]+dx)+','+(state.center[1]+dz))||'pending',x=(dx+r)*cell,y=(dz+r)*cell;counts[s]=(counts[s]||0)+1;
      if(s==='no-region'){ctx.fillStyle='#ffffff';ctx.fillRect(x,y,cell,cell);ctx.strokeStyle='#8b9890';ctx.lineWidth=1;ctx.beginPath();for(let i=-cell;i<cell;i+=4){ctx.moveTo(x+Math.max(0,i),y+Math.max(0,-i));ctx.lineTo(x+Math.min(cell,i+cell),y+Math.min(cell,cell-i));}ctx.stroke();}
      else{ctx.fillStyle=COVER[s];ctx.fillRect(x,y,cell,cell);}
      ctx.strokeStyle='#ffffff';ctx.lineWidth=1;ctx.strokeRect(x+.5,y+.5,cell-1,cell-1);
    }
    ctx.strokeStyle='#26332c';ctx.lineWidth=2;ctx.strokeRect(r*cell+1,r*cell+1,cell-2,cell-2);
    const summary=`${fmt(n*n)} chunks requested: ${fmt(counts.loaded)} loaded, ${fmt(counts.absent)} not stored, ${fmt(counts['no-region'])} without a region file, ${fmt(counts.error)} unreadable${counts.pending?', '+fmt(counts.pending)+' reading':''}.`;
    $('coverage-summary').textContent=summary;c.setAttribute('aria-label','Chunk coverage grid, north up. '+summary);return counts;
  }
  $('coverage').addEventListener('click',e=>{const c=$('coverage'),rect=c.getBoundingClientRect(),n=2*state.radius+1,dx=Math.floor((e.clientX-rect.left)/rect.width*n)-state.radius,dz=Math.floor((e.clientY-rect.top)/rect.height*n)-state.radius;if(dx||dz)load([state.center[0]+dx,state.center[1]+dz],state.radius);});

  function streetEye(){
    // Stand at street level in the most open spot near the centre of the chosen chunk.
    const cx=state.center[0]*16+8,cz=state.center[1]*16+8,ground=state.floorY+5,low=(x,z)=>{const t=world.top(x,z);return t!==null&&t<=ground;};let best=null;
    for(let dz=-24;dz<=24;dz+=2)for(let dx=-24;dx<=24;dx+=2){
      if(!low(cx+dx,cz+dz))continue;let open=0;for(let j=-4;j<=4;j+=2)for(let i=-4;i<=4;i+=2)open+=low(cx+dx+i,cz+dz+j);
      const score=open-Math.hypot(dx,dz)*.25;if(!best||score>best.score)best={x:cx+dx,z:cz+dz,score};
    }
    const x=best?best.x:cx,z=best?best.z:cz,t=world.top(x,z)??ground;
    return new THREE.Vector3(x+.5-state.originX,t+1+1.62,z+.5-state.originZ);
  }
  function contentPoints(){
    // Column tops on a 4-block stride plus the floor corners, for tight orthographic framing.
    const out=[],r=state.radius,x0=(state.center[0]-r)*16,z0=(state.center[1]-r)*16,n=(2*r+1)*16;
    for(let z=0;z<=n;z+=4)for(let x=0;x<=n;x+=4){const t=world.top(x0+Math.min(x,n-1),z0+Math.min(z,n-1));if(t!==null)out.push(x0+x-state.originX,t+1,z0+z-state.originZ);}
    for(const c of world.chunks.values())if(c.sections.size)for(const [x,z] of [[0,0],[16,0],[0,16],[16,16]])out.push(c.cx*16+x-state.originX,state.floorY,c.cz*16+z-state.originZ);
    return out;
  }
  function setView(view,{reframe=true}={}){
    viewer.setView(view);doc.body.dataset.view=view;
    for(const b of doc.querySelectorAll('.view-switch button'))b.setAttribute('aria-pressed',String(b.dataset.view===view));
    if(reframe)viewer.frame(view==='street'?streetEye():null,contentPoints());viewer.requestRender();syncUrl();
  }
  function syncUrl(){
    if(params.qa)return;const q=new URLSearchParams();q.set('chunk',state.center.join(','));q.set('radius',String(state.radius));q.set('view',viewer.view);
    if(params.custom)q.set('base',params.base);else if(params.revision!=='main')q.set('revision',params.revision);
    history.replaceState(null,'','?'+q.toString());
  }
  function stats(blocks,faces,regions){
    $('stat-center').textContent=`${state.center[0]}, ${state.center[1]} (blocks ${state.center[0]*16}, ${state.center[1]*16})`;
    $('stat-regions').textContent=regions.map(([rx,rz])=>{const h=client.headers.get(rx+','+rz);return regionName([rx,rz])+(h===null?' (not found)':h?` (${fmt(h.count)} chunks stored)`:'');}).join(', ');
    $('stat-blocks').textContent=fmt(blocks);$('stat-faces').textContent=fmt(faces);$('stat-requests').textContent=fmt(client.stats.requests);$('stat-bytes').textContent=bytes(client.stats.bytes);
    const unknown=[...registry.unknown];$('unknown-blocks').hidden=!unknown.length;$('unknown-blocks').textContent=unknown.length?'Drawn in a neutral color (no table entry): '+unknown.join(', ')+'.':'';
  }
  async function mesh(signal){
    viewer.clearChunks();viewer.showHighlight(null);pinned=null;let blocks=0,faces=0,i=0;
    const r=state.radius;let top=state.floorY+8;for(const c of world.chunks.values())for(const t of c.tops)if(t+1>top)top=t+1;
    viewer.bounds.set(new THREE.Vector3(-r*16,state.floorY,-r*16),new THREE.Vector3((r+1)*16,top,(r+1)*16));
    for(const [cx,cz] of spiral(state.center,r)){
      if(!world.get(cx,cz))continue;
      const m=meshChunk(world,cx,cz,{floorY:state.floorY,originX:state.originX,originZ:state.originZ});blocks+=m.blocks;faces+=m.faces;viewer.setChunk(cx+','+cz,m);
      if(++i%12===0&&!params.qa){viewer.requestRender();await nextFrame();if(signal?.aborted)return null;}
    }
    return {blocks,faces};
  }
  async function load(center,radius,{reframe=true,floor=null}={}){
    session?.abort();const controller=new AbortController(),signal=controller.signal;session=controller;
    state={...state,center,radius,floor,originX:center[0]*16,originZ:center[1]*16,statuses:new Map(),errors:[]};
    $('goto-x').value=String(center[0]);$('goto-z').value=String(center[1]);$('goto-radius').value=String(radius);
    if(![...$('goto-radius').options].some(o=>o.value===String(radius))){const o=doc.createElement('option');o.value=o.textContent=String(radius);$('goto-radius').append(o);$('goto-radius').value=String(radius);}
    const coords=spiral(center,radius),regions=[...new Map(coords.map(c=>{const r=chunkRegion(...c);return [r.join(','),r];})).values()];
    for(const c of coords)state.statuses.set(c.join(','),'pending');drawCoverage();syncUrl();
    wrap.setAttribute('aria-busy','true');report({status:'loading'});
    setStatus(`Reading ${fmt(coords.length)} chunks around chunk ${center[0]}, ${center[1]} from ${regions.map(regionName).join(', ')}…`);
    world=new BlockWorld(registry);let drawn=0;
    try{
      await client.loadChunks(coords,{signal,onChunk(r){
        if(signal.aborted)return;
        if(r.chunk)world.set(prepareChunk(r.chunk,registry));
        state.statuses.set(r.cx+','+r.cz,r.status==='ok'?'loaded':r.status);if(r.error)state.errors.push(`chunk ${r.cx}, ${r.cz}: ${r.error.message}`);
        if(++drawn%9===0)drawCoverage();
      }});
      if(signal.aborted)return;
      state.floorY=state.floor??autoFloor(world);$('floor-y').value=String(state.floorY);
      const counts=drawCoverage();
      const result=await mesh(signal);if(!result||signal.aborted)return;
      if(reframe){viewer.frame(viewer.view==='street'?streetEye():null,contentPoints());}
      stats(result.blocks,result.faces,regions);
      const missing=counts.absent+counts['no-region'];
      let message=counts.loaded?`Showing ${fmt(counts.loaded)} of ${fmt(coords.length)} chunks around chunk ${center[0]}, ${center[1]} · ${fmt(result.blocks)} blocks above Y ${state.floorY}.`:counts['no-region']===coords.length?`No region file at this source covers chunk ${center[0]}, ${center[1]}.`:`No stored chunks around chunk ${center[0]}, ${center[1]}.`;
      if(missing&&counts.loaded)message+=` ${fmt(missing)} not stored.`;
      if(state.errors.length)message+=` ${fmt(state.errors.length)} could not be read (${state.errors[0]}).`;
      setStatus(message);wrap.setAttribute('aria-busy','false');
      viewer.renderNow();if(params.qa){await nextFrame();await nextFrame();}
      if(signal.aborted)return;
      report({status:counts.loaded===0&&state.errors.length?'error':'done',chunks_requested:coords.length,chunks_loaded:counts.loaded,blocks:result.blocks,faces:result.faces,missing,errors:state.errors.length,
        error:state.errors[0]||null,floor_y:state.floorY,view:viewer.view,yaw:viewer.yaw,pitch:viewer.pitch,center_chunk:center,radius,regions:regions.map(regionName),base:params.base,requests:client.stats.requests,bytes:client.stats.bytes,unknown_blocks:[...registry.unknown]});
    }catch(error){
      if(signal.aborted)return;
      const message=error?.message||String(error);setStatus('The blocks could not be read: '+message);wrap.setAttribute('aria-busy','false');drawCoverage();
      report({status:'error',error:message,chunks_requested:coords.length,chunks_loaded:0,blocks:0,missing:0});
    }finally{if(session===controller)session=null;}
  }

  installControls(viewer,canvas,{onHover,onTap,onKey(k){
    const views={'1':'orbit','2':'iso','3':'top','4':'street'};
    if(views[k]){setView(views[k]);return true;}
    if(k==='r'){viewer.setView(viewer.view);setView(viewer.view);return true;}
    if(k==='Escape'){pinned=null;viewer.showHighlight(null);$('readout-block').textContent='Point at a block to name it';return true;}
    return false;
  }});
  for(const b of doc.querySelectorAll('.view-switch button'))b.onclick=()=>setView(b.dataset.view);
  $('goto').addEventListener('submit',e=>{e.preventDefault();const x=Number($('goto-x').value),z=Number($('goto-z').value);if(!Number.isSafeInteger(x)||!Number.isSafeInteger(z)){setStatus('Enter whole-number chunk coordinates.');return;}load([x,z],Number($('goto-radius').value));});
  $('goto-tower').onclick=()=>load([WATER_TOWER_BLOCK[0]>>4,WATER_TOWER_BLOCK[1]>>4],Number($('goto-radius').value));
  $('floor-form').addEventListener('submit',async e=>{e.preventDefault();const y=Number($('floor-y').value);if(!Number.isInteger(y)||y<-64||y>960){setStatus('Enter a whole-number Y between -64 and 960.');return;}state.floor=state.floorY=y;const result=await mesh();if(result){stats(result.blocks,result.faces,[...new Map(spiral(state.center,state.radius).map(c=>{const r=chunkRegion(...c);return [r.join(','),r];})).values()]);viewer.frame(viewer.view==='street'?streetEye():null,contentPoints());viewer.requestRender();setStatus(`Cut away below Y ${y} · ${fmt(result.blocks)} blocks drawn.`);}});
  const setPanel=open=>{doc.body.dataset.panel=open?'open':'closed';$('panel-toggle').setAttribute('aria-expanded',String(open));};
  $('panel-toggle').onclick=()=>setPanel(doc.body.dataset.panel!=='open');$('panel-close').onclick=()=>{setPanel(false);$('panel-toggle').focus();};
  setPanel(!params.qa&&innerWidth>=1024);
  doc.body.dataset.view=params.view;for(const b of doc.querySelectorAll('.view-switch button'))b.setAttribute('aria-pressed',String(b.dataset.view===params.view));
  load(params.center,params.radius,{floor:params.floor});
  return {viewer,load,get world(){return world;},get state(){return state;}};
}

if(globalThis.document?.getElementById('viewer'))installBlocksPage();
