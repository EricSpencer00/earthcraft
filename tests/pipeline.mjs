import assert from 'node:assert/strict';
import {publications,hostState,tileSource,ageLabel} from '../dashboard/pipeline.mjs';
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
console.log('Pipeline: publication ordering, invalid data, stale telemetry, pinned tile paths and flat region layout passed');
