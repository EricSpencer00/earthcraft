const $=id=>document.getElementById(id);
let data=null,view=null,busy=false;
const number=value=>Number(value||0).toLocaleString();
const stateLabel=value=>String(value||'unknown').replaceAll('-',' ');

function draw(){
  if(!data)return;
  const canvas=$('map'),ctx=canvas.getContext('2d'),dpr=devicePixelRatio||1;
  const width=canvas.clientWidth,height=canvas.clientHeight;
  canvas.width=width*dpr;canvas.height=height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);
  const pad=28,x0=pad,y0=24,mapWidth=width-pad*2,mapHeight=height-54;
  view={x0,y0,mapWidth,mapHeight};
  ctx.fillStyle='oklch(.925 .025 105)';ctx.fillRect(0,0,width,height);
  ctx.strokeStyle='oklch(.81 .023 100)';ctx.lineWidth=.6;
  for(let lon=-180;lon<=180;lon+=30){const x=x0+(lon+180)/360*mapWidth;ctx.beginPath();ctx.moveTo(x,y0);ctx.lineTo(x,y0+mapHeight);ctx.stroke()}
  for(let lat=-60;lat<=60;lat+=30){const y=y0+(90-lat)/180*mapHeight;ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x0+mapWidth,y);ctx.stroke()}
  ctx.strokeStyle='oklch(.55 .025 155)';ctx.lineWidth=1;
  ctx.beginPath();ctx.ellipse(x0+mapWidth/2,y0+mapHeight/2,mapWidth/2,mapHeight/2,0,0,Math.PI*2);ctx.stroke();
  const regions=data.regions||[];
  if($('layer').value==='regions'){
    for(const region of regions){
      const x=x0+(Number(region.longitude)+180)/360*mapWidth;
      const y=y0+(90-Number(region.latitude))/180*mapHeight;
      ctx.fillStyle='oklch(.45 .10 155)';ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();
      ctx.fillStyle='oklch(.25 .022 155)';ctx.font='11px Helvetica Neue, Arial, sans-serif';ctx.fillText(region.label||region.id,x+10,y+4);
    }
  }else{
    const rows=data.workstreams||[];
    rows.forEach((workstream,index)=>{
      const y=y0+18+index*28;ctx.fillStyle=workstream.state==='implemented'?'oklch(.45 .10 155)':'oklch(.68 .12 80)';ctx.fillRect(x0+12,y-8,8,8);
      ctx.fillStyle='oklch(.25 .022 155)';ctx.font='12px Helvetica Neue, Arial, sans-serif';ctx.fillText(`${workstream.label} · ${stateLabel(workstream.state)}`,x0+28,y);
    });
  }
  $('mapnote').textContent=regions.length?`${number(regions.length)} region${regions.length===1?'':'s'} indexed · no global denominator yet`:'No measured regions yet';
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
  $('warning').hidden=false;$('warning').textContent=warning;draw();
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

$('refresh').onclick=refresh;$('layer').onchange=draw;$('auto').onchange=()=>{if($('auto').checked)refresh()};
window.addEventListener('resize',draw);
$('map').addEventListener('mousemove',event=>{
  if(!view||!data)return;const rect=$('map').getBoundingClientRect();
  const lon=Math.round(((event.clientX-rect.left-view.x0)/view.mapWidth*360-180)*10)/10;
  const lat=Math.round((90-(event.clientY-rect.top-view.y0)/view.mapHeight*180)*10)/10;
  if(lon<-180||lon>180||lat<-90||lat>90)return;$('cell').textContent=`Approximate atlas coordinate ${lat}°, ${lon}°. No measurement is recorded here.`;
});
refresh();setInterval(()=>{if($('auto').checked&&!document.hidden)refresh()},10000);
