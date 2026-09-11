const $=id=>document.getElementById(id);
let data=null,view=null,busy=false,animationFrame=0,lastFrame=0,rotation=0;
const motionQuery=window.matchMedia?.('(prefers-reduced-motion: reduce)');
let reducedMotion=motionQuery?.matches??false;
const number=value=>Number(value||0).toLocaleString();
const stateLabel=value=>String(value||'unknown').replaceAll('-',' ');
const colors={paper:'oklch(.965 .012 85)',ink:'oklch(.25 .022 155)',muted:'oklch(.48 .025 155)',line:'oklch(.81 .023 100)',green:'oklch(.45 .10 155)',amber:'oklch(.68 .12 80)',wash:'oklch(.925 .025 105)',ocean:'oklch(.78 .055 215)',oceanDark:'oklch(.61 .075 215)'};

function project(latitude,longitude,globe){
  const lat=Number(latitude)*Math.PI/180;
  const lon=Number(longitude)*Math.PI/180-globe.rotation;
  const cosLat=Math.cos(lat),x=cosLat*Math.sin(lon),y=Math.sin(lat),z=cosLat*Math.cos(lon);
  return z>-.015?{x:globe.cx+x*globe.radius,y:globe.cy-y*globe.radius,z}:null;
}

function drawProjectedLine(ctx,points,globe){
  let open=false;
  for(const [latitude,longitude] of points){
    const point=project(latitude,longitude,globe);
    if(point){
      if(!open){ctx.beginPath();ctx.moveTo(point.x,point.y);open=true}else ctx.lineTo(point.x,point.y);
    }else if(open){ctx.stroke();open=false}
  }
  if(open)ctx.stroke();
}

function drawGraticule(ctx,globe){
  ctx.save();ctx.beginPath();ctx.arc(globe.cx,globe.cy,globe.radius,0,Math.PI*2);ctx.clip();
  ctx.strokeStyle='oklch(.82 .035 215 / .72)';ctx.lineWidth=.7;
  for(let longitude=-180;longitude<180;longitude+=30){
    const points=[];for(let latitude=-90;latitude<=90;latitude+=3)points.push([latitude,longitude]);drawProjectedLine(ctx,points,globe);
  }
  for(let latitude=-60;latitude<=60;latitude+=30){
    const points=[];for(let longitude=-180;longitude<=180;longitude+=3)points.push([latitude,longitude]);drawProjectedLine(ctx,points,globe);
  }
  ctx.strokeStyle='oklch(.55 .055 215 / .85)';ctx.lineWidth=1;
  drawProjectedLine(ctx,Array.from({length:121},(_,index)=>[index*1.5-90,0]),globe);
  drawProjectedLine(ctx,Array.from({length:241},(_,index)=>[0,index*1.5-180]),globe);
  ctx.restore();
}

function drawRegions(ctx,globe,regions){
  ctx.font='11px Helvetica Neue, Arial, sans-serif';
  for(const region of regions){
    const point=project(region.latitude,region.longitude,globe);if(!point)continue;
    ctx.fillStyle=colors.green;ctx.beginPath();ctx.arc(point.x,point.y,6,0,Math.PI*2);ctx.fill();
    ctx.fillStyle=colors.ink;ctx.fillText(region.label||region.id,point.x+10,point.y+4);
  }
}

function drawWorkstreams(ctx,globe,rows,width){
  const left=globe.sidePanel?globe.cx+globe.radius+34:24;
  const top=globe.sidePanel?globe.cy-globe.radius+24:globe.cy+globe.radius+28;
  ctx.font='12px Helvetica Neue, Arial, sans-serif';
  rows.forEach((workstream,index)=>{
    const y=top+index*25;ctx.fillStyle=workstream.state==='implemented'?colors.green:colors.amber;ctx.fillRect(left,y-8,8,8);
    ctx.fillStyle=colors.ink;ctx.fillText(`${workstream.label} · ${stateLabel(workstream.state)}`,left+16,y);
  });
}

function draw(now=performance.now()){
  animationFrame=0;if(!data)return;
  if(lastFrame&&!reducedMotion)rotation+=Math.min(now-lastFrame,100)/1000*.12;lastFrame=now;
  const canvas=$('map'),ctx=canvas.getContext('2d'),dpr=devicePixelRatio||1;
  const width=canvas.clientWidth,height=canvas.clientHeight;
  canvas.width=width*dpr;canvas.height=height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);
  const sidePanel=$('layer').value==='workstreams'&&width>700;
  const globeWidth=sidePanel?width-280:width;
  const globe={cx:sidePanel?globeWidth/2:width/2,cy:height/2,radius:Math.min(globeWidth*.38,height*.42),rotation,sidePanel};view=globe;
  ctx.fillStyle=colors.wash;ctx.fillRect(0,0,width,height);
  const sphere=ctx.createRadialGradient(globe.cx-globe.radius*.34,globe.cy-globe.radius*.42,globe.radius*.08,globe.cx,globe.cy,globe.radius*1.08);
  sphere.addColorStop(0,colors.ocean);sphere.addColorStop(1,colors.oceanDark);
  ctx.fillStyle=sphere;ctx.beginPath();ctx.arc(globe.cx,globe.cy,globe.radius,0,Math.PI*2);ctx.fill();
  drawGraticule(ctx,globe);
  ctx.strokeStyle=colors.green;ctx.lineWidth=1.2;ctx.beginPath();ctx.arc(globe.cx,globe.cy,globe.radius,0,Math.PI*2);ctx.stroke();
  const regions=data.regions||[];
  if($('layer').value==='regions')drawRegions(ctx,globe,regions);else drawWorkstreams(ctx,globe,data.workstreams||[],width);
  $('mapnote').textContent=regions.length?`${number(regions.length)} region${regions.length===1?'':'s'} indexed · rotating globe`:'No measured regions yet';
  if(!reducedMotion&&!document.hidden)animationFrame=requestAnimationFrame(draw);
}

function scheduleDraw(){
  if(!data)return;
  if(reducedMotion||document.hidden)draw();else if(!animationFrame)animationFrame=requestAnimationFrame(draw);
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
  $('warning').hidden=false;$('warning').textContent=warning;scheduleDraw();
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

$('refresh').onclick=refresh;$('layer').onchange=scheduleDraw;$('auto').onchange=()=>{if($('auto').checked)refresh()};
window.addEventListener('resize',scheduleDraw);
document.addEventListener('visibilitychange',()=>{if(document.hidden&&animationFrame)cancelAnimationFrame(animationFrame),animationFrame=0;else scheduleDraw()});
motionQuery?.addEventListener?.('change',event=>{reducedMotion=event.matches;lastFrame=0;scheduleDraw()});
$('map').addEventListener('mousemove',event=>{
  if(!view||!data)return;const rect=$('map').getBoundingClientRect();
  const x=(event.clientX-rect.left-view.cx)/view.radius,y=(view.cy-(event.clientY-rect.top))/view.radius;
  if(x*x+y*y>1){$('cell').textContent='Move over the globe to inspect a coordinate.';return}
  const longitude=((Math.atan2(x,Math.sqrt(Math.max(0,1-x*x-y*y)))+view.rotation)*180/Math.PI+540)%360-180;
  const latitude=Math.asin(y)*180/Math.PI;
  $('cell').textContent=`Approximate coordinate ${Math.round(latitude*10)/10}°, ${Math.round(longitude*10)/10}°.`;
});
refresh();setInterval(()=>{if($('auto').checked&&!document.hidden)refresh()},10000);
