// Public metadata only. Publication history and host telemetry have independent clocks.
export const REPO='EricSpencer00/earthcraft';
export const HUB='https://huggingface.co';
const TILE=/^-?\d{1,6}_-?\d{1,6}$/;
const SHA=/^[a-f0-9]{40}$/;
const ROOT=`${HUB}/datasets/${REPO}`;
export const FEED_URL=`${HUB}/api/datasets/${REPO}/commits/main?limit=20`;
export const STATUS_URL=`${ROOT}/resolve/main/status/pipeline.json`;

export function publications(value){
  const list=Array.isArray(value)?value:value?.commits;
  if(!Array.isArray(list))throw Error('Publication history is unavailable.');
  return list.flatMap(entry=>{
    const m=/^Generate tiles\/v2: (\d+) tile\(s\) (.+?)(?:: batch|$)/.exec(entry?.title||'');
    const revision=entry.id||entry.oid,at=Date.parse(entry.date||entry.created_at);
    if(!m||!SHA.test(revision||'')||!Number.isFinite(at))return [];
    const tiles=m[2].split(',').map(x=>x.trim());
    if(!tiles.length||tiles.length>400||!tiles.every(x=>TILE.test(x)))return [];
    return [{revision,at,tiles,count:tiles.length}];
  }).sort((a,b)=>b.at-a.at);
}

export function hostState(status,now=Date.now()){
  const at=Date.parse(status?.time_utc),age=now-at;
  if(status?.schema!=='earthcraft.pipeline-status.v1'||!Number.isFinite(age)||age< -300000)return {state:'unknown',label:'Unavailable'};
  if(age>90*60000)return {state:'stale',label:'Older report',at};
  if(status.service!=='active')return {state:'stopped',label:'Reported '+String(status.service||'unknown'),at};
  return {state:'recent',label:'Reported active',at};
}

export function tileSource(manifest,batch,tile=batch.tiles[0]){
  if(!SHA.test(batch.revision)||!TILE.test(tile)||!batch.tiles.includes(tile))throw Error('Invalid tile publication.');
  const [x,z]=tile.split('_').map(Number);
  if(manifest?.schema!=='earthcraft.generated-tile.v2'||manifest.tile!==tile||manifest.region?.x!==x||manifest.region?.z!==z)throw Error('The tile manifest does not match this publication.');
  const region=manifest.files?.find(f=>f.role==='region');
  // Tiles are published packed (.mcp); ones from before packing carry a raw .mca.
  const packed=region?.path===`tiles/v2/${tile}/region/r.${x}.${z}.mcp`;
  if(!packed&&region?.path!==`tiles/v2/${tile}/region/r.${x}.${z}.mca`||!Number.isFinite(region.bytes)||region.bytes<=0||!/^[a-f0-9]{64}$/.test(region.sha256||''))throw Error('The published region is unavailable.');
  const base=`${ROOT}/resolve/${batch.revision}/tiles/v2/${tile}/region/`;
  const query=new URLSearchParams({base,layout:packed?'flat-packed':'flat',region:`${x},${z}`,radius:'3',view:'iso'});
  return {base,href:`./blocks.html?${query}`,x:x*512+256,z:z*512+256,tile};
}

async function jsonPage(url,limit=2*1024*1024){
  const response=await fetch(url,{cache:'no-cache',credentials:'omit',signal:AbortSignal.timeout(20000)});
  if(!response.ok){response.body?.cancel().catch(()=>{});throw Error(`Feed request failed (${response.status}).`);}
  const reader=response.body.getReader(),chunks=[];let length=0;
  try{
    while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>limit)throw Error('The feed exceeded its size limit.');chunks.push(value);}
  }catch(error){await reader.cancel().catch(()=>{});throw error;}
  const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
  return {data:JSON.parse(new TextDecoder().decode(bytes)),link:response.headers.get('Link')||''};
}
async function json(url,limit){return (await jsonPage(url,limit)).data;}

// List directory names only, at the same immutable publication as the preview.
// Ten bounded pages cap work; beyond that the result is explicitly a lower bound.
export async function publishedTileCount(revision,read=jsonPage){
  if(!SHA.test(revision))throw Error('Invalid publication revision.');
  const prefix=`${HUB}/api/datasets/${REPO}/tree/${revision}/tiles/v2`;
  let url=prefix+'?recursive=false&expand=false&limit=1000',pages=0;
  const tiles=new Set();
  while(url&&pages<10){
    const {data,link}=await read(url,512*1024);
    if(!Array.isArray(data)||data.length>1000)throw Error('Invalid published-tile listing.');
    for(const entry of data){
      const tile=String(entry.path||'').slice('tiles/v2/'.length);
      if(entry.type==='directory'&&entry.path===`tiles/v2/${tile}`&&TILE.test(tile))tiles.add(tile);
    }
    const next=/<([^>]+)>;\s*rel="next"/.exec(link)?.[1];
    if(next){
      const continuation=new URL(next);
      if(continuation.origin!==HUB||continuation.username||continuation.password||
          decodeURIComponent(continuation.pathname)!==new URL(prefix).pathname)throw Error('Unexpected tile-list continuation.');
    }
    url=next;pages++;
  }
  return {count:tiles.size,complete:!url};
}
export const dateLabel=at=>new Date(at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
export function ageLabel(at,now=Date.now()){
  const minutes=Math.max(0,Math.floor((now-at)/60000));
  return minutes<1?'just now':minutes<60?`${minutes} min ago`:minutes<1440?`${Math.floor(minutes/60)} h ago`:`${Math.floor(minutes/1440)} d ago`;
}
const count=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value.toLocaleString():'Unavailable';

export function installPipeline(doc=document){
  const $=id=>doc.getElementById(id);
  let busy=false,current=null,preview=null,drawGeneration=0,shown='';
  const text=(id,value)=>{$(id).textContent=value;};
  const link=(id,href)=>{$(id).href=href;$(id).hidden=false;};
  function host(status){
    const health=hostState(status),valid=health.state!=='unknown';
    text('worker-state',health.label);$('worker-state').dataset.state=health.state;
    text('worker-report',valid?`Report from ${dateLabel(health.at)} (${ageLabel(health.at)}). Reports arrive with tile batches and can lag publications.`:'The worker report is unavailable. Publication history can still show progress.');
    for(const [id,field] of [['rate-hour','tiles_last_hour'],['rate-day','tiles_last_24h'],['worker-count','workers']])text(id,valid?count(status[field]):'Unavailable');
    text('worker-disk',valid&&Number.isFinite(status.disk?.free_bytes)?`${(status.disk.free_bytes/2**30).toFixed(1)} GiB`:'Unavailable');
    const list=$('pipeline-stages');list.replaceChildren();
    for(const [key,label] of ['Source data','Terrain & buildings','Surface detail','Minecraft checks'].entries()){
      const row=status?.journal?.[key],li=doc.createElement('li'),name=doc.createElement('span'),small=doc.createElement('small'),total=doc.createElement('strong');
      name.textContent=label;small.textContent=valid&&row?`${count(row.running??0)} in progress · ${count(row.pending??0)} queued`:'No current journal data';
      name.append(small);total.textContent=valid&&row?`${count(row.complete??0)} done`:'Unavailable';li.append(name,total);list.append(li);
    }
  }
  async function draw(source){
    const generation=++drawGeneration;
      $('preview-retry').hidden=true;text('preview-status','Reading the published blocks…');
      try{
        preview??=await import('./cell-preview.mjs?v=lake-1');
        if(generation!==drawGeneration)return;
        const result=await preview.showCell($('tile-canvas'),{...source,radius:2,layout:'flat'});
        if(generation!==drawGeneration)return;
        text('preview-status',result.status==='done'?'80 × 80 m excerpt · drag to turn · scroll to zoom':result.status==='empty'?'This excerpt has no stored chunks. Open the tile to explore another area.':'Preview interrupted.');
      }catch{if(generation===drawGeneration){text('preview-status','The block preview could not load. Tile details and source receipts are still available.');$('preview-retry').hidden=false;}}
  }
  function history(batches){
    const list=$('recent-publications');list.replaceChildren();
    for(const batch of batches.slice(0,6)){
      const li=doc.createElement('li'),a=doc.createElement('a'),name=doc.createElement('span'),time=doc.createElement('time');
      a.href=`${ROOT}/commit/${batch.revision}`;name.textContent=`${batch.count} tile${batch.count===1?'':'s'} · ${batch.tiles[0]}${batch.count>1?' + more':''}`;
      time.dateTime=new Date(batch.at).toISOString();time.textContent=ageLabel(batch.at);time.title=dateLabel(batch.at);a.append(name,time);li.append(a);list.append(li);
    }
  }
  async function refresh(){
    if(busy)return;busy=true;$('pipeline-refresh').disabled=true;$('pipeline-error').hidden=true;
    try{
      const [commits,status]=await Promise.allSettled([json(FEED_URL),json(STATUS_URL,64*1024)]);
      host(status.status==='fulfilled'?status.value:null);
      if(commits.status==='rejected')throw Error('Publication history could not be loaded. Try Refresh in a moment.');
      const batches=publications(commits.value);history(batches);
      if(!batches.length)throw Error('No recent tile publication was found in this feed.');
      const latest=batches[0],tile=latest.tiles[0];
      text('publication-state',`Latest batch: ${latest.count} tile${latest.count===1?'':'s'}, published ${ageLabel(latest.at)}. ${Date.now()-latest.at>3*3600000?'No recent publication; the pipeline may need attention.':''}`);
      if(shown!==latest.revision){
        const manifest=await json(`${ROOT}/resolve/${latest.revision}/tiles/v2/${tile}/tile.json`,512*1024);
        current=tileSource(manifest,latest,tile);shown=latest.revision;
        text('published-tiles','Checking');
        void publishedTileCount(latest.revision).then(result=>{
          if(shown!==latest.revision)return;
          text('published-tiles',(result.complete?'':'At least ')+count(result.count));
          text('published-basis',`Distinct 512 × 512 m v2 tile folders at publication ${latest.revision.slice(0,8)}. Legacy v1 packages and saved-world declarations are separate; folders are not a full integrity audit.`);
        }).catch(()=>{if(shown===latest.revision){text('published-tiles','Unavailable');text('published-basis','The published tile count could not be read. Journal counts are not a substitute.');}});
        text('tile-id',tile);text('tile-published',dateLabel(latest.at));
        text('tile-description',`An excerpt from tile ${tile} in the latest published batch. Each tile covers 512 × 512 m; this view shows its centre.`);
        text('tile-quality',({LQ:'Base terrain','scan-roof':'LiDAR roofs','scan-points-and-roof':'LiDAR points & roofs','classified-scan-points':'Classified LiDAR points'})[manifest.quality]||'Not recorded');
        const seconds=manifest.generator?.seconds;text('tile-duration',Number.isFinite(seconds)?`${(seconds/60).toFixed(1)} min`:'Not recorded');
        link('tile-open',current.href);link('tile-manifest',`${ROOT}/blob/${latest.revision}/tiles/v2/${tile}/tile.json`);link('tile-commit',`${ROOT}/commit/${latest.revision}`);
        $('tile-canvas').setAttribute('aria-label',`Minecraft blocks from tile ${tile}. Drag to turn and scroll to zoom.`);
        // Metadata refresh does not wait for the renderer or repeatedly reload an unchanged tile.
        void draw(current);
      }
      text('checked-at',`Checked ${dateLabel(Date.now())}. Updates every two minutes while this page is visible.`);
    }catch(error){text('pipeline-error',error.message);$('pipeline-error').hidden=false;if(!current)text('preview-status','Tile preview is waiting for publication data.');}
    finally{busy=false;$('pipeline-refresh').disabled=false;doc.querySelector('.latest-tile').setAttribute('aria-busy','false');}
  }
  $('pipeline-refresh').addEventListener('click',refresh);
  $('preview-retry').addEventListener('click',()=>{if(current)void draw(current);});
  void refresh();
  const timer=setInterval(()=>{if(!doc.hidden)void refresh();},120000);
  globalThis.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
}
if(typeof document!=='undefined'&&document.getElementById('pipeline'))installPipeline();
