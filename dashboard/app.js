const $=id=>document.getElementById(id);
const EARTH_IMAGE='https://cdn.jsdelivr.net/npm/three-globe@2.45.0/example/img/earth-blue-marble.jpg';
const TERRAIN_IMAGE='https://cdn.jsdelivr.net/npm/three-globe@2.45.0/example/img/earth-topology.png';
const number=value=>Number(value||0).toLocaleString();
const stateLabel=value=>String(value||'unknown').replaceAll('_',' ').replaceAll('-',' ');
const motionQuery=window.matchMedia?.('(prefers-reduced-motion: reduce)');
const CELL_COLORS={
  queued:'rgba(255, 211, 116, .78)',
  running:'rgba(212, 139, 55, .95)',
  sourced:'rgba(181, 190, 181, .9)',
  generated:'rgba(100, 159, 113, .95)',
  styled:'rgba(53, 113, 76, .98)',
  verified:'rgba(25, 78, 47, 1)',
  failed:'rgba(190, 75, 58, .98)'
};
let data=null,busy=false,globe=null,hoveredItem=null,reducedMotion=motionQuery?.matches??false;

function setMapMessage(message){$('mapnote').textContent=message}

function formatCoordinate(latitude,longitude){
  return `${Math.round(Number(latitude)*10)/10}°, ${Math.round(Number(longitude)*10)/10}°`;
}

function cellColor(cell){return CELL_COLORS[cell?.state]||CELL_COLORS.queued}

function cellLabel(cell){
  const generated=number(cell?.chunks_generated),total=number(cell?.chunks_total);
  return `${cell?.region_id||'Earth'} · ${cell?.tile_id||cell?.id}<br>${stateLabel(cell?.state)} · ${generated} / ${total} chunks`;
}

function updateControls(){
  if(!globe)return;
  const controls=globe.controls();
  controls.autoRotate=!reducedMotion&&!document.hidden;
  controls.autoRotateSpeed=.22;
  controls.enablePan=false;
  controls.minDistance=110;
  controls.maxDistance=380;
}

function resizeGlobe(){
  if(!globe)return;
  const frame=$('map').parentElement;
  globe.width(frame.clientWidth).height(frame.clientHeight);
}

function showCoordinate(event){
  if(!globe||hoveredItem)return;
  const rect=$('map').getBoundingClientRect();
  const point=globe.toGlobeCoords(event.clientX-rect.left,event.clientY-rect.top);
  if(!point){$('cell').textContent='Move over the globe to inspect a cell.';return}
  $('cell').textContent=`Approximate coordinate ${formatCoordinate(point.lat,point.lng)}.`;
}

function updateWorkstreamList(){
  const list=$('workstream-list'),show=$('layer').value==='workstreams';
  list.hidden=!show;list.replaceChildren();if(!show)return;
  for(const workstream of data?.workstreams||[]){
    const row=document.createElement('div'),mark=document.createElement('i'),text=document.createElement('span');
    mark.className=workstream.state==='implemented'?'implemented':'planned';
    text.textContent=`${workstream.label} · ${stateLabel(workstream.state)}`;
    row.append(mark,text);list.append(row);
  }
}

function updateGlobeLayer(){
  if(!globe||!data)return;
  const view=$('layer').value;
  const regions=data.regions||[],cells=data.cells||[];
  const cellView=view==='cells';
  globe.tilesData(cellView?cells:[]);
  globe.pointsData(cellView?cells:regions);
  globe.labelsData(view==='regions'?regions:[]);
  globe.pointRadius(cellView?.65:.6);
  globe.pointColor(cellView?cellColor:()=> 'rgb(45, 96, 65)');
  updateWorkstreamList();
  const generated=cells.reduce((sum,cell)=>sum+Number(cell.chunks_generated||0),0);
  if(view==='workstreams')setMapMessage('Workstream state · select Cells to inspect the generation grid');
  else if(view==='regions')setMapMessage(`${number(regions.length)} region${regions.length===1?'':'s'} indexed · interactive globe`);
  else setMapMessage(cells.length?`${number(cells.length)} cells · ${number(generated)} chunks generated · drag to inspect`:'No materialized cells yet');
  updateControls();resizeGlobe();
}

function initGlobe(){
  const map=$('map');
  if(typeof Globe!=='function'){
    map.setAttribute('aria-label','Earth globe unavailable until the globe library loads');
    setMapMessage('Globe library unavailable.');
    return;
  }
  try{globe=new Globe(map,{rendererConfig:{antialias:true,alpha:true},waitForGlobeReady:false,animateIn:false})
    .backgroundColor('rgba(0,0,0,0)')
    .globeImageUrl(EARTH_IMAGE)
    .bumpImageUrl(TERRAIN_IMAGE)
    .showGraticules(true)
    .showAtmosphere(true)
    .atmosphereColor('rgb(97, 145, 150)')
    .atmosphereAltitude(.08)
    .pointLat('latitude')
    .pointLng('longitude')
    .pointColor(cellColor)
    .pointAltitude(.022)
    .pointRadius(.65)
    .pointResolution(8)
    .pointsMerge(true)
    .pointLabel(cellLabel)
    .tileLat('latitude')
    .tileLng('longitude')
    .tileWidth('width_deg')
    .tileHeight('height_deg')
    .tileUseGlobeProjection(true)
    .tileAltitude(.012)
    .tileMaterial(cell=>new THREE.MeshLambertMaterial({color:cellColor(cell),transparent:true,opacity:.78,side:THREE.DoubleSide}))
    .tileLabel(cellLabel)
    .tileCurvatureResolution(1)
    .tilesTransitionDuration(0)
    .labelLat('latitude')
    .labelLng('longitude')
    .labelText(region=>region.label||region.id)
    .labelColor(()=> 'rgb(45, 96, 65)')
    .labelSize(.55)
    .labelAltitude(.075)
    .labelDotRadius(.2)
    .labelIncludeDot(true)
    .onPointHover(item=>{
      hoveredItem=item;
      map.style.cursor=item?'pointer':'grab';
      if(item)$('cell').textContent=cellLabel(item).replace('<br>',' · ');
    })
    .onTileHover(item=>{
      hoveredItem=item;
      map.style.cursor=item?'pointer':'grab';
      if(item)$('cell').textContent=cellLabel(item).replace('<br>',' · ');
    })
    .onPointClick(item=>globe.pointOfView({lat:item.latitude,lng:item.longitude,altitude:.42},700))
    .onTileClick(item=>globe.pointOfView({lat:item.latitude,lng:item.longitude,altitude:.42},700))
    .onGlobeClick(({lat,lng})=>{$('cell').textContent=`Approximate coordinate ${formatCoordinate(lat,lng)}.`})
    .showPointerCursor(true);
  }catch(error){
    console.error('Earthcraft cell globe failed to initialize',error);
    globe=null;
    setMapMessage('The Earth image loaded, but the cell layer is unavailable.');
  }
  if(globe){updateControls();resizeGlobe();}
}

function render(){
  const rollup=data.rollup||{},local=data.local||{},performance=data.performance||{},grid=data.cell_grid||{};
  $('phase').textContent=`${stateLabel(rollup.state)} · ${data.scope?.label||'Earth'}`;
  $('connection').textContent=data.source==='local'?'Local data':'Public snapshot · cell view';
  $('updated').textContent=data.updated_utc?`Updated ${new Date(data.updated_utc).toLocaleString()}`:'';
  $('rollup').textContent=stateLabel(rollup.state);
  $('geometry').textContent=number(rollup.generated_tiles);
  $('sources').textContent=local.source_tiles_total?`${number(local.source_tiles_complete)} / ${number(local.source_tiles_total)}`:number(local.source_tiles_complete);
  $('cells').textContent=number(grid.materialized_cells||rollup.materialized_cells);
  $('chunks').textContent=number(rollup.materialized_chunks);
  $('recovery').textContent=performance.parallel_tile_workers+'; '+performance.source_cache_coordination+'.';
  $('log').textContent=Object.entries(local.stages||{}).map(([stage,counts])=>`${stage}: ${Object.entries(counts).map(([state,count])=>`${state} ${count}`).join(', ')||'no jobs'}`).join('\n')||'No local run connected.';
  $('footer-state').textContent=`Observed ${new Date(data.updated_utc||Date.now()).toLocaleTimeString()}`;
  $('warning').hidden=false;
  $('warning').textContent=data.claims?.global_complete?'Global completion is not reported.':'Unmeasured cells are left out of the public grid.';
  if(globe){
    updateGlobeLayer();
    const regions=data.regions||[];
    if(regions[0]&&!globe.__positioned){globe.pointOfView({lat:regions[0].latitude,lng:regions[0].longitude,altitude:2.15},0);globe.__positioned=true;}
  }
}

async function readSnapshot(){
  try{
    const response=await fetch('./api/earth',{cache:'no-store'});
    if(!response.ok)throw Error('local feed unavailable');
    data={...await response.json(),source:'local'};
  }catch(localError){
    const response=await fetch('./progress/earth.json',{cache:'no-store'});
    if(!response.ok)throw Error('public snapshot unavailable');
    data={...await response.json(),source:'public'};
  }
}

async function refresh(){
  if(busy)return;busy=true;$('refresh').disabled=true;
  try{await readSnapshot();render()}
  catch(error){$('connection').textContent='Progress feed disconnected';$('warning').hidden=false;$('warning').textContent='The latest public snapshot could not be read.'}
  finally{busy=false;$('refresh').disabled=false}
}

initGlobe();
$('refresh').onclick=refresh;
$('layer').onchange=updateGlobeLayer;
$('auto').onchange=()=>{if($('auto').checked)refresh()};
$('map').addEventListener('pointermove',showCoordinate);
$('map').addEventListener('pointerleave',()=>{hoveredItem=null;$('cell').textContent='Move over the globe to inspect a cell.'});
window.addEventListener('resize',resizeGlobe);
document.addEventListener('visibilitychange',()=>{if(document.hidden)globe?.pauseAnimation();else{globe?.resumeAnimation();updateControls()}});
motionQuery?.addEventListener?.('change',event=>{reducedMotion=event.matches;updateControls()});
refresh();
setInterval(()=>{if($('auto').checked&&!document.hidden)refresh()},10000);
