const $=id=>document.getElementById(id);
const EARTH_IMAGE='https://cdn.jsdelivr.net/npm/three-globe@2.45.0/example/img/earth-blue-marble.jpg';
const TERRAIN_IMAGE='https://cdn.jsdelivr.net/npm/three-globe@2.45.0/example/img/earth-topology.png';
const number=value=>Number(value||0).toLocaleString();
const stateLabel=value=>String(value||'unknown').replaceAll('-',' ');
const motionQuery=window.matchMedia?.('(prefers-reduced-motion: reduce)');
let data=null,busy=false,globe=null,hoveredPoint=null,reducedMotion=motionQuery?.matches??false;

function setMapMessage(message){$('mapnote').textContent=message}

function formatCoordinate(latitude,longitude){
  return `${Math.round(Number(latitude)*10)/10}°, ${Math.round(Number(longitude)*10)/10}°`;
}

function updateControls(){
  if(!globe)return;
  const controls=globe.controls();
  controls.autoRotate=!reducedMotion&&!document.hidden;
  controls.autoRotateSpeed=.28;
  controls.enablePan=false;
  controls.minDistance=110;
  controls.maxDistance=380;
}

function resizeGlobe(){
  if(!globe)return;
  const map=$('map');globe.width(map.clientWidth).height(map.clientHeight);
}

function showCoordinate(event){
  if(!globe||hoveredPoint)return;
  const rect=$('map').getBoundingClientRect();
  const point=globe.toGlobeCoords(event.clientX-rect.left,event.clientY-rect.top);
  if(!point){$('cell').textContent='Move over the globe to inspect a coordinate.';return}
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

function initGlobe(){
  const map=$('map');
  if(typeof Globe!=='function'){
    map.setAttribute('aria-label','Earth globe unavailable until the globe library loads');
    setMapMessage('Globe library unavailable.');
    return;
  }
  globe=new Globe(map,{rendererConfig:{antialias:true,alpha:true},waitForGlobeReady:false,animateIn:false})
    .backgroundColor('rgba(0,0,0,0)')
    .globeImageUrl(EARTH_IMAGE)
    .bumpImageUrl(TERRAIN_IMAGE)
    .showGraticules(true)
    .showAtmosphere(true)
    .atmosphereColor('rgb(97, 145, 150)')
    .atmosphereAltitude(.08)
    .pointLat('latitude')
    .pointLng('longitude')
    .pointColor(()=> 'rgb(45, 96, 65)')
    .pointAltitude(.045)
    .pointRadius(.42)
    .pointResolution(12)
    .pointsMerge(false)
    .pointLabel(region=>`${region.label||region.id}<br>${formatCoordinate(region.latitude,region.longitude)}`)
    .onPointHover(point=>{
      hoveredPoint=point;
      map.style.cursor=point?'pointer':'grab';
      if(point)$('cell').textContent=`${point.label||point.id} · ${formatCoordinate(point.latitude,point.longitude)}.`;
    })
    .onGlobeClick(({lat,lng})=>{$('cell').textContent=`Approximate coordinate ${formatCoordinate(lat,lng)}.`})
    .showPointerCursor(true);
  updateControls();resizeGlobe();
}

function render(){
  const rollup=data.rollup||{},local=data.local||{},performance=data.performance||{};
  $('phase').textContent=`${stateLabel(rollup.state)} · ${data.scope?.label||'Earth'}`;
  $('connection').textContent=data.source==='local'?'Local data':'Public snapshot · GitHub Pages';
  $('updated').textContent=data.updated_utc?`Updated ${new Date(data.updated_utc).toLocaleString()}`:'';
  $('rollup').textContent=stateLabel(rollup.state);
  $('geometry').textContent=number(rollup.generated_tiles);
  $('sources').textContent=local.source_tiles_total?`${number(local.source_tiles_complete)} / ${number(local.source_tiles_total)}`:number(local.source_tiles_complete);
  $('coverage').textContent=data.scope?.coverage_percent==null?'Not computed':`${data.scope.coverage_percent}%`;
  $('surface').textContent=stateLabel((data.workstreams||[]).find(item=>item.id==='terrain')?.state);
  $('local').textContent=stateLabel(local.state);
  $('recovery').textContent=performance.parallel_tile_workers+'; '+performance.source_cache_coordination+'.';
  $('log').textContent=Object.entries(local.stages||{}).map(([stage,counts])=>`${stage}: ${Object.entries(counts).map(([state,count])=>`${state} ${count}`).join(', ')||'no jobs'}`).join('\n')||'No local run connected.';
  $('footer-state').textContent=`Observed ${new Date(data.updated_utc||Date.now()).toLocaleTimeString()}`;
  const warning=data.claims?.global_complete?'Global completion is not reported.':'Blank regions have not been measured yet.';
  $('warning').hidden=false;$('warning').textContent=warning;
  updateWorkstreamList();
  if(globe){
    const regions=data.regions||[];
    globe.pointsData(regions);
    if(regions[0]&&!globe.__positioned){globe.pointOfView({lat:regions[0].latitude,lng:regions[0].longitude,altitude:4.5},0);globe.__positioned=true;}
    setMapMessage(regions.length?`${number(regions.length)} region${regions.length===1?'':'s'} indexed · interactive globe`:'No measured regions yet');
    updateControls();resizeGlobe();
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
$('layer').onchange=()=>{updateWorkstreamList();setMapMessage($('layer').value==='workstreams'?'Workstream state':'Known regions');};
$('auto').onchange=()=>{if($('auto').checked)refresh()};
$('map').addEventListener('pointermove',showCoordinate);
window.addEventListener('resize',resizeGlobe);
document.addEventListener('visibilitychange',()=>{if(document.hidden)globe?.pauseAnimation();else{globe?.resumeAnimation();updateControls()}});
motionQuery?.addEventListener?.('change',event=>{reducedMotion=event.matches;updateControls()});
refresh();
setInterval(()=>{if($('auto').checked&&!document.hidden)refresh()},10000);
