import {SourceBoundV2Client,polygonContains} from './source-bound-v2.mjs';
import {HF_RELEASE} from './hf-release.mjs';
import {PreviewController,drawCrop} from './block-preview.mjs?v=hf-v2-1';
export function installPublicBlocks(root,{onCoverage,onSelect}){
 const $=id=>root.querySelector('#'+id),client=new SourceBoundV2Client(HF_RELEASE);let connection=null,connected=false,rows=[],selected=null,crop=null,rotation=0;
 const base=`https://huggingface.co/datasets/${HF_RELEASE.repo}`,revision=base+'/tree/'+HF_RELEASE.revision;
 $('hf-repository').href=revision;$('hf-provenance').href=client.url('provenance.json');$('hf-license').href=base+'/blob/'+HF_RELEASE.revision+'/LICENSE';$('hf-source-offer').href=client.url('sources/source-bundle.json');
 $('hf-release-note').textContent=HF_RELEASE.coverage_note+' Pinned revision '+HF_RELEASE.revision+'.';
 const controller=new PreviewController(state=>{
  crop=state.crop||null;rotation=0;$('hf-canvas').hidden=state.status!=='ready';$('hf-turn').hidden=state.status!=='ready';$('hf-cancel').hidden=state.status!=='loading';$('hf-column-wrap').hidden=state.status!=='ready';$('hf-downloads').hidden=state.status!=='ready';
  $('hf-preview-status').textContent=state.status==='ready'?crop.quality_disclosure:state.status==='loading'?'Downloading and verifying selected crop…':state.message||'Choose a published tile.';
  $('hf-columns').replaceChildren();$('hf-column-detail').textContent='';
  if(crop){
   for(const [i,c] of crop.roof_support_columns.entries()){const option=document.createElement('option');option.value=String(i);option.textContent=`X ${crop.origin[0]+c.local_xz[0]}, Z ${crop.origin[2]+c.local_xz[1]} · ${c.roof_support}`;$('hf-columns').append(option);}
   $('hf-columns').disabled=!crop.roof_support_columns.length;showColumn();
   $('hf-crop-download').href=client.url(crop.transport.crop_path);$('hf-manifest-download').href=client.url(crop.transport.manifest_path);
   $('hf-integrity').textContent=`${crop.occupied_total.toLocaleString()} occupied positions · ${(crop.transport.crop_bytes/1024).toFixed(1)} KiB. Crop SHA-256 ${crop.transport.crop_sha256}. Tile generation ${crop.generation.id_sha256}. Dataset generation ${crop.generation.dataset_id_sha256}.`;
  }
  draw();if(crop)$('hf-selection').scrollIntoView({block:'start'});
 },{decode:v=>v});
 function draw(){drawCrop($('hf-canvas'),crop,rotation,{supportColors:true});}
 function showColumn(){const c=crop?.roof_support_columns[Number($('hf-columns').value)];$('hf-column-detail').textContent=!c?'No mapped roof columns in this crop.':c.roof_support==='direct'?`Direct lidar return. World roof Y ${c.roof_y}; observation ${c.observation_id}; source SHA-256 ${c.source_asset_sha256}.`:`${c.roof_support}. World roof Y ${c.roof_y??'unknown'}; uncertainty ${c.uncertainty_m??'unknown'} m; support distance ${c.support_distance_m??'unknown'} m; model ${c.model_id||'none'}; inside model domain: ${c.inside_model_domain===true?'yes':'no'}.`;}
 function select(tile){selected=tile.id;for(const b of $('hf-tiles').querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.tile===tile.id));$('hf-selection').textContent='Tile '+tile.id+' · '+tile.size[0]+' × '+tile.size[2]+' m';onSelect(tile);controller.select({load:signal=>client.crop(tile,signal)});}
 function renderTiles(){
  $('hf-tiles').replaceChildren();const xs=rows.map(t=>t.origin[0]),zs=rows.map(t=>t.origin[2]),minX=Math.min(...xs),minZ=Math.min(...zs),width=(Math.max(...xs)-minX)/16+1;
  $('hf-tiles').style.gridTemplateColumns=`repeat(${width},minmax(0,1fr))`;
  for(const tile of rows){const b=document.createElement('button');b.type='button';b.dataset.tile=tile.id;b.textContent=tile.id;b.setAttribute('aria-pressed','false');b.setAttribute('aria-label','Preview tile '+tile.id);b.style.gridColumn=String((tile.origin[0]-minX)/16+1);b.style.gridRow=String((tile.origin[2]-minZ)/16+1);b.onclick=()=>select(tile);$('hf-tiles').append(b);}
 }
 async function open(){
  if(connected)return;connection?.abort();connection=new AbortController();const pending=connection;const signal=AbortSignal.any([connection.signal,AbortSignal.timeout(30000)]);$('hf-connect').disabled=true;$('hf-connect-cancel').hidden=false;$('hf-status').textContent='Verifying published coverage (index and bounded manifests; no block crops yet)…';
  try{await client.connect(signal);const coverage=await client.coverage(signal);signal.throwIfAborted();if(connection!==pending)return;rows=coverage;connected=true;renderTiles();onCoverage(rows);$('hf-status').textContent=`${rows.length} published 16 m preview tiles. Select a square on the map or in the tile grid below. Gaps have no published preview. Coverage is limited to this Oakbrook footprint.`;$('hf-connect').hidden=true;}
  catch(error){if(connection!==pending)return;$('hf-status').textContent=signal.aborted?'Coverage request stopped. Retry when ready.':'HF coverage could not load: '+error.message;$('hf-connect').disabled=false;}
  finally{if(connection===pending)$('hf-connect-cancel').hidden=true;}
 }
 $('hf-connect').onclick=open;$('hf-connect-cancel').onclick=()=>connection?.abort();$('hf-cancel').onclick=()=>{controller.cancel();crop=null;$('hf-preview-status').textContent='Preview request stopped. Select a tile to retry.';$('hf-cancel').hidden=true;};
 $('hf-choose').onclick=()=>$('hf-tiles').scrollIntoView({block:'start'});
 $('hf-turn').onclick=()=>{rotation=(rotation+1)%4;draw();};$('hf-columns').onchange=showColumn;
 return {open,cancel(){connection?.abort();controller.cancel();},draw(ctx,map){for(const t of rows){const points=t.corners.map(p=>map.project(p));ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();ctx.fillStyle=t.id===selected?'#315f83':'#a25922';ctx.globalAlpha=.32;ctx.fill();ctx.globalAlpha=1;ctx.strokeStyle=t.id===selected?'#194b72':'#744019';ctx.lineWidth=t.id===selected?3:1.5;ctx.stroke();}},selectAt(point){const tile=rows.find(t=>polygonContains(t.corners,point));if(!tile)return false;select(tile);return true;},get rows(){return rows;}};
}
