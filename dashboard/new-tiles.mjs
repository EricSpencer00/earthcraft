// Newly published 512 m tiles (tiles/v2/index.json on the dataset), shown on the map within
// minutes of upload. Each tile is one Minecraft region r.<rx>.<rz>.mca = 2 × 2 map cells of 256 m.
import {HF_REPO,WORLD_CAPTURED_UTC} from './anvil.mjs?v=tiles-1';

const ROOT=`https://huggingface.co/datasets/${HF_REPO}/resolve/main/tiles/v2/`;
export const TILES_INDEX_URL=ROOT+'index.json';
export const TILES_REFRESH_MS=3*60*1000;
const TILE=/^(-?\d{1,6})_(-?\d{1,6})$/;

// {count,last,tiles:Map(id→{rx,rz,quality,at})}; throws on anything that is not the index.
export function parseTilesIndex(value){
  if(value?.schema!=='earthcraft.tiles-index.v1'||!value.tiles||typeof value.tiles!=='object')throw Error('Unsupported tiles index.');
  const tiles=new Map();let last=0;
  for(const [id,entry] of Object.entries(value.tiles)){
    const m=TILE.exec(id);if(!m||!Array.isArray(entry))continue;
    const quality=Number(entry[0]),at=Number(entry[1])*1000;if(!Number.isFinite(quality)||!Number.isFinite(at))continue;
    tiles.set(id,{id,rx:Number(m[1]),rz:Number(m[2]),quality,at});if(at>last)last=at;
  }
  return {count:tiles.size,last,tiles};
}

export async function loadTilesIndex(fetcher=globalThis.fetch,now=Date.now()){
  // Bust browser and CDN caches about once a minute; the file changes with every tile batch.
  const response=await fetcher(TILES_INDEX_URL+'?t='+Math.floor(now/60000),{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(15000)});
  if(!response.ok){response.body?.cancel?.().catch(()=>{});throw Error('Tiles index unavailable.');}
  return parseTilesIndex(await response.json());
}

// The tile that holds a 256 m cell id "x_z", or null.
export function tileForCell(index,cellId){
  const m=TILE.exec(cellId||'');if(!index||!m)return null;
  return index.tiles.get(Math.floor(Number(m[1])/2)+'_'+Math.floor(Number(m[2])/2))||null;
}
export function cellsOfTile(tile){
  const out=[];for(const dz of [0,1])for(const dx of [0,1])out.push({x:tile.rx*2+dx,z:tile.rz*2+dz});return out;
}
// Region file of a tile; preferred over the installed world when it was published after the world was captured.
export function tileRegionUrl(tile){return `${ROOT}${tile.id}/region/r.${tile.rx}.${tile.rz}.mca`;}
export function prefersTile(tile){return tile.at>Date.parse(WORLD_CAPTURED_UTC);}

export function agoLabel(at,now=Date.now()){
  const minutes=Math.max(0,Math.round((now-at)/60000));
  if(minutes<1)return 'just now';
  if(minutes<60)return minutes+' min ago';
  const hours=Math.round(minutes/60);if(hours<48)return hours+(hours===1?' hour ago':' hours ago');
  return Math.round(hours/24)+' days ago';
}
export function counterLabel(index,now=Date.now()){
  if(!index?.count)return '';
  return `${index.count.toLocaleString()} new ${index.count===1?'tile':'tiles'} · last added ${agoLabel(index.last,now)}`;
}

// Block (x, z) → [lon, lat], fitted (quadratic least squares) to the cells the snapshot already
// places on the map. Accurate to metres across the Chicago area; null with too few cells.
export function fitProjection(rows,cellSize=256){
  const pts=rows.filter(r=>Number.isFinite(r.longitude)&&Number.isFinite(r.latitude)).map(r=>[(r.x+.5)*cellSize/1e5,(r.z+.5)*cellSize/1e5,r.longitude,r.latitude]);
  if(pts.length<12)return null;
  const terms=(x,z)=>[1,x,z,x*x,x*z,z*z];
  const solve=k=>{
    const n=6,a=Array.from({length:n},()=>Array(n+1).fill(0));
    for(const p of pts){const t=terms(p[0],p[1]);for(let i=0;i<n;i++){for(let j=0;j<n;j++)a[i][j]+=t[i]*t[j];a[i][n]+=t[i]*p[k];}}
    for(let c=0;c<n;c++){
      let best=c;for(let r=c+1;r<n;r++)if(Math.abs(a[r][c])>Math.abs(a[best][c]))best=r;
      [a[c],a[best]]=[a[best],a[c]];if(Math.abs(a[c][c])<1e-12)return null;
      for(let r=0;r<n;r++)if(r!==c){const f=a[r][c]/a[c][c];for(let j=c;j<=n;j++)a[r][j]-=f*a[c][j];}
    }
    return a.map((row,i)=>row[n]/row[i]);
  };
  const lon=solve(2),lat=solve(3);if(!lon||!lat)return null;
  return (x,z)=>{const t=terms(x/1e5,z/1e5);return [t.reduce((s,v,i)=>s+v*lon[i],0),t.reduce((s,v,i)=>s+v*lat[i],0)];};
}
// A map record for a 256 m cell that is only in a new tile.
export function cellRecord(project,x,z,tile,cellSize=256){
  const west=x*cellSize,north=z*cellSize,east=west+cellSize,south=north+cellSize;
  const corners=[project(west,north),project(east,north),project(east,south),project(west,south)],[longitude,latitude]=project(west+cellSize/2,north+cellSize/2);
  return {id:x+'_'+z,tile_id:x+'_'+z,x,z,size_m:cellSize,state:'generated',geometry_state:'complete',appearance_state:tile.quality>0?'complete':'pending',
    longitude,latitude,corners_lonlat:corners,new_tile:tile.id};
}
