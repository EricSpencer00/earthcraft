import {installCellBrowser} from './cell-browser.mjs?v=simple-1';
import {parseLocation} from './location-search.mjs?v=simple-1';
import * as maplibre from './assets/vendor/maplibre/maplibre-gl.mjs';

const $=id=>document.getElementById(id);
new ResizeObserver(entries=>{document.documentElement.style.setProperty('--tools-height',entries[0].target.getBoundingClientRect().height+'px');}).observe(document.querySelector('.map-tools'));

const number=value=>Number(value||0).toLocaleString();
const stateLabel=value=>({
  'regional-foundation':'Regional build',pending:'Not yet',running:'Building',leased:'Building',
  complete:'Done',generated:'Terrain & buildings built',styled:'Surface detail added',
  verified:'Checked in Minecraft',failed:'Needs review',unknown:'Not recorded'
}[value]||String(value||'Not recorded').replaceAll('_',' ').replaceAll('-',' '));
const color=name=>getComputedStyle(document.documentElement).getPropertyValue('--'+name).trim();
const palette=()=>['surface','gray','green','amber','rust'].map(color);
const countLabel=value=>typeof value==='number'&&Number.isFinite(value)?number(value):'Not recorded';
let cellBrowser=null;
// Per-cell detail level (256 m cells = 16x16 chunks).
const DETAIL_URL='https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/main/world/index/detail-levels.json';
const DETAIL_LEVELS=['none','low','high','hand'];
let detail=null;
function detailLevel(index){
  const id=atlas?.cell_ids?.[index];
  if(!detail||!id)return null;
  return detail.cells?.[id]||null;
}
function detailColor(level){return {none:color('gray'),low:color('amber'),high:color('green'),hand:color('rust')}[level]||color('surface');}
function renderDetailCount(){
  const box=$('detail-count');
  if(!detail){box.hidden=true;return;}
  const sampled=Number(detail.sampled)>0&&Number(detail.sampled)<1;
  const parts=[document.createTextNode('Detail:')];
  for(const level of DETAIL_LEVELS){
    const value=sampled?(detail.estimated_totals?.[level]?.estimate??0):(detail.counts?.[level]??0);
    const item=document.createElement('span'),swatch=document.createElement('i');
    swatch.className='lvl-'+level;swatch.setAttribute('aria-hidden','true');
    item.append(swatch,level+' '+(sampled&&value?'~':'')+number(value));
    parts.push(item);
  }
  box.replaceChildren(...parts);
  box.title=sampled?'Estimated from a '+Math.round(detail.sampled*100)+'% sample of tiles. Cells not in the sample are shown plain.':'Counted over every tile.';
  box.hidden=false;
}
async function loadDetail(){
  try{
    const response=await fetch(DETAIL_URL,{cache:'no-cache',signal:AbortSignal.timeout(10000)});
    if(!response.ok)return;
    const data=await response.json();
    if(data?.schema!=='earthcraft.detail-levels.v1')return;
    detail=data;renderDetailCount();
    if(atlas){scheduleDraw();if(selectedIndex!==null)renderSelection();}
  }catch{/* detail levels are optional */}
}
let snapshot=null,atlas=null,busy=false,refreshQueued=false,mapMetrics=null,zoom=1,selectedIndex=null,hoverIndex=null;
let geoMap=null,geoCells=[],geoBounds=null,geoSignature='',mapLayer='build',drawPending=false;
// These geographic anchors match configs/cities.json; the Minecraft frame stays unchanged.
const places={chicago:{name:'Chicago',coordinates:[-87.62443,41.8972]},elmhurst:{name:'Elmhurst',coordinates:[-87.9403418,41.8994745]}};

function setPanel(open,section=document.body.dataset.section||'world'){
  const changed=section!==document.body.dataset.section;
  document.body.dataset.panel=open?'open':'closed';
  document.body.dataset.section=section;
  $('panel-toggle').setAttribute('aria-expanded',String(open));
  for(const id of ['world','cell','about','browse']){
    $(id+'-panel').hidden=id!==section;
    if(id==='world'||id==='cell')$('tab-'+id).setAttribute('aria-pressed',String(id===section));
  }
  if(changed)document.querySelector('.panel-content').scrollTop=0;
}
function scheduleDraw(){
  if(drawPending)return;
  drawPending=true;
  requestAnimationFrame(()=>{drawPending=false;if(atlas)drawAtlas();});
}
function geographic(){return document.body.dataset.map==='geographic'&&geoMap&&geoCells.length>0;}
function mercator(lon,lat){return maplibre.MercatorCoordinate.fromLngLat([lon,lat]);}

function prepareGeography(){
  const signature=atlasSignature();
  const changed=signature!==geoSignature;
  geoSignature=signature;
  geoCells=[];geoBounds=null;
  if(atlas?.source!=='published'){
    document.body.dataset.map='grid';$('map-attribution').hidden=true;return;
  }
  for(const [index,record] of atlas.records.entries()){
    if(!record||!Number.isFinite(record.latitude)||!Number.isFinite(record.longitude))continue;
    const lat=record.latitude,lon=record.longitude;
    const height=Number(record.height_deg)||atlas.area.cell_size_blocks/111320;
    const width=Number(record.width_deg)||atlas.area.cell_size_blocks/(111320*Math.cos(lat*Math.PI/180));
    if(lat<=-85||lat>=85||lon<-180||lon>180||height<=0||width<=0)continue;
    const bounds=[lon-width/2,lat-height/2,lon+width/2,lat+height/2];
    const corners=record.corners_lonlat||[[bounds[0],bounds[3]],[bounds[2],bounds[3]],[bounds[2],bounds[1]],[bounds[0],bounds[1]]];
    const exactBounds=[Math.min(...corners.map(p=>p[0])),Math.min(...corners.map(p=>p[1])),Math.max(...corners.map(p=>p[0])),Math.max(...corners.map(p=>p[1]))];
    geoCells.push({index,bounds:exactBounds,corners,points:corners.map(p=>mercator(...p)),nw:mercator(exactBounds[0],exactBounds[3]),se:mercator(exactBounds[2],exactBounds[1])});
    bounds.splice(0,4,...exactBounds);
    if(!geoBounds)geoBounds=[...bounds];
    else geoBounds=[Math.min(geoBounds[0],bounds[0]),Math.min(geoBounds[1],bounds[1]),Math.max(geoBounds[2],bounds[2]),Math.max(geoBounds[3],bounds[3])];
  }
  if(geoCells.length&&geoMap){
    document.body.dataset.map='geographic';
    $('map-attribution').hidden=document.body.dataset.basemap!=='ready';
    geoMap.resize();if(changed)fitGeography();
  }else document.body.dataset.map='grid';
}

function fitGeography(){
  if(!geoMap||!geoBounds)return;
  const desktop=innerWidth>=1024,panel=desktop&&document.body.dataset.panel==='open';
  geoMap.fitBounds([[geoBounds[0],geoBounds[1]],[geoBounds[2],geoBounds[3]]],{
    padding:{top:document.querySelector('.map-tools').getBoundingClientRect().height+24,bottom:110,left:panel?$('details-panel').getBoundingClientRect().right+24:24,right:72},duration:0,maxZoom:14
  });
  for(const id of Object.keys(places))$('place-'+id).setAttribute('aria-pressed','false');
  updateZoomButtons();scheduleDraw();
}

async function initializeBasemap(){
  try{
    maplibre.setWorkerUrl(new URL('./assets/vendor/maplibre/maplibre-gl-worker.mjs',import.meta.url).href);
    maplibre.setWorkerCount(1);
    geoMap=new maplibre.Map({container:'basemap',style:{version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#ecebe3'}}]},center:places.chicago.coordinates,zoom:10.5,minZoom:7,maxZoom:18,attributionControl:false,dragRotate:false,pitchWithRotate:false,touchPitch:false,renderWorldCopies:false});
    geoMap.keyboard.disable();geoMap.touchZoomRotate.disableRotation();
    geoMap.getCanvas().tabIndex=-1;geoMap.getCanvas().setAttribute('aria-hidden','true');
    geoMap.on('move',()=>{scheduleDraw();updateZoomButtons();});
    geoMap.on('movestart',()=>{document.body.dataset.camera='moving';});
    geoMap.on('idle',()=>{document.body.dataset.camera='idle';});
    geoMap.on('click',event=>{
      if(!geographic())return;
      const cell=cellAtLocation(event.lngLat);
      if(cell)selectIndex(cell.index);else clearSelection();
    });
    geoMap.on('mousemove',event=>{
      if(!geographic())return;
      const cell=cellAtLocation(event.lngLat),index=cell?.index??null;
      geoMap.getCanvas().style.cursor=cell?'pointer':'';
      if(index!==hoverIndex){hoverIndex=index;scheduleDraw();}
    });
    geoMap.on('moveend',()=>{if(document.body.dataset.section==='browse')cellBrowser?.refresh();});
    geoMap.on('mouseout',()=>{hoverIndex=null;scheduleDraw();});
    geoMap.on('error',()=>{
      document.body.dataset.basemap='unavailable';
      $('basemap-state').hidden=false;
      $('basemap-state').textContent='Street map unavailable. Build cells are still shown.';
    });
    const response=await fetch('https://tiles.openfreemap.org/styles/positron',{signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw Error('Basemap unavailable');
    const style=await response.json();
    // MapLibre's style API takes RGB colors; keep geography quiet under build cells.
    for(const layer of style.layers){
      if(layer.type==='background')layer.paint['background-color']='#ecebe3';
      if(layer.id==='water')layer.paint['fill-color']='#c9dce1';
      if(layer.type==='symbol'&&layer.layout?.['text-field']){
        layer.paint={...layer.paint,'text-color':layer.id.startsWith('place_')?'#414d47':'#617069','text-halo-color':'#f6f3eb','text-halo-width':1};
      }
    }
    geoMap.setStyle(style);
    geoMap.once('idle',()=>{
      document.body.dataset.basemap='ready';$('basemap-state').hidden=true;
      $('map-attribution').hidden=!geographic();scheduleDraw();
    });
  }catch{
    document.body.dataset.basemap='unavailable';
    $('basemap-state').hidden=false;
    $('basemap-state').textContent=geoMap?'Street map unavailable. Build cells are still shown.':'Street map unavailable. Showing the generation grid.';
  }
}

function cellAtLocation({lng,lat}){
  return geoCells.find(({bounds:[west,south,east,north],corners})=>{
    if(lng<west||lng>east||lat<south||lat>north)return false;
    let inside=false;
    for(let i=0,j=3;i<4;j=i++){
      const a=corners[i],b=corners[j];
      if((a[1]>lat)!==(b[1]>lat)&&lng<(b[0]-a[0])*(lat-a[1])/(b[1]-a[1])+a[0])inside=!inside;
    }
    return inside;
  });
}

function drawGeography(ctx,cssWidth,cssHeight){
  // A flat Mercator camera makes every cell share one transform per frame.
  // Cache geographic corners; avoid projecting thousands of points while dragging.
  const first=geoCells[0],anchor=geoMap.project([first.bounds[0],first.bounds[3]]);
  const reference=geoMap.project([first.bounds[0]+1,first.bounds[3]]);
  const scale=(reference.x-anchor.x)*360,colors=palette();
  let selected=null,hover=null;
  // One path per colour, filled once: translucent cells filled one by one
  // double their alpha along shared antialiased edges and read as a dotted grid.
  const groups=new Map(),outlines=[];
  for(const cell of geoCells){
    const x=anchor.x+(cell.nw.x-first.nw.x)*scale,y=anchor.y+(cell.nw.y-first.nw.y)*scale;
    const w=(cell.se.x-cell.nw.x)*scale,h=(cell.se.y-cell.nw.y)*scale;
    if(x+w<0||y+h<0||x>cssWidth||y>cssHeight)continue;
    const record=atlas.records[cell.index];
    const complete=record.appearance_state==='complete'||record.appearance_state==='verified';
    const code=mapLayer==='appearance'?(complete?2:1):atlas.cells[cell.index];
    const level=mapLayer==='detail'?detailLevel(cell.index):null;
    const fill=mapLayer==='detail'?detailColor(level):colors[code]||colors[0];
    const alpha=mapLayer==='detail'?(level?.6:.12):mapLayer==='appearance'?(complete?.8:.16):.28;
    const points=cell.points.map(p=>({x:anchor.x+(p.x-first.nw.x)*scale,y:anchor.y+(p.y-first.nw.y)*scale}));
    const key=fill+'|'+alpha;
    if(!groups.has(key))groups.set(key,{fill,alpha,path:new Path2D()});
    // Grow each cell by a fraction of a pixel so neighbours meet without hairline gaps.
    const cx=(points[0].x+points[2].x)/2,cy=(points[0].y+points[2].y)/2,path=groups.get(key).path;
    points.forEach((p,i)=>{const dx=p.x-cx,dy=p.y-cy,d=Math.hypot(dx,dy)||1,q={x:p.x+dx/d*.35,y:p.y+dy/d*.35};i?path.lineTo(q.x,q.y):path.moveTo(q.x,q.y);});
    path.closePath();
    if(w>5)outlines.push({points,stroke:mapLayer==='detail'?fill:colors[code]});
    if(cell.index===selectedIndex)selected=points;
    if(cell.index===hoverIndex)hover=points;
  }
  for(const {fill,alpha,path} of groups.values()){ctx.fillStyle=fill;ctx.globalAlpha=alpha;ctx.fill(path);}
  ctx.globalAlpha=mapLayer==='appearance'?.25:.42;ctx.lineWidth=.6;
  for(const {points,stroke} of outlines){
    ctx.strokeStyle=stroke;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();ctx.stroke();
  }
  ctx.globalAlpha=1;
  for(const [points,strong] of [[hover,false],[selected,true]])if(points){
    ctx.strokeStyle=color(strong?'ink':'green');ctx.lineWidth=strong?2.5:1.5;
    ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();ctx.stroke();
  }
  const a=geoMap.unproject([cssWidth/2,cssHeight/2]),b=geoMap.unproject([cssWidth/2+68,cssHeight/2]);
  $('map-scale').textContent=distanceLabel(Math.round(a.distanceTo(b)));
}

function setLayer(layer){
  mapLayer=layer;
  for(const name of ['build','appearance','detail'])$('layer-'+name).setAttribute('aria-pressed',String(layer===name));
  if(atlas){renderLegend(atlas.source==='published',atlas.counts,atlas.area.id==='live');drawAtlas();}
  if(layer==='appearance'&&atlas?.source==='published'){
    const complete=atlas.records.filter(record=>['complete','verified'].includes(record?.appearance_state)).length;
    setMapMessage(number(complete)+' cells with surface detail · '+number(atlas.area.tiles)+' in total');
  }else if(layer==='detail'&&atlas?.source==='published'){
    setMapMessage(detail?'Detail level of each 512 m tile':'Detail levels are not available yet');
  }else if(atlas)renderAtlas();
}

function goToPlace(id){
  if(!geographic())return;
  const place=places[id];$('places-menu').open=false;
  for(const name of Object.keys(places))$('place-'+name).setAttribute('aria-pressed',String(name===id));
  geoMap.easeTo({center:place.coordinates,zoom:13,duration:0});
  const cell=cellAtLocation({lng:place.coordinates[0],lat:place.coordinates[1]});
  if(cell){selectIndex(cell.index);setMapMessage(place.name+' · cell '+atlas.cell_ids[cell.index]);}
  else{clearSelection();setMapMessage(place.name+' is not on this map yet.');}
}

$('location-search').onsubmit=async event=>{
  event.preventDefault();
  const status=$('search-status');
  try{
    const location=parseLocation($('location-query').value,places);
    if(!geographic())throw new Error('The map is still loading. Try again in a moment.');
    const [lng,lat]=location.coordinates;
    const cell=cellAtLocation({lng,lat});
    if(!cell){clearSelection();status.textContent=location.name+' is not built yet.';return;}
    geoMap.easeTo({center:location.coordinates,zoom:13,duration:0});
    selectIndex(cell.index);
    status.textContent='Showing '+location.name+'.';
  }catch(error){status.textContent=error.message;}
};
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
  const world=$('area').value==='world'?data.generated_world:null;
  const defaults=world?.cell_defaults||{};
  const rows=(world?.cells||data.cells||[]).map(cell=>{
    const id=String(cell.tile_id||String(cell.id||'').split('/').pop()||'');
    const match=id.match(/^(-?\d+)_(-?\d+)$/);
    return match?{...defaults,...cell,id,x:Number(match[1]),z:Number(match[2])}:null;
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
  const cellSize=Number(world?.cell_size_m||data.cell_grid?.cell_size_m)||256;
  return {
    source:'published',
    area:{
      id:world?'world':'published',label:'Chicago area',left_block:left*cellSize,top_block:top*cellSize,
      width_cells:width,height_cells:height,width_chunks:width*cellSize/16,height_chunks:height*cellSize/16,
      cell_size_blocks:cellSize,cell_label:'generation cell',tiles:rows.length
    },
    palette:['Queued','Data ready','Terrain & buildings built','Building','Needs review'],
    cells,cell_ids:cellIds,records,counts,failed_tiles:counts.failed,
    updated_utc:data.updated_utc,overlays:{}
  };
}

function renderSnapshot(){
  if(!snapshot)return;
  const rollup=snapshot.rollup||{},local=snapshot.local||{};
  if(snapshot.source==='public')$('area').value=atlas?.area.id==='world'?'world':'published';
  $('phase').textContent=snapshot.regions?.[0]?.label||'Chicago area';
  $('connection').textContent=snapshot.source==='local'?'Local build connected':'Published snapshot';
  document.body.dataset.feed=snapshot.source;
  $('updated').textContent=snapshot.updated_utc?(snapshot.source==='public'?'Snapshot published ':'Map updated ')+dateLabel(snapshot.updated_utc):'';
  $('rollup').textContent=stateLabel(rollup.state);
  $('geometry').textContent=countLabel(rollup.generated_tiles);
  $('sources').textContent=countLabel(local.source_tiles_complete);
  $('appearance').textContent=countLabel(local.stages?.appearance?.complete);
  const world=atlas?.area.id==='world'?snapshot.generated_world:null;
  if(world){
    $('phase').textContent='Chicago area';
    $('geometry').textContent=number(world.unique_cells);
    $('sources').textContent=number(world.scan_upgrade_parent_tiles);
    $('appearance').textContent=number(world.photo_colored_cells);
    $('rollup').textContent='Built';
  }
  $('geometry-label').textContent=world?'Cells built':'Terrain & buildings';
  $('sources-label').textContent=world?'Tiles with extra scan detail':'Data ready';
  $('appearance-label').textContent=world?'Cells with photo color':'Surface detail';
  $('record-note').textContent=world?'Saved-world footprint snapshot, separate from new AWS publications. Each cell is a '+number(world.cell_size_m||256)+' m square. Select one to see its blocks.':'Historical build record, separate from current AWS publications. Green cells have terrain and buildings.';
  $('auto-control').hidden=snapshot.source==='public';
  $('recovery').textContent=snapshot.source==='local'
    ?'Source files are reused between tiles. Interrupted work resumes from its last checkpoint.'
    :'';
  const stageNames={sources:'Map data',geometry:'Terrain & buildings',appearance:'Surface detail',game_verify:'Minecraft check'};
  $('log').textContent=Object.entries(local.stages||{}).map(([stage,counts])=>(stageNames[stage]||stateLabel(stage))+': '+(Object.entries(counts).map(([state,count])=>stateLabel(state)+' '+number(count)).join(', ')||'No jobs')).join('\n')||'No stage counts recorded';
  $('footer-state').textContent=new Date().toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});
  $('warning').hidden=true;
}

function resizeCanvas(){
  if(!atlas)return;
  const canvas=$('map'),rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  canvas.width=Math.max(1,Math.round(rect.width*dpr));
  canvas.height=Math.max(1,Math.round(rect.height*dpr));
  if(geographic())geoMap.resize();
  drawAtlas();
}

function dateLabel(value){return new Date(value).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'});}
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
  if(geographic()){drawGeography(ctx,cssWidth,cssHeight);return;}
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
      let code=atlas.cells[row*width+column];
      if(code<0)continue;
      if(atlas.source==='published'&&mapLayer==='appearance')code=['complete','verified'].includes(atlas.records[row*width+column]?.appearance_state)?2:1;
      if(left+(column+1)*cell<0||left+column*cell>cssWidth||top+(row+1)*cell<0||top+row*cell>cssHeight)continue;
      ctx.fillStyle=atlas.source==='published'&&mapLayer==='detail'?detailColor(detailLevel(row*width+column)):colors[code]||colors[0];
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
  if(isPublished&&mapLayer==='detail'){
    for(const id of ['outside','queued','source','finished','installed','running','failed'])$('legend-'+id).hidden=true;
    return;
  }
  $('legend-outside').hidden=false;
  if(isPublished&&mapLayer==='appearance'){
    $('legend-outside-label').textContent='Not built yet';
    $('legend-source-label').textContent='Awaiting detail';
    $('legend-finished-label').textContent='Surface detail added';
    for(const id of ['queued','installed','running','failed'])$('legend-'+id).hidden=true;
    $('legend-source').hidden=false;$('legend-finished').hidden=false;
    return;
  }
  $('legend-outside-label').textContent=isPublished?'Not built yet':live?'Not imported':'Outside plan';
  $('legend-queued-label').textContent='Queued';
  $('legend-source-label').textContent='Data ready';
  $('legend-finished-label').textContent=live?'In Minecraft':'Built';
  $('legend-queued').hidden=live||!counts.white;
  $('legend-source').hidden=live||!counts.gray;
  $('legend-finished').hidden=false;
  $('legend-installed').hidden=live||isPublished;
  $('legend-running').hidden=live||!counts.active;
  $('legend-failed').hidden=live||!counts.failed;
}

function renderAtlas(){
  cellBrowser?.setData(atlas,snapshot);
  $('browse-open').disabled=!atlas;
  $('coverage-note').textContent=!atlas?'Map unavailable':atlas.area.id==='world'?'Built so far · the Chicago area':'Build progress · the Chicago area';
  if(!atlas){
    geoSignature='';geoCells=[];document.body.dataset.map='grid';$('map-attribution').hidden=true;
    for(const id of ['place-chicago','place-elmhurst','layer-appearance','layer-detail'])$(id).disabled=true;
    $('grid-size').textContent='Unavailable';
    $('grid-count').textContent='This view has no map yet.';
    $('chunk-count').textContent='Unavailable';
    $('border').textContent='Unavailable';
    $('leases').textContent='Unavailable';
    setMapMessage('No map is available right now.');
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
  prepareGeography();
  for(const id of ['place-chicago','place-elmhurst'])$(id).disabled=!geographic();
  $('layer-appearance').disabled=atlas.source!=='published';$('layer-detail').disabled=atlas.source!=='published';
  if(atlas.source!=='published'){
    mapLayer='build';$('layer-build').setAttribute('aria-pressed','true');$('layer-appearance').setAttribute('aria-pressed','false');$('layer-detail').setAttribute('aria-pressed','false');
  }
  const widthBlocks=area.width_chunks*16,heightBlocks=area.height_chunks*16;
  const isPublished=atlas.source==='published',live=area.id==='live',queue=area.id==='queue';
  const mapped=countCells(counts),built=Number(counts.green||0);
  if(isPublished){
    const world=area.id==='world'?snapshot.generated_world:null;
    $('grid-size').textContent=number(area.tiles)+' cells';
    $('grid-count').textContent=distanceLabel(widthBlocks)+' × '+distanceLabel(heightBlocks)+' · '+number(area.cell_size_blocks)+' m per cell.';
    $('chunk-count').textContent=number(mapped)+' cells';
    $('border').textContent='Published grid';
    $('leases').textContent='Snapshot';
    $('map-unit').textContent=number(area.cell_size_blocks)+' m cells';
    $('map-title').textContent=world?'What’s built':'Build progress';
    $('map-caption').textContent='Each square covers '+number(area.cell_size_blocks)+' × '+number(area.cell_size_blocks)+' metres. Green means terrain and buildings are built.';
    if(world){
      $('map-caption').textContent='Each square covers '+number(area.cell_size_blocks)+' × '+number(area.cell_size_blocks)+' metres of the Chicago area. Select one to see it up close or in 3D.';
      $('border').textContent='Existing world frame';
      $('chunk-count').textContent=number(world.native_tiles)+' tiles';
    }
    const detailed=atlas.records.filter(record=>['complete','verified'].includes(record?.appearance_state)).length;
    setMapMessage(mapLayer==='appearance'?number(detailed)+' cells with surface detail · '+number(area.tiles)+' in total':world?number(area.tiles)+' cells built':built===area.tiles?number(built)+' cells built':number(area.tiles)+' cells · '+number(built)+' built');
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
  $('map-updated').textContent=atlas.updated_utc?'Updated '+dateLabel(atlas.updated_utc):'';
  $('map-summary').textContent=number(counts.white)+' queued, '+number(counts.gray)+' with data ready, '+number(counts.green)+(live?' imported':' built')+' cells, '+number(counts.active)+' in progress, '+number(counts.failed)+' needing review.';
  if(area.id==='world')$('map-summary').textContent=number(area.tiles)+' cells built, each '+number(area.cell_size_blocks)+' m square.';
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
  if(['world','published'].includes($('area').value))return readPublished();
  try{await readLocal();}catch(localError){await readPublished();}
}

async function refresh(){
  if(busy){refreshQueued=true;return;}
  busy=true;
  $('refresh').disabled=true;
  $('refresh').dataset.busy='true';
  $('refresh').textContent='…';
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
    $('warning').textContent=atlas?'The map could not be updated. The last loaded map is still shown. Use Refresh to try again.':'The map could not be loaded. Check your connection and use Refresh to try again.';
    $('mapwrap').setAttribute('aria-busy','false');
    if(!snapshot){
      $('phase').textContent='Map unavailable';
      $('grid-size').textContent='Unavailable';
      $('map-title').textContent='Map unavailable';
      $('map-caption').textContent='The map will appear once it can be loaded.';
      $('map-summary').textContent='Map unavailable. Use Refresh to try again.';
      $('map').setAttribute('aria-label','Map unavailable. Use Refresh to try again.');
      for(const id of ['geometry','sources','appearance','chunk-count','border','leases','rollup'])$(id).textContent='Not recorded';
      for(const id of ['legend-queued','legend-source','legend-finished','legend-installed','legend-running','legend-failed'])$(id).hidden=true;
      $('selection-title').textContent='No cell selected';
      $('cell').textContent='Load the map to look at cells.';
      $('log').textContent='No build details loaded';
      $('footer-state').textContent='Not loaded';
      updateZoomButtons();
    }
    if(!atlas)setMapMessage('Map unavailable. Use Refresh to try again.');
  }finally{
    busy=false;
    $('refresh').disabled=false;
    $('refresh').dataset.busy='false';
    $('refresh').textContent='↻';
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

let cellPreview=null,cellToken=0;
function renderSelection(){
  const details=$('selection-details');details.replaceChildren();
  details.hidden=true;$('clear-selection').hidden=selectedIndex===null;$('cell-actions').hidden=true;$('cell-view').hidden=true;
  const token=++cellToken;
  if(selectedIndex===null||!atlas){
    $('selection-title').textContent='Choose a cell';
    $('cell').hidden=false;$('cell').textContent='Select a square on the map to see it up close. You can also use the arrow keys.';
    return;
  }
  const width=atlas.area.width_cells,column=selectedIndex%width,row=Math.floor(selectedIndex/width);
  const code=atlas.cells[selectedIndex],record=atlas.records?.[selectedIndex],size=atlas.area.cell_size_blocks;
  const blockX=atlas.area.left_block+column*size,blockZ=atlas.area.top_block+row*size;
  const unit=size===16?'Chunk':'Cell';
  const id=atlas.cell_ids?.[selectedIndex]||Math.floor(blockX/size)+'_'+Math.floor(blockZ/size);
  $('selection-title').textContent=unit+' '+id.replace('_',', ');
  const rows=[['Size',number(size)+' × '+number(size)+' m']];
  if(record&&Number.isFinite(record.latitude)&&Number.isFinite(record.longitude))rows.push(['Location',record.latitude.toFixed(4)+'°, '+record.longitude.toFixed(4)+'°']);
  rows.push(['World X / Z',number(blockX)+' / '+number(blockZ)]);
  if(detail&&atlas.source==='published')rows.push(['Detail',detailLevel(selectedIndex)||'not checked yet']);
  for(const [label,value] of rows){
    const item=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');
    dt.textContent=label;dd.textContent=value;item.append(dt,dd);details.append(item);
  }
  details.hidden=false;
  if(code<0){$('cell').hidden=false;$('cell').textContent='Not built yet.';return;}
  $('cell').hidden=true;
  const cx=Math.floor(blockX+size/2),cz=Math.floor(blockZ+size/2),radius=Math.max(1,Math.min(8,Math.ceil(size/32)));
  $('cell-blocks').href=`./blocks.html?block=${cx},${cz}&radius=${radius}`;$('cell-actions').hidden=false;
  $('cell-view').hidden=false;$('cell-view').dataset.state='loading';$('cell-view-status').textContent='Loading blocks…';
  cellPreview??=import('./cell-preview.mjs?v=cell-2');
  cellPreview.then(module=>module.showCell($('cell-canvas'),{x:cx,z:cz,radius:2})).then(result=>{
    if(token!==cellToken||result.status==='aborted')return;
    $('cell-view').dataset.state=result.status;
    $('cell-view-status').textContent=result.status==='empty'?'Not built yet.':'Drag to turn · scroll or pinch to zoom.';
  }).catch(error=>{
    if(token!==cellToken)return;console.warn(error);
    $('cell-view').dataset.state='error';$('cell-view-status').textContent='The 3D view could not be drawn here. Use Explore in Minecraft viewer.';
  });
}

function clearSelection(){
  selectedIndex=null;hoverIndex=null;renderSelection();
  if(document.body.dataset.section==='cell')setPanel(innerWidth>=1024,'world');
  if(atlas)drawAtlas();
}
function selectIndex(index){
  selectedIndex=index;renderSelection();setPanel(true,'cell');
  document.querySelector('.panel-content').scrollTop=0;
  if(geographic()){
    const record=atlas.records?.[index];
    if(record){
      const point=geoMap.project([record.longitude,record.latitude]);
      if(innerWidth<1024){
        const available=$('details-panel').getBoundingClientRect().top-$('map').getBoundingClientRect().top;
        geoMap.panBy([point.x-innerWidth/2,point.y-(available/2+28)],{duration:0});
      }else if(point.x<$('details-panel').getBoundingClientRect().right+24||point.y<0||point.x>innerWidth||point.y>$('map').clientHeight)geoMap.panTo([record.longitude,record.latitude],{duration:0});
    }
  }
  drawAtlas();
}
function updateZoomButtons(){
  $('zoom-in').disabled=!atlas||(geographic()?geoMap.getZoom()>=18:zoom>=4);
  $('zoom-out').disabled=!atlas||(geographic()?geoMap.getZoom()<=7:zoom<=1);
  $('zoom-reset').disabled=!atlas;
}
function changeZoom(delta){
  if(geographic())geoMap.zoomTo(Math.max(7,Math.min(18,geoMap.getZoom()+delta)),{duration:0});
  else zoom=Math.max(1,Math.min(4,zoom+delta));
  updateZoomButtons();if(atlas)drawAtlas();
}

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
  if(event.shiftKey&&geographic()){
    geoMap.panBy([event.key==='ArrowRight'?80:event.key==='ArrowLeft'?-80:0,event.key==='ArrowDown'?80:event.key==='ArrowUp'?-80:0],{duration:0});
    return;
  }
  const width=atlas.area.width_cells,height=atlas.area.height_cells;
  if(selectedIndex===null){const first=atlas.cells.findIndex(code=>code>=0);if(first>=0)selectIndex(first);return;}
  const column=selectedIndex%width,row=Math.floor(selectedIndex/width);
  const nextColumn=Math.max(0,Math.min(width-1,column+(event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0)));
  const nextRow=Math.max(0,Math.min(height-1,row+(event.key==='ArrowDown'?1:event.key==='ArrowUp'?-1:0)));
  selectIndex(nextRow*width+nextColumn);
});
$('zoom-in').onclick=()=>changeZoom(1);
$('zoom-out').onclick=()=>changeZoom(-1);
$('zoom-reset').onclick=()=>{if(geographic())fitGeography();else{zoom=1;updateZoomButtons();drawAtlas();}};
$('panel-toggle').onclick=()=>setPanel(document.body.dataset.panel!=='open');
$('panel-close').onclick=()=>{setPanel(false);$('panel-toggle').focus();};
$('about-open').onclick=()=>setPanel(true,'about');
for(const id of ['world','cell'])$('tab-'+id).onclick=()=>setPanel(true,id);
for(const id of ['build','appearance','detail'])$('layer-'+id).onclick=()=>setLayer(id);
for(const id of Object.keys(places))$('place-'+id).onclick=()=>goToPlace(id);
$('clear-selection').onclick=()=>{clearSelection();$('map').focus();};
$('refresh').onclick=refresh;
$('area').onchange=refresh;
$('auto').onchange=()=>{if($('auto').checked)refresh();};
window.addEventListener('resize',resizeCanvas);
document.fonts.ready.then(()=>{if(atlas)drawAtlas();});
const localHost=['localhost','127.0.0.1','[::1]'].includes(location.hostname);
$('area').value=new URLSearchParams(location.search).get('view')==='published'?'published':'world';
if(!localHost)for(const option of $('area').options)if(!['world','published'].includes(option.value))option.disabled=true;
setPanel(false,'world');
initializeBasemap();
refresh();
loadDetail();
let ticks=0;
setInterval(()=>{ticks+=1;if(snapshot?.source!=='public'&&$('auto').checked&&!document.hidden&&(!['world','published'].includes($('area').value)||ticks%6===0))refresh();},10000);


cellBrowser=installCellBrowser($('browse-panel'),{select:index=>{selectIndex(index);$('selection-title').tabIndex=-1;$('selection-title').focus({preventScroll:true});},visibleBounds:()=>{if(!geographic())return null;const bounds=geoMap.getBounds();return {west:bounds.getWest(),east:bounds.getEast(),south:bounds.getSouth(),north:bounds.getNorth()};}});
$('browse-open').onclick=()=>{setPanel(true,'browse');cellBrowser.setData(atlas,snapshot);};
