const $=id=>document.getElementById(id);
const number=value=>Number(value||0).toLocaleString();
const stateLabel=value=>String(value||'unknown').replaceAll('_',' ').replaceAll('-',' ');
const DEFAULT_COLORS=['oklch(.985 .006 85)','oklch(.76 .018 145)','oklch(.45 .10 155)','oklch(.64 .13 72)','oklch(.57 .16 30)'];
const outside='oklch(.89 .018 92)';
const installed='oklch(.64 .13 72)';
let snapshot=null,atlas=null,busy=false,refreshQueued=false,mapMetrics=null;

function setMapMessage(message){$('mapnote').textContent=message;}
function countCells(counts={}){return Object.values(counts).reduce((total,value)=>total+Number(value||0),0);}

function stageCode(cell){
  if(cell.state==='failed')return 4;
  if(cell.state==='running'||cell.state==='leased')return 3;
  if(['generated','styled','verified'].includes(cell.state))return 2;
  if(cell.state==='sourced')return 1;
  return 0;
}

function publishedAtlas(data){
  const rows=(data.cells||[]).map(cell=>{
    const id=String(cell.tile_id||String(cell.id||'').split('/').pop()||'');
    const match=id.match(/^(-?\d+)_(-?\d+)$/);
    return match?{...cell,id,x:Number(match[1]),z:Number(match[2])}:null;
  }).filter(Boolean);
  if(!rows.length)return null;
  const left=Math.min(...rows.map(row=>row.x));
  const top=Math.min(...rows.map(row=>row.z));
  const right=Math.max(...rows.map(row=>row.x))+1;
  const bottom=Math.max(...rows.map(row=>row.z))+1;
  const width=right-left,height=bottom-top;
  const cells=Array(width*height).fill(-1);
  const cellIds=Array(width*height).fill(null);
  for(const row of rows){
    const index=(row.z-top)*width+row.x-left;
    cells[index]=stageCode(row);
    cellIds[index]=row.id;
  }
  const counts={white:0,gray:0,green:0,active:0,failed:0};
  for(const code of cells){
    if(code===0)counts.white+=1;
    if(code===1)counts.gray+=1;
    if(code===2)counts.green+=1;
    if(code===3)counts.active+=1;
    if(code===4)counts.failed+=1;
  }
  const cellSize=Number(data.cell_grid?.cell_size_m)||256;
  return {
    source:'published',
    area:{
      id:'published',label:'Published Chicago progress',left_block:left*cellSize,top_block:top*cellSize,
      width_cells:width,height_cells:height,width_chunks:width*cellSize/16,height_chunks:height*cellSize/16,
      cell_size_blocks:cellSize,cell_label:'generation cell',tiles:rows.length
    },
    palette:['Queued','Source ready','Building layer ready','In progress','Needs review'],
    colors:DEFAULT_COLORS,cells,cell_ids:cellIds,counts,failed_tiles:counts.failed,
    updated_utc:data.updated_utc,overlays:{}
  };
}

function renderSnapshot(){
  if(!snapshot)return;
  const rollup=snapshot.rollup||{},local=snapshot.local||{},performance=snapshot.performance||{};
  if(snapshot.source==='public')$('area').value='published';
  $('phase').textContent=stateLabel(rollup.state)+' · '+(snapshot.regions?.[0]?.label||snapshot.scope?.label||'Earth');
  $('connection').textContent=snapshot.source==='local'?'Local source connected':'Published progress snapshot';
  $('updated').textContent=snapshot.updated_utc?'Updated '+new Date(snapshot.updated_utc).toLocaleString():'';
  $('rollup').textContent=stateLabel(rollup.state);
  $('geometry').textContent=number(rollup.generated_tiles);
  $('sources').textContent=local.source_tiles_total?number(local.source_tiles_complete)+' / '+number(local.source_tiles_total):number(local.source_tiles_complete);
  $('recovery').textContent=snapshot.source==='local'
    ?(performance.parallel_tile_workers||'Local workers')+'; '+(performance.source_cache_coordination||'local source cache')+'.'
    :'';
  $('log').textContent=Object.entries(local.stages||{}).map(([stage,counts])=>stage+': '+Object.entries(counts).map(([state,count])=>state+' '+count).join(', ')||'no jobs').join('\n')||'Published aggregate';
  $('footer-state').textContent='Observed '+new Date(snapshot.updated_utc||Date.now()).toLocaleTimeString();
  $('warning').hidden=true;
}

function resizeCanvas(){
  if(!atlas)return;
  const canvas=$('map'),rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  canvas.width=Math.max(1,Math.round(rect.width*dpr));
  canvas.height=Math.max(1,Math.round(rect.height*dpr));
  drawAtlas();
}

function distanceLabel(blocks){return blocks>=1000?(blocks/1000).toFixed(1)+' km':number(blocks)+' m';}
function canvasPoint(blockX,blockZ){
  const {left,top,cell,cellSize,leftBlock,topBlock}=mapMetrics;
  return {x:left+(blockX-leftBlock)/cellSize*cell,y:top+(blockZ-topBlock)/cellSize*cell};
}

function drawAtlas(){
  if(!atlas)return;
  const canvas=$('map'),ctx=canvas.getContext('2d'),width=atlas.area.width_cells,height=atlas.area.height_cells;
  const cssWidth=canvas.clientWidth,cssHeight=canvas.clientHeight,dpr=window.devicePixelRatio||1;
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,cssWidth,cssHeight);
  const margin=20,cell=Math.max(1,Math.min((cssWidth-margin*2)/width,(cssHeight-margin*2)/height));
  const mapWidth=cell*width,mapHeight=cell*height,left=(cssWidth-mapWidth)/2,top=(cssHeight-mapHeight)/2;
  mapMetrics={left,top,cell,width,height,cellSize:atlas.area.cell_size_blocks,leftBlock:atlas.area.left_block,topBlock:atlas.area.top_block};
  ctx.fillStyle=outside;
  ctx.fillRect(left,top,mapWidth,mapHeight);
  const colors=atlas.colors||DEFAULT_COLORS;
  for(let row=0;row<height;row++){
    for(let column=0;column<width;column++){
      const code=atlas.cells[row*width+column];
      if(code<0)continue;
      ctx.fillStyle=colors[code]||colors[0];
      const gap=cell>=2?.3:0;
      ctx.fillRect(left+column*cell,top+row*cell,Math.max(1,cell-gap),Math.max(1,cell-gap));
    }
  }
  if(cell>=5){
    ctx.strokeStyle='rgba(43,52,44,.14)';
    ctx.lineWidth=1;
    ctx.beginPath();
    for(let column=0;column<=width;column++){
      const x=Math.round(left+column*cell)+.5;
      ctx.moveTo(x,top);
      ctx.lineTo(x,top+mapHeight);
    }
    for(let row=0;row<=height;row++){
      const y=Math.round(top+row*cell)+.5;
      ctx.moveTo(left,y);
      ctx.lineTo(left+mapWidth,y);
    }
    ctx.stroke();
  }
  if(atlas.area.id==='queue'){
    ctx.fillStyle='oklch(.64 .13 72 / .2)';
    ctx.strokeStyle=installed;
    ctx.lineWidth=1;
    for(const tile of atlas.overlays?.installed?.tiles||[]){
      const a=canvasPoint(tile.x,tile.z),b=canvasPoint(tile.x+tile.size,tile.z+tile.size);
      ctx.fillRect(a.x,a.y,b.x-a.x,b.y-a.y);
      ctx.strokeRect(a.x+.5,a.y+.5,b.x-a.x-1,b.y-a.y-1);
    }
    const extent=atlas.overlays?.installed;
    if(extent?.width_blocks){
      const a=canvasPoint(extent.left_block,extent.top_block),b=canvasPoint(extent.right_block,extent.bottom_block);
      ctx.strokeStyle=installed;
      ctx.lineWidth=2.5;
      ctx.strokeRect(a.x,a.y,b.x-a.x,b.y-a.y);
    }
  }
  const anchor=atlas.overlays?.anchor,point=anchor?canvasPoint(anchor.x,anchor.z):null;
  if(point&&point.x>=left&&point.x<=left+mapWidth&&point.y>=top&&point.y<=top+mapHeight){
    ctx.strokeStyle='oklch(.25 .022 155)';
    ctx.fillStyle='oklch(.985 .006 85)';
    ctx.lineWidth=1.5;
    ctx.beginPath();
    ctx.arc(point.x,point.y,5,0,Math.PI*2);
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(point.x-8,point.y);
    ctx.lineTo(point.x+8,point.y);
    ctx.moveTo(point.x,point.y-8);
    ctx.lineTo(point.x,point.y+8);
    ctx.stroke();
    ctx.font='600 11px Helvetica Neue, Arial, sans-serif';
    ctx.fillStyle='oklch(.25 .022 155)';
    ctx.fillText(anchor.label,point.x+11,point.y-7);
  }
}

function renderLegend(isPublished,counts,live){
  $('legend-outside-label').textContent=isPublished?'Outside published extent':live?'Outside playable coverage':'Outside the planned area';
  $('legend-queued-label').textContent='Queued';
  $('legend-source-label').textContent=isPublished?'Source ready':'Source data in';
  $('legend-finished-label').textContent=isPublished?'Building layer ready':live?'Playable in Minecraft':'Building layer ready';
  $('legend-queued').hidden=live;
  $('legend-source').hidden=live;
  $('legend-installed').hidden=live||isPublished;
  $('legend-running').hidden=!isPublished||!counts.active;
  $('legend-failed').hidden=!isPublished||!counts.failed;
}

function renderAtlas(){
  if(!atlas){
    $('grid-size').textContent='Unavailable';
    $('grid-count').textContent='The selected source has not published an atlas.';
    $('chunk-count').textContent='Unavailable';
    $('border').textContent='Unavailable';
    $('leases').textContent='Unavailable';
    setMapMessage('No Chicago atlas is available.');
    return;
  }
  const {area,counts}=atlas;
  const widthBlocks=area.width_chunks*16,heightBlocks=area.height_chunks*16;
  const isPublished=atlas.source==='published',live=area.id==='live',queue=area.id==='queue';
  const mapped=countCells(counts),built=Number(counts.green||0);
  if(isPublished){
    $('grid-size').textContent=number(area.tiles)+' cells';
    $('grid-count').textContent=distanceLabel(widthBlocks)+' × '+distanceLabel(heightBlocks)+' · 256 m per cell.';
    $('chunk-count').textContent=number(mapped)+' published generation cells';
    $('border').textContent='Published grid';
    $('leases').textContent='Snapshot';
    $('map-unit').textContent='PUBLISHED 256 METRE CELLS';
    $('map-title').textContent='Published Chicago progress';
    $('map-caption').textContent='Each square is a 256 metre Chicago generation cell.';
    setMapMessage(number(area.tiles)+' published cells · '+number(built)+' building layers ready.');
  }else{
    $('grid-size').textContent=distanceLabel(widthBlocks)+' × '+distanceLabel(heightBlocks);
    $('grid-count').textContent=queue
      ?number(area.tiles)+' tiles in the Chicago plan · one square is one '+area.cell_label+'.'
      :live
        ?number(counts.green)+' chunks imported into Minecraft · one square is one '+area.cell_label+'.'
        :number(area.tiles)+' original generation tiles · one square is one '+area.cell_label+'.';
    $('chunk-count').textContent=number(mapped)+' '+area.cell_label+(mapped===1?'':'s');
    $('map-unit').textContent=area.cell_size_blocks===16?'16 × 16 BLOCK CHUNKS':'256 × 256 BLOCK TILES';
    $('map-title').textContent=area.label;
    const workers=atlas.active_worker_owners||0,deferred=atlas.deferred_tiles||0,oldLeases=atlas.superseded_leases||0;
    $('leases').textContent=workers
      ?number(workers)+' workers'+(deferred?' · '+number(deferred)+' deferred':'')+(oldLeases?' · '+number(oldLeases)+' expiring':'')
      :deferred?number(deferred)+' deferred':'No active workers';
    const border=atlas.border;
    $('border').textContent=border?(border.synced?'Plan aligned':'Target '+distanceLabel(border.expected.size)):'Not checked';
    $('map-caption').textContent=live
      ?'The Water Tower marks the coordinate origin. Green chunks are present in the running Minecraft world.'
      :queue?'Build stages across the Chicago plan. The Water Tower marks the coordinate origin.'
        :'The original installed footprint is retained as a project record.';
    const minecraft=atlas.minecraft_import;
    setMapMessage(live&&minecraft?.needs_minecraft_relaunch
      ?number(counts.green)+' playable chunks. Relaunch Earthcraft — Geographic Explorer to resume imports.'
      :queue
        ?number(area.tiles)+' planned tiles · '+number(built)+' building layers ready.'
        :live?number(counts.green)+' chunks imported into Minecraft.'
          :number(area.tiles)+' original tiles across '+distanceLabel(atlas.overlays?.installed?.width_blocks||0)+' × '+distanceLabel(atlas.overlays?.installed?.height_blocks||0)+'.');
    if(live&&minecraft?.needs_minecraft_relaunch){
      $('connection').textContent='Generation running · Minecraft import paused';
      $('warning').hidden=false;
      $('warning').textContent='Relaunch Earthcraft — Geographic Explorer to resume Minecraft imports.';
    }
  }
  $('map-updated').textContent=atlas.updated_utc?'Atlas '+new Date(atlas.updated_utc).toLocaleTimeString():'';
  $('map-summary').textContent=number(counts.white)+' queued, '+number(counts.gray)+' source-ready, '+number(counts.green)+' building-ready cells.';
  renderLegend(isPublished,counts,live);
  resizeCanvas();
}

async function readLocal(){
  const [earth,chunks]=await Promise.all([
    fetch('./api/earth',{cache:'no-store'}),
    fetch('./api/chunks?area='+encodeURIComponent($('area').value),{cache:'no-store'})
  ]);
  if(!earth.ok||!chunks.ok)throw Error('local feed unavailable');
  snapshot={...await earth.json(),source:'local'};
  atlas=await chunks.json();
}

async function readPublished(){
  const response=await fetch('./progress/earth.json',{cache:'no-store'});
  if(!response.ok)throw Error('published snapshot unavailable');
  snapshot={...await response.json(),source:'public'};
  atlas=publishedAtlas(snapshot);
}

async function readSnapshot(){
  if($('area').value==='published')return readPublished();
  try{await readLocal();}catch(localError){await readPublished();}
}

async function refresh(){
  if(busy){refreshQueued=true;return;}
  busy=true;
  $('refresh').disabled=true;
  try{
    await readSnapshot();
    renderSnapshot();
    renderAtlas();
  }catch(error){
    $('connection').textContent='Progress feed disconnected';
    $('warning').hidden=false;
    $('warning').textContent='The latest published progress could not be read.';
  }finally{
    busy=false;
    $('refresh').disabled=false;
    if(refreshQueued){refreshQueued=false;refresh();}
  }
}

$('map').addEventListener('pointermove',event=>{
  if(!atlas||!mapMetrics)return;
  const rect=$('map').getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
  const column=Math.floor((x-mapMetrics.left)/mapMetrics.cell),row=Math.floor((y-mapMetrics.top)/mapMetrics.cell);
  if(column<0||row<0||column>=mapMetrics.width||row>=mapMetrics.height)return;
  const index=row*mapMetrics.width+column,code=atlas.cells[index];
  if(code<0){
    $('cell').textContent=atlas.source==='published'?'Outside the published Chicago extent.':atlas.area.id==='live'?'Outside playable coverage.':'Outside the planned Chicago area.';
    return;
  }
  const blockX=atlas.area.left_block+column*atlas.area.cell_size_blocks;
  const blockZ=atlas.area.top_block+row*atlas.area.cell_size_blocks;
  if(atlas.source==='published'){
    $('cell').textContent='Cell '+(atlas.cell_ids?.[index]||Math.floor(blockX/256)+'_'+Math.floor(blockZ/256))+' · '+atlas.palette[code]+'.';
    return;
  }
  if(atlas.area.cell_size_blocks===256){
    $('cell').textContent='Tile '+Math.floor(blockX/256)+'_'+Math.floor(blockZ/256)+' · '+atlas.palette[code]+'.';
    return;
  }
  $('cell').textContent='Chunk '+Math.floor(blockX/16)+', '+Math.floor(blockZ/16)+' · '+atlas.palette[code]+'.';
});
$('map').addEventListener('pointerleave',()=>{$('cell').textContent='Move over the atlas to inspect a cell.';});
$('refresh').onclick=refresh;
$('area').onchange=refresh;
$('auto').onchange=()=>{if($('auto').checked)refresh();};
window.addEventListener('resize',resizeCanvas);
refresh();
setInterval(()=>{if($('auto').checked&&!document.hidden)refresh();},10000);
