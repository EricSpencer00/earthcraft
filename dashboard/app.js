const $=id=>document.getElementById(id);
const number=value=>Number(value||0).toLocaleString();
const stateLabel=value=>String(value||'unknown').replaceAll('_',' ').replaceAll('-',' ');
const palette=['oklch(0.985 0.006 85)','oklch(0.76 0.018 145)','oklch(0.45 0.10 155)'];
const outside='oklch(0.89 0.018 92)';
const installed='oklch(0.64 0.13 72)';
let snapshot=null,atlas=null,busy=false,refreshQueued=false,mapMetrics=null;

function setMapMessage(message){$('mapnote').textContent=message}

function renderSnapshot(){
  if(!snapshot)return;
  const rollup=snapshot.rollup||{},local=snapshot.local||{},performance=snapshot.performance||{};
  $('phase').textContent=`${stateLabel(rollup.state)} · ${snapshot.scope?.label||'Earth'}`;
  $('connection').textContent=snapshot.source==='local'?'Local journal connected':'Public snapshot · chunk view unavailable';
  $('updated').textContent=snapshot.updated_utc?`Updated ${new Date(snapshot.updated_utc).toLocaleString()}`:'';
  $('rollup').textContent=stateLabel(rollup.state);
  $('geometry').textContent=number(rollup.generated_tiles);
  $('sources').textContent=local.source_tiles_total?`${number(local.source_tiles_complete)} / ${number(local.source_tiles_total)}`:number(local.source_tiles_complete);
  $('recovery').textContent=performance.parallel_tile_workers+'; '+performance.source_cache_coordination+'.';
  $('log').textContent=Object.entries(local.stages||{}).map(([stage,counts])=>`${stage}: ${Object.entries(counts).map(([state,count])=>`${state} ${count}`).join(', ')||'no jobs'}`).join('\n')||'No local run connected.';
  $('footer-state').textContent=`Observed ${new Date(snapshot.updated_utc||Date.now()).toLocaleTimeString()}`;
  $('warning').hidden=false;
  $('warning').textContent=snapshot.claims?.global_complete?'Global completion is not reported.':'Blank regions have not been measured yet.';
}

function resizeCanvas(){
  if(!atlas)return;
  const canvas=$('map'),rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  canvas.width=Math.max(1,Math.round(rect.width*dpr));canvas.height=Math.max(1,Math.round(rect.height*dpr));
  drawAtlas();
}

function distanceLabel(blocks){
  return blocks>=1000?`${(blocks/1000).toFixed(1)} km`:`${number(blocks)} m`;
}

function canvasPoint(blockX,blockZ){
  const {left,top,cell,cellSize,leftBlock,topBlock}=mapMetrics;
  return {x:left+(blockX-leftBlock)/cellSize*cell,y:top+(blockZ-topBlock)/cellSize*cell};
}

function drawAtlas(){
  if(!atlas)return;
  const canvas=$('map'),ctx=canvas.getContext('2d'),width=atlas.area.width_cells,height=atlas.area.height_cells;
  const cssWidth=canvas.clientWidth,cssHeight=canvas.clientHeight,dpr=window.devicePixelRatio||1;
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,cssWidth,cssHeight);
  const margin=20,cell=Math.max(1,Math.min((cssWidth-margin*2)/width,(cssHeight-margin*2)/height));
  const mapWidth=cell*width,mapHeight=cell*height,left=(cssWidth-mapWidth)/2,top=(cssHeight-mapHeight)/2;
  mapMetrics={left,top,cell,width,height,cellSize:atlas.area.cell_size_blocks,leftBlock:atlas.area.left_block,topBlock:atlas.area.top_block};
  ctx.fillStyle=outside;ctx.fillRect(left,top,mapWidth,mapHeight);
  for(let row=0;row<height;row++){
    for(let column=0;column<width;column++){
      const code=atlas.cells[row*width+column];
      if(code<0)continue;
      ctx.fillStyle=palette[code];
      const gap=cell>=2?.3:0;
      ctx.fillRect(left+column*cell,top+row*cell,Math.max(1,cell-gap),Math.max(1,cell-gap));
    }
  }
  if(cell>=5){
    ctx.strokeStyle='rgba(43,52,44,.14)';ctx.lineWidth=1;ctx.beginPath();
    for(let column=0;column<=width;column++){const x=Math.round(left+column*cell)+.5;ctx.moveTo(x,top);ctx.lineTo(x,top+mapHeight)}
    for(let row=0;row<=height;row++){const y=Math.round(top+row*cell)+.5;ctx.moveTo(left,y);ctx.lineTo(left+mapWidth,y)}
    ctx.stroke();
  }
  if(atlas.area.id==='queue'){
    ctx.fillStyle='oklch(0.64 0.13 72 / 0.2)';ctx.strokeStyle=installed;ctx.lineWidth=1;
    for(const tile of atlas.overlays?.installed?.tiles||[]){
      const a=canvasPoint(tile.x,tile.z),b=canvasPoint(tile.x+tile.size,tile.z+tile.size);
      ctx.fillRect(a.x,a.y,b.x-a.x,b.y-a.y);ctx.strokeRect(a.x+.5,a.y+.5,b.x-a.x-1,b.y-a.y-1);
    }
    const extent=atlas.overlays?.installed;
    if(extent?.width_blocks){
      const a=canvasPoint(extent.left_block,extent.top_block),b=canvasPoint(extent.right_block,extent.bottom_block);
      ctx.strokeStyle=installed;ctx.lineWidth=2.5;ctx.strokeRect(a.x,a.y,b.x-a.x,b.y-a.y);
    }
  }
  const anchor=atlas.overlays?.anchor,point=anchor?canvasPoint(anchor.x,anchor.z):null;
  if(point&&point.x>=left&&point.x<=left+mapWidth&&point.y>=top&&point.y<=top+mapHeight){
    ctx.strokeStyle='oklch(0.25 0.022 155)';ctx.fillStyle='oklch(0.985 0.006 85)';ctx.lineWidth=1.5;
    ctx.beginPath();ctx.arc(point.x,point.y,5,0,Math.PI*2);ctx.fill();ctx.stroke();
    ctx.beginPath();ctx.moveTo(point.x-8,point.y);ctx.lineTo(point.x+8,point.y);ctx.moveTo(point.x,point.y-8);ctx.lineTo(point.x,point.y+8);ctx.stroke();
    ctx.font='600 11px Helvetica Neue, Arial, sans-serif';ctx.fillStyle='oklch(0.25 0.022 155)';
    ctx.fillText(anchor.label,point.x+11,point.y-7);
  }
}

function renderAtlas(){
  if(!atlas){
    $('grid-size').textContent='Unavailable';$('grid-count').textContent='Open the local dashboard for the chunk atlas.';
    $('chunk-count').textContent='Unavailable';$('border').textContent='Unavailable';$('leases').textContent='Unavailable';
    setMapMessage('The public snapshot does not include local chunk evidence.');return;
  }
  const {area,counts}=atlas;
  const widthBlocks=area.width_chunks*16,heightBlocks=area.height_chunks*16;
  $('grid-size').textContent=`${distanceLabel(widthBlocks)} × ${distanceLabel(heightBlocks)}`;
  $('grid-count').textContent=area.id==='queue'
    ?`${number(area.tiles)} tiles in the frozen Chicago plan · one square is one ${area.cell_label}.`
    :area.id==='live'
      ?`${number(counts.green)} chunks accepted by Minecraft · one square is one ${area.cell_label}.`
      :`${number(area.tiles)} original generation tiles · one square is one ${area.cell_label}.`;
  $('chunk-count').textContent=`${number(counts.white+counts.gray+counts.green)} ${area.cell_label}${counts.white+counts.gray+counts.green===1?'':'s'}`;
  $('map-unit').textContent=area.cell_size_blocks===16?'16 × 16 BLOCK CHUNKS':'256 × 256 BLOCK TILES';
  $('map-title').textContent=area.label;
  const workers=atlas.active_worker_owners||0,oldLeases=atlas.superseded_leases||0;
  const deferred=atlas.deferred_tiles||0,steals=atlas.work_steals||0;
  $('leases').textContent=workers
    ?`${number(workers)} workers${deferred?` · ${number(deferred)} slow tiles deferred`:''}${oldLeases?` · ${number(oldLeases)} old leases expiring`:''}`
    :deferred?`${number(deferred)} slow tiles deferred`:'No active leases';
  if(steals)$('recovery').textContent=`${number(steals)} one-second waits skipped; workers kept moving on other tiles.`;
  const border=atlas.border;
  $('border').textContent=border?(border.synced?'Full plan aligned':`Too small · target ${distanceLabel(border.expected.size)}`):'Not checked';
  $('map-updated').textContent=atlas.updated_utc?`Atlas ${new Date(atlas.updated_utc).toLocaleTimeString()}`:'';
  const unit=area.cell_label+(counts.white+counts.gray+counts.green===1?'':'s');
  const installedCount=atlas.overlays?.installed?.tiles?.length||0;
  const installedExtent=atlas.overlays?.installed||{};
  const summary=`${number(counts.white)} white, ${number(counts.gray)} gray, ${number(counts.green)} green ${unit}. ${atlas.failed_tiles?number(atlas.failed_tiles)+' failed source tiles.':''}`;
  $('map-summary').textContent=summary;
  const live=area.id==='live',queue=area.id==='queue',minecraft=atlas.minecraft_import;
  $('legend-outside-label').textContent=live?'Not playable yet':'Outside plan';
  $('legend-finished-label').textContent=live?'Green · playable now':'Green · finished build layer';
  $('legend-queued').hidden=live;$('legend-source').hidden=live;$('legend-installed').hidden=live;
  $('map-caption').textContent=live
    ?'The Water Tower marks the coordinate origin. Green chunks are present in the running Minecraft world.'
    :queue?'This is build progress, not playable coverage. Switch to Playable in Minecraft now for the exact in-game edge.'
      :'The original installed footprint is retained as a provenance reference.';
  setMapMessage(live&&minecraft?.needs_minecraft_relaunch
    ?`${number(counts.green)} playable chunks. Minecraft import paused at a full ${number(minecraft.capacity_chunks)}-chunk handoff; relaunch the Earthcraft profile to resume it.`
    :queue
    ?`${number(area.tiles)} planned tiles; generated colors do not mean playable in Minecraft.`
    :live?`${number(counts.green)} playable chunks. Northern edge: ${distanceLabel(Math.max(0,-area.top_block))} from the Water Tower.`
      :`${number(installedCount)} original tiles across ${distanceLabel(installedExtent.width_blocks||0)} × ${distanceLabel(installedExtent.height_blocks||0)}.`);
  if(live&&minecraft?.needs_minecraft_relaunch){
    $('connection').textContent='Generation running · Minecraft import needs relaunch';
    $('warning').hidden=false;
    $('warning').textContent='The build pipeline is still advancing. Relaunch Earthcraft — Geographic Explorer once to load the repaired importer and resume playable coverage.';
  }
  resizeCanvas();
}

async function readLocal(){
  const [earth,chunks]=await Promise.all([
    fetch('./api/earth',{cache:'no-store'}),
    fetch(`./api/chunks?area=${encodeURIComponent($('area').value)}`,{cache:'no-store'})
  ]);
  if(!earth.ok||!chunks.ok)throw Error('local feed unavailable');
  snapshot={...await earth.json(),source:'local'};atlas=await chunks.json();
}

async function readSnapshot(){
  try{await readLocal()}
  catch(localError){
    const response=await fetch('./progress/earth.json',{cache:'no-store'});
    if(!response.ok)throw Error('public snapshot unavailable');
    snapshot={...await response.json(),source:'public'};atlas=null;
  }
}

async function refresh(){
  if(busy){refreshQueued=true;return}busy=true;$('refresh').disabled=true;
  try{await readSnapshot();renderSnapshot();renderAtlas()}
  catch(error){$('connection').textContent='Progress feed disconnected';$('warning').hidden=false;$('warning').textContent='The latest progress snapshot could not be read.'}
  finally{busy=false;$('refresh').disabled=false;if(refreshQueued){refreshQueued=false;refresh()}}
}

$('map').addEventListener('pointermove',event=>{
  if(!atlas||!mapMetrics)return;
  const rect=$('map').getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
  const column=Math.floor((x-mapMetrics.left)/mapMetrics.cell),row=Math.floor((y-mapMetrics.top)/mapMetrics.cell);
  if(column<0||row<0||column>=mapMetrics.width||row>=mapMetrics.height)return;
  const code=atlas.cells[row*mapMetrics.width+column];
  if(code<0){$('cell').textContent=atlas.area.id==='live'?'Not playable in Minecraft yet.':'Outside the planned Chicago boundary.';return}
  const blockX=atlas.area.left_block+column*atlas.area.cell_size_blocks;
  const blockZ=atlas.area.top_block+row*atlas.area.cell_size_blocks;
  if(atlas.area.cell_size_blocks===256){
    $('cell').textContent=`Tile ${Math.floor(blockX/256)}_${Math.floor(blockZ/256)} · ${atlas.palette[code]}.`;
  }else{
    $('cell').textContent=`Chunk ${Math.floor(blockX/16)}, ${Math.floor(blockZ/16)} · ${atlas.palette[code]}.`;
  }
});
$('map').addEventListener('pointerleave',()=>{$('cell').textContent='Move over the atlas to inspect a tile.'});
$('refresh').onclick=refresh;
$('area').onchange=refresh;
$('auto').onchange=()=>{if($('auto').checked)refresh()};
window.addEventListener('resize',resizeCanvas);
refresh();
setInterval(()=>{if($('auto').checked&&!document.hidden)refresh()},10000);
