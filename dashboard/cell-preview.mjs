// Small in-panel 3D view of a selected map cell. Loaded on demand (pulls in three.js).
import {ChunkViewer,installControls,spiral,autoFloor} from './chunk-viewer.mjs?v=lake-1';
import {RegionClient,hfRegionBase,VIEW_CACHES} from './anvil.mjs?v=tiles-1';
import {BlockWorld,BlockRegistry,prepareChunk,meshChunk} from './chunk-mesh.mjs?v=blocks-2';
import {LakeClient} from './lake.mjs?v=lake-1';
import * as THREE from './assets/vendor/three/three.module.min.js';

const client=new RegionClient({base:hfRegionBase(),packs:VIEW_CACHES}),registry=new BlockRegistry();
const viewers=new WeakMap();
let session=null;

// Draws the chunks around block (x,z) into canvas. Resolves to {status:'done'|'empty'|'aborted', chunks}.
export async function showCell(canvas,{x,z,radius=2,base=null,layout='bucketed'}){
  session?.abort();const controller=new AbortController(),signal=controller.signal;session=controller;
  let viewer=viewers.get(canvas);
  if(!viewer){
    viewer=new ChunkViewer(canvas);viewers.set(canvas,viewer);
    const fit=()=>viewer.setSize(canvas.clientWidth,canvas.clientHeight);new ResizeObserver(fit).observe(canvas);fit();
    installControls(viewer,canvas,{onHover(){},onTap(){},onKey(){return false;}});
  }
  viewer.clearChunks();viewer.requestRender();
  const center=[Math.floor(x/16),Math.floor(z/16)],coords=spiral(center,radius),world=new BlockWorld(registry);
  let loaded=0,errors=0,firstError=null;
  const source=layout==='lake'?new LakeClient({base}):base?new RegionClient({base,layout,maxConcurrent:2}):client;
  await source.loadChunks(coords,{signal,onChunk(r){if(r.chunk&&!signal.aborted){world.set(prepareChunk(r.chunk,registry));loaded++;}if(r.status==='error'){errors++;firstError??=r.error;}}});
  if(signal.aborted)return {status:'aborted'};
  if(!loaded){viewer.requestRender();if(errors)throw firstError||Error('Blocks could not be read.');return {status:'empty',chunks:0};}
  const floorY=autoFloor(world),originX=center[0]*16,originZ=center[1]*16;let top=floorY+8;
  for(const c of world.chunks.values())for(const t of c.tops)if(t+1>top)top=t+1;
  viewer.bounds.set(new THREE.Vector3(-radius*16,floorY,-radius*16),new THREE.Vector3((radius+1)*16,top,(radius+1)*16));
  for(const [cx,cz] of coords)if(world.get(cx,cz))viewer.setChunk(cx+','+cz,meshChunk(world,cx,cz,{floorY,originX,originZ}));
  viewer.setView('orbit',{yaw:45,pitch:40});viewer.frame();viewer.renderNow();
  return {status:'done',chunks:loaded};
}
