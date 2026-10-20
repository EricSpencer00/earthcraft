import assert from 'node:assert/strict';
import {publications,hostState,tileSource,ageLabel,publishedTileCount} from '../dashboard/pipeline.mjs';
import {RegionClient} from '../dashboard/anvil.mjs';
import {parseViewerParams} from '../dashboard/chunk-viewer.mjs';

const revision='a'.repeat(40),date='2026-10-18T12:00:00Z';
const batches=publications([
  {id:'b'.repeat(40),date:'2026-10-18T11:00:00Z',title:'Generate tiles/v2: 1 tile(s) 1_2: batch 1 (8 files) [hf_store]'},
  {id:revision,date,title:'Generate tiles/v2: 2 tile(s) -136_68, -130_68: batch 1 (16 files) [hf_store]'},
  {id:'c'.repeat(40),date,title:'Update status'},
  {id:revision,date,title:'Generate tiles/v2: 1 tile(s) ../../private: batch 1'},
]);
assert.equal(batches.length,2);assert.equal(batches[0].revision,revision);assert.deepEqual(batches[0].tiles,['-136_68','-130_68']);
assert.deepEqual(publications({commits:[]}),[]);assert.throws(()=>publications({oops:[]}),/unavailable/);
const now=Date.parse(date),status={schema:'earthcraft.pipeline-status.v1',time_utc:date,service:'active'};
assert.equal(hostState(status,now).state,'recent');
assert.equal(hostState(status,now+91*60000).state,'stale');
assert.equal(hostState({...status,service:'inactive'},now).state,'stopped');
assert.equal(hostState({...status,time_utc:'broken'},now).state,'unknown');
assert.equal(hostState(status,now-6*60000).state,'unknown');
assert.equal(hostState(null,now).state,'unknown');
assert.equal(ageLabel(now,now+120000),'2 min ago');
const manifest={schema:'earthcraft.generated-tile.v2',tile:'-136_68',region:{x:-136,z:68},files:[{role:'region',path:'tiles/v2/-136_68/region/r.-136.68.mca',bytes:4202496,sha256:'f'.repeat(64)}]};
const source=tileSource(manifest,batches[0]);assert.deepEqual([source.x,source.z],[-69376,35072]);
assert.ok(source.base.includes(revision));assert.ok(source.href.includes('layout=flat'));
assert.throws(()=>tileSource({...manifest,region:{x:1,z:2}},batches[0]),/match/);
assert.throws(()=>tileSource({...manifest,files:[{...manifest.files[0],path:'../../private'}]},batches[0]),/unavailable/);
const flat=new RegionClient({base:source.base,layout:'flat'}),bucketed=new RegionClient({base:source.base});
assert.ok(flat.url(-136,68).endsWith('/region/r.-136.68.mca'));
assert.ok(bucketed.url(-136,68).endsWith('/region/-9/r.-136.68.mca'));
assert.throws(()=>new RegionClient({base:source.base,layout:'other'}),/layout/);
const q=parseViewerParams(new URL(source.href,'http://localhost').search);
assert.equal(q.layout,'flat');assert.deepEqual(q.center,[-136*32+16,68*32+16]);
assert.throws(()=>parseViewerParams('?layout=unknown'),/layout/);
const countPrefix=`https://huggingface.co/api/datasets/EricSpencer00/earthcraft/tree/${revision}/tiles/v2`;
let pages=0;
const actual=await publishedTileCount(revision,async()=>++pages===1?{data:[{type:'directory',path:'tiles/v2/1_2'},{type:'file',path:'tiles/v2/not-a-tile'}],link:`<${countPrefix.replace('/tiles/v2','/tiles%2Fv2')}?cursor=next>; rel="next"`}:{data:[{type:'directory',path:'tiles/v2/-3_4'},{type:'directory',path:'tiles/v2/1_2'}],link:''});
assert.deepEqual(actual,{count:2,complete:true});assert.equal(pages,2);
const bounded=await publishedTileCount(revision,async()=>({data:[{type:'directory',path:'tiles/v2/1_2'}],link:`<${countPrefix}?cursor=next>; rel="next"`}));
assert.deepEqual(bounded,{count:1,complete:false});
await assert.rejects(publishedTileCount(revision,async()=>({data:[],link:'<https://example.com/>; rel="next"'})),/continuation/);
// New tiles index: parsing, cell mapping, counter, projection fit and region overrides.
{
  const {parseTilesIndex,tileForCell,cellsOfTile,tileRegionUrl,prefersTile,counterLabel,fitProjection,cellRecord}=await import('../dashboard/new-tiles.mjs');
  const index=parseTilesIndex({schema:'earthcraft.tiles-index.v1',tiles:{'-137_42':[0,1792342669],'1_2':[2,1792000000],'bad':[0,1]}});
  assert.equal(index.count,2);assert.equal(index.last,1792342669000);
  assert.throws(()=>parseTilesIndex({schema:'other'}),/Unsupported/);
  assert.equal(tileForCell(index,'-274_85')?.id,'-137_42');assert.equal(tileForCell(index,'-273_84')?.id,'-137_42');assert.equal(tileForCell(index,'-272_84'),null);
  assert.deepEqual(cellsOfTile(index.tiles.get('1_2')),[{x:2,z:4},{x:3,z:4},{x:2,z:5},{x:3,z:5}]);
  assert.ok(tileRegionUrl(index.tiles.get('-137_42')).endsWith('/resolve/main/tiles/v2/-137_42/region/r.-137.42.mca'));
  assert.ok(prefersTile(index.tiles.get('-137_42')));assert.ok(!prefersTile({at:Date.parse('2026-10-01T00:00:00Z')}));
  assert.equal(counterLabel(index,1792342669000+3*60000),'2 new tiles · last added 3 min ago');assert.equal(counterLabel(null),'');
  const rows=[];for(let x=-20;x<=20;x+=5)for(let z=-20;z<=20;z+=5)rows.push({x,z,longitude:-87.6+(x+.5)*256/83000,latitude:41.9-(z+.5)*256/111000});
  const project=fitProjection(rows);const [lon,lat]=project(-274*256+128,85*256+128);
  assert.ok(Math.abs(lon-(-87.6+(-274+.5)*256/83000))<1e-6&&Math.abs(lat-(41.9-(85+.5)*256/111000))<1e-6);
  const cell=cellRecord(project,-274,85,index.tiles.get('-137_42'));assert.equal(cell.id,'-274_85');assert.equal(cell.corners_lonlat.length,4);assert.equal(cell.new_tile,'-137_42');
  assert.equal(fitProjection(rows.slice(0,5)),null);
  const client=new RegionClient({base:'https://huggingface.co/datasets/o/r/resolve/x/world/region/'});
  client.setOverride(-137,42,tileRegionUrl(index.tiles.get('-137_42')));
  assert.equal(client.url(-137,42),tileRegionUrl(index.tiles.get('-137_42')));assert.ok(client.url(-136,42).endsWith('/world/region/-9/r.-136.42.mca'));
  assert.throws(()=>client.setOverride(0,0,'javascript:alert(1)'),/override/);
}
console.log('New tiles: index parsing, cells, counter, projection fit and region overrides passed');
console.log('Pipeline: publication ordering, invalid data, stale telemetry, pinned tile paths and flat region layout passed');
