const $=id=>document.getElementById(id);
const number=value=>Number(value||0).toLocaleString();
const stateLabel=value=>({
  'regional-foundation':'Regional build',pending:'Queued',running:'Building',leased:'Building',
  complete:'Ready',generated:'Terrain & buildings ready',styled:'Surface detail added',
  verified:'Game checked',failed:'Needs review',unknown:'Not recorded'
}[value]||String(value||'Not recorded').replaceAll('_',' ').replaceAll('-',' '));
const color=name=>getComputedStyle(document.documentElement).getPropertyValue('--'+name).trim();
const palette=()=>['surface','gray','green','amber','rust'].map(color);
const countLabel=value=>typeof value==='number'&&Number.isFinite(value)?number(value):'Not recorded';
let snapshot=null,atlas=null,busy=false,refreshQueued=false,mapMetrics=null,zoom=1,selectedIndex=null,hoverIndex=null;

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
  const bounds=rows.reduce((box,row)=>({left:Math.min(box.left,row.x),top:Math.min(box.top,row.z),right:Math.max(box.right,row.x+1),bottom:Math.max(box.bottom,row.z+1)}),{left:Infinity,top:Infinity,right:-Infinity,bottom:-Infinity});
  const {left,top,right,bottom}=bounds;
  const width=right-left,height=bottom-top;
  if(!Number.isSafeInteger(width*height)||width*height>2000000)throw Error('Published grid is too large for this atlas');
  const cells=Array(width*height).fill(-1);
  const cellIds=Array(width*height).fill(null);
  const records=Array(width*height).fill(null);
  for(const row of rows){
    const index=(row.z-top)*width+row.x-left;
    cells[index]=stageCode(row);
    cellIds[index]=row.id;
    records[index]=row;
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
      id:'published',label:'Chicago',left_block:left*cellSize,top_block:top*cellSize,
      width_cells:width,height_cells:height,width_chunks:width*cellSize/16,height_chunks:height*cellSize/16,
      cell_size_blocks:cellSize,cell_label:'generation cell',tiles:rows.length
    },
    palette:['Queued','Source ready','Terrain & buildings ready','Building','Needs review'],
    cells,cell_ids:cellIds,records,counts,failed_tiles:counts.failed,
    updated_utc:data.updated_utc,overlays:{}
  };
}

function renderSnapshot(){
  if(!snapshot)return;
  const rollup=snapshot.rollup||{},local=snapshot.local||{};
  if(snapshot.source==='public')$('area').value='published';
  $('phase').textContent=snapshot.regions?.[0]?.label||'Chicago build';
  $('connection').textContent=snapshot.source==='local'?'Local build connected':'Published snapshot';
  document.body.dataset.feed=snapshot.source;
  $('updated').textContent=snapshot.updated_utc?'Snapshot · '+new Date(snapshot.updated_utc).toLocaleString(undefined,{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}):'Date not recorded';
  $('rollup').textContent=stateLabel(rollup.state);
  $('geometry').textContent=countLabel(rollup.generated_tiles);
  $('sources').textContent=countLabel(local.source_tiles_complete);
  $('appearance').textContent=countLabel(local.stages?.appearance?.complete);
  $('recovery').textContent=snapshot.source==='local'
    ?'Source files are reused between tiles. Interrupted work resumes from its last checkpoint.'
    :'';
  const stageNames={sources:'Source data',geometry:'Terrain & buildings',appearance:'Surface detail',game_verify:'Minecraft check'};
  $('log').textContent=Object.entries(local.stages||{}).map(([stage,counts])=>(stageNames[stage]||stateLabel(stage))+': '+(Object.entries(counts).map(([state,count])=>stateLabel(state)+' '+number(count)).join(', ')||'No jobs')).join('\n')||'No stage counts recorded';
  $('footer-state').textContent='Last read '+new Date().toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});
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
  const margin=56,cell=Math.max(.05,Math.min((cssWidth-margin*2)/width,(cssHeight-margin*2)/height))*zoom;
  const mapWidth=cell*width,mapHeight=cell*height;
  const centerColumn=zoom>1&&selectedIndex!==null?selectedIndex%width+.5:width/2;
  const centerRow=zoom>1&&selectedIndex!==null?Math.floor(selectedIndex/width)+.5:height/2;
  const left=cssWidth/2-centerColumn*cell,top=cssHeight/2-centerRow*cell;
  mapMetrics={left,top,cell,width,height,cellSize:atlas.area.cell_size_blocks,leftBlock:atlas.area.left_block,topBlock:atlas.area.top_block};
  ctx.fillStyle=color('outside');
  ctx.fillRect(left,top,mapWidth,mapHeight);
  const colors=palette();
  for(let row=0;row<height;row++){
    for(let column=0;column<width;column++){
      const code=atlas.cells[row*width+column];
      if(code<0)continue;
      if(left+(column+1)*cell<0||left+column*cell>cssWidth||top+(row+1)*cell<0||top+row*cell>cssHeight)continue;
      ctx.fillStyle=colors[code]||colors[0];
      const gap=cell>=2?.5:0;
      ctx.fillRect(left+column*cell,top+row*cell,cell-gap,cell-gap);
    }
  }
  if(cell>=5){
    ctx.strokeStyle=color('line');
    ctx.globalAlpha=.3;
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
    ctx.globalAlpha=1;
  }
  if(atlas.area.id==='queue'){
    ctx.fillStyle=color('amber');
    ctx.strokeStyle=color('amber');
    ctx.lineWidth=1;
    for(const tile of atlas.overlays?.installed?.tiles||[]){
      const a=canvasPoint(tile.x,tile.z),b=canvasPoint(tile.x+tile.size,tile.z+tile.size);
      ctx.globalAlpha=.2;
      ctx.fillRect(a.x,a.y,b.x-a.x,b.y-a.y);
      ctx.globalAlpha=1;
      ctx.strokeRect(a.x+.5,a.y+.5,b.x-a.x-1,b.y-a.y-1);
    }
    const extent=atlas.overlays?.installed;
    if(extent?.width_blocks){
      const a=canvasPoint(extent.left_block,extent.top_block),b=canvasPoint(extent.right_block,extent.bottom_block);
      ctx.strokeStyle=color('amber');
      ctx.lineWidth=2.5;
      ctx.strokeRect(a.x,a.y,b.x-a.x,b.y-a.y);
    }
  }
  const anchor=atlas.overlays?.anchor,point=anchor?canvasPoint(anchor.x,anchor.z):null;
  if(point&&point.x>=left&&point.x<=left+mapWidth&&point.y>=top&&point.y<=top+mapHeight){
    ctx.strokeStyle=color('ink');
    ctx.fillStyle=color('surface');
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
    ctx.font='600 11px "Libre Franklin", sans-serif';
    ctx.fillStyle=color('ink');
    ctx.fillText(anchor.label,point.x+11,point.y-7);
  }
  for(const [index,strong] of [[hoverIndex,false],[selectedIndex,true]]){
    if(index===null||atlas.cells[index]<0)continue;
    const x=left+(index%width)*cell,y=top+Math.floor(index/width)*cell;
    ctx.strokeStyle=color(strong?'ink':'surface');ctx.lineWidth=strong?2:1;
    ctx.strokeRect(x-1,y-1,Math.max(cell+2,4),Math.max(cell+2,4));
  }
  $('map-scale').textContent=distanceLabel(68/cell*atlas.area.cell_size_blocks);
}

function renderLegend(isPublished,counts,live){
  $('legend-outside-label').textContent=isPublished?'Unlisted':live?'Not imported':'Outside plan';
  $('legend-queued-label').textContent='Queued';
  $('legend-source-label').textContent='Source ready';
  $('legend-finished-label').textContent=live?'In Minecraft':'Terrain & buildings';
  $('legend-queued').hidden=live;
  $('legend-source').hidden=live;
  $('legend-finished').hidden=false;
  $('legend-installed').hidden=live||isPublished;
  $('legend-running').hidden=live||!counts.active;
  $('legend-failed').hidden=live||!counts.failed;
}

function renderAtlas(){
  if(!atlas){
    $('grid-size').textContent='Unavailable';
    $('grid-count').textContent='The selected source has not published an atlas.';
    $('chunk-count').textContent='Unavailable';
    $('border').textContent='Unavailable';
    $('leases').textContent='Unavailable';
    setMapMessage('No Chicago atlas is available.');
    $('mapwrap').setAttribute('aria-busy','false');
    $('map').getContext('2d').clearRect(0,0,$('map').width,$('map').height);
    $('map-summary').textContent='No cells are available in this view.';
    $('map-title').textContent='No cells recorded';
    $('map-unit').textContent='Generation cells';
    $('map-caption').textContent='This snapshot has no recorded generation cells.';
    $('map-updated').textContent='';
    $('map-scale').textContent='';
    mapMetrics=null;
    $('map').setAttribute('aria-label','No generation cells recorded.');
    for(const id of ['legend-queued','legend-source','legend-finished','legend-installed','legend-running','legend-failed'])$(id).hidden=true;
    updateZoomButtons();
    clearSelection();
    return;
  }
  const {area,counts}=atlas;
  const widthBlocks=area.width_chunks*16,heightBlocks=area.height_chunks*16;
  const isPublished=atlas.source==='published',live=area.id==='live',queue=area.id==='queue';
  const mapped=countCells(counts),built=Number(counts.green||0);
  if(isPublished){
    $('grid-size').textContent=number(area.tiles)+' cells';
    $('grid-count').textContent=distanceLabel(widthBlocks)+' × '+distanceLabel(heightBlocks)+' · '+number(area.cell_size_blocks)+' m per cell.';
    $('chunk-count').textContent=number(mapped)+' published generation cells';
    $('border').textContent='Published grid';
    $('leases').textContent='Snapshot';
    $('map-unit').textContent=number(area.cell_size_blocks)+' m generation cells';
    $('map-title').textContent='Published progress';
    $('map-caption').textContent='Each square covers '+number(area.cell_size_blocks)+' × '+number(area.cell_size_blocks)+' metres. Green means terrain and buildings have been generated.';
    setMapMessage(number(area.tiles)+' cells recorded · '+number(built)+' generated');
  }else{
    $('grid-size').textContent=distanceLabel(widthBlocks)+' × '+distanceLabel(heightBlocks);
    $('grid-count').textContent=queue
      ?number(area.tiles)+' tiles in the Chicago plan · one square is one '+area.cell_label+'.'
      :live
        ?number(counts.green)+' chunks imported into Minecraft · one square is one '+area.cell_label+'.'
        :number(area.tiles)+' original generation tiles · one square is one '+area.cell_label+'.';
    $('chunk-count').textContent=number(mapped)+' '+area.cell_label+(mapped===1?'':'s');
    $('map-unit').textContent=number(area.cell_size_blocks)+' m '+(live?'Minecraft chunks':'generation cells');
    $('map-title').textContent=({live:'In Minecraft',queue:'Build queue',installed:'Original footprint'})[area.id]||area.label;
    const workers=atlas.active_worker_owners||0,deferred=atlas.deferred_tiles||0,oldLeases=atlas.superseded_leases||0;
    $('leases').textContent=workers
      ?number(workers)+' workers'+(deferred?' · '+number(deferred)+' deferred':'')+(oldLeases?' · '+number(oldLeases)+' expiring':'')
      :deferred?number(deferred)+' waiting':'None active';
    const border=atlas.border;
    $('border').textContent=border?(border.synced?'Matches plan':'Target '+distanceLabel(border.expected.size)):'Not checked';
    $('map-caption').textContent=live
      ?'Green chunks have been imported into the world. The Water Tower marks the coordinate origin.'
      :queue?'Build stages across the Chicago plan. Select a square for its status.'
        :'A record of the first installed area.';
    const minecraft=atlas.minecraft_import;
    setMapMessage(live&&minecraft?.needs_minecraft_relaunch
      ?number(counts.green)+' chunks imported · Minecraft import paused'
      :queue
        ?number(area.tiles)+' planned tiles · '+number(built)+' generated'
        :live?number(counts.green)+' chunks imported into Minecraft.'
          :number(area.tiles)+' original tiles across '+distanceLabel(atlas.overlays?.installed?.width_blocks||0)+' × '+distanceLabel(atlas.overlays?.installed?.height_blocks||0)+'.');
    if(live&&minecraft?.needs_minecraft_relaunch){
      $('connection').textContent='Build running · Import paused';
      $('warning').hidden=false;
      $('warning').textContent='Reopen Earthcraft in Minecraft to resume imports.';
    }
  }
  $('map-updated').textContent=atlas.updated_utc?'Snapshot '+new Date(atlas.updated_utc).toLocaleDateString(undefined,{day:'numeric',month:'short'}):'';
  $('map-summary').textContent=number(counts.white)+' queued, '+number(counts.gray)+' source-ready, '+number(counts.green)+(live?' imported':' generated')+' cells, '+number(counts.active)+' in progress, '+number(counts.failed)+' needing review.';
  $('map').setAttribute('aria-label',`${$('map-title').textContent}. ${$('map-summary').textContent} Use arrow keys to inspect cells.`);
  $('mapwrap').setAttribute('aria-busy','false');
  renderSelection();
  renderLegend(isPublished,counts,live);
  updateZoomButtons();
  resizeCanvas();
}

async function readLocal(){
  const [earth,chunks]=await Promise.all([
    fetch('./api/earth',{cache:'no-store',signal:AbortSignal.timeout(10000)}),
    fetch('./api/chunks?area='+encodeURIComponent($('area').value),{cache:'no-store',signal:AbortSignal.timeout(10000)})
  ]);
  if(!earth.ok||!chunks.ok)throw Error('local feed unavailable');
  const [earthData,chunkData]=await Promise.all([earth.json(),chunks.json()]);
  snapshot={...earthData,source:'local'};
  atlas=chunkData;
}

async function readPublished(){
  const response=await fetch('./progress/earth.json',{cache:'no-cache',signal:AbortSignal.timeout(10000)});
  if(!response.ok)throw Error('published snapshot unavailable');
  const data={...await response.json(),source:'public'},nextAtlas=publishedAtlas(data);
  snapshot=data;
  atlas=nextAtlas;
}

async function readSnapshot(){
  if($('area').value==='published')return readPublished();
  try{await readLocal();}catch(localError){await readPublished();}
}

async function refresh(){
  if(busy){refreshQueued=true;return;}
  busy=true;
  $('refresh').disabled=true;
  $('refresh').textContent='Reading…';
  const previousView=atlasSignature();
  try{
    await readSnapshot();
    if(previousView!==atlasSignature()){
      selectedIndex=null;hoverIndex=null;zoom=1;updateZoomButtons();
    }
    renderSnapshot();
    renderAtlas();
  }catch(error){
    document.body.dataset.feed='error';
    $('connection').textContent='Snapshot unavailable';
    $('warning').hidden=false;
    $('warning').textContent=atlas?'The latest snapshot could not be read. The last loaded map is still shown. Use Refresh to try again.':'The progress snapshot could not be read. Check your connection and use Refresh to try again.';
    $('mapwrap').setAttribute('aria-busy','false');
    if(!snapshot){
      $('phase').textContent='Record unavailable';
      $('grid-size').textContent='Unavailable';
      $('map-title').textContent='Progress unavailable';
      $('map-caption').textContent='The atlas will appear when a snapshot can be read.';
      $('map-summary').textContent='Progress unavailable. Use Refresh to try again.';
      $('map').setAttribute('aria-label','Progress unavailable. Use Refresh to try again.');
      for(const id of ['geometry','sources','appearance','chunk-count','border','leases','rollup'])$(id).textContent='Not recorded';
      for(const id of ['legend-queued','legend-source','legend-finished','legend-installed','legend-running','legend-failed'])$(id).hidden=true;
      $('selection-title').textContent='No cell selected';
      $('cell').textContent='Read a snapshot to begin inspecting cells.';
      $('log').textContent='No build record loaded';
      $('footer-state').textContent='No snapshot loaded';
      updateZoomButtons();
    }
    if(!atlas)setMapMessage('Progress unavailable. Use Refresh to try again.');
  }finally{
    busy=false;
    $('refresh').disabled=false;
    $('refresh').textContent='Refresh ↻';
    if(refreshQueued){refreshQueued=false;refresh();}
  }
}

function atlasSignature(){
  if(!atlas)return '';
  const a=atlas.area;
  return [atlas.source,a.id,a.left_block,a.top_block,a.width_cells,a.height_cells,a.cell_size_blocks].join('|');
}

function indexAtPointer(event){
  if(!atlas||!mapMetrics)return null;
  const rect=$('map').getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
  const column=Math.floor((x-mapMetrics.left)/mapMetrics.cell),row=Math.floor((y-mapMetrics.top)/mapMetrics.cell);
  if(column<0||row<0||column>=mapMetrics.width||row>=mapMetrics.height)return null;
  return row*mapMetrics.width+column;
}

function renderSelection(){
  const details=$('selection-details');details.replaceChildren();
  details.hidden=true;$('clear-selection').hidden=selectedIndex===null;
  if(selectedIndex===null||!atlas){
    $('selection-title').textContent='Choose a square';
    $('cell').textContent='Click or tap the atlas. You can also use the arrow keys.';
    return;
  }
  const width=atlas.area.width_cells,column=selectedIndex%width,row=Math.floor(selectedIndex/width);
  const code=atlas.cells[selectedIndex],record=atlas.records?.[selectedIndex];
  const blockX=atlas.area.left_block+column*atlas.area.cell_size_blocks;
  const blockZ=atlas.area.top_block+row*atlas.area.cell_size_blocks;
  const unit=atlas.area.cell_size_blocks===16?'Chunk':'Cell';
  const id=atlas.cell_ids?.[selectedIndex]||Math.floor(blockX/atlas.area.cell_size_blocks)+'_'+Math.floor(blockZ/atlas.area.cell_size_blocks);
  $('selection-title').textContent=unit+' '+id.replace('_',', ');
  $('cell').textContent=code<0?'No build record for this square.':atlas.area.id==='live'?'Imported into Minecraft.':stateLabel(atlas.palette[code])+'.';
  const rows=[['Size',number(atlas.area.cell_size_blocks)+' × '+number(atlas.area.cell_size_blocks)+' m']];
  if(record){
    if(Number.isFinite(record.latitude)&&Number.isFinite(record.longitude))rows.push(['Location',record.latitude.toFixed(4)+'°, '+record.longitude.toFixed(4)+'°']);
    for(const [label,field] of [['Source data','source_state'],['Terrain & buildings','geometry_state'],['Surface detail','appearance_state'],['Minecraft check','game_verify_state']])rows.push([label,stateLabel(record[field])]);
  }else if(code>=0)rows.push(['World X / Z',number(blockX)+' / '+number(blockZ)]);
  for(const [label,value] of rows){
    const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');
    dt.textContent=label;dd.textContent=value;row.append(dt,dd);details.append(row);
  }
  details.hidden=false;
}

function clearSelection(){selectedIndex=null;hoverIndex=null;renderSelection();if(atlas)drawAtlas();}
function selectIndex(index){selectedIndex=index;renderSelection();drawAtlas();}
function updateZoomButtons(){$('zoom-in').disabled=!atlas||zoom>=4;$('zoom-out').disabled=!atlas||zoom<=1;$('zoom-reset').disabled=!atlas;}
function changeZoom(next){zoom=Math.max(1,Math.min(4,next));updateZoomButtons();if(atlas)drawAtlas();}

$('map').addEventListener('pointermove',event=>{
  const index=indexAtPointer(event);
  if(index!==hoverIndex){hoverIndex=index;if(atlas)drawAtlas();}
});
$('map').addEventListener('pointerleave',()=>{hoverIndex=null;if(atlas)drawAtlas();});
$('map').addEventListener('click',event=>{const index=indexAtPointer(event);if(index!==null)selectIndex(index);});
$('map').addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();clearSelection();return;}
  if(!atlas||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
  event.preventDefault();
  const width=atlas.area.width_cells,height=atlas.area.height_cells;
  if(selectedIndex===null){const first=atlas.cells.findIndex(code=>code>=0);if(first>=0)selectIndex(first);return;}
  const column=selectedIndex%width,row=Math.floor(selectedIndex/width);
  const nextColumn=Math.max(0,Math.min(width-1,column+(event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0)));
  const nextRow=Math.max(0,Math.min(height-1,row+(event.key==='ArrowDown'?1:event.key==='ArrowUp'?-1:0)));
  selectIndex(nextRow*width+nextColumn);
});
$('zoom-in').onclick=()=>changeZoom(zoom+1);
$('zoom-out').onclick=()=>changeZoom(zoom-1);
$('zoom-reset').onclick=()=>changeZoom(1);
$('clear-selection').onclick=clearSelection;
$('refresh').onclick=refresh;
$('area').onchange=refresh;
$('auto').onchange=()=>{if($('auto').checked)refresh();};
window.addEventListener('resize',resizeCanvas);
document.fonts.ready.then(()=>{if(atlas)drawAtlas();});
const localHost=['localhost','127.0.0.1','[::1]'].includes(location.hostname);
if(!localHost||new URLSearchParams(location.search).get('view')==='published')$('area').value='published';
if(!localHost)for(const option of $('area').options)if(option.value!=='published')option.disabled=true;
refresh();
let ticks=0;
setInterval(()=>{ticks+=1;if($('auto').checked&&!document.hidden&&($('area').value!=='published'||ticks%6===0))refresh();},10000);
