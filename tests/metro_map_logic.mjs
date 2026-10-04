// Data and copy regressions run without a browser or private world access.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const elements=new Map();
function element(id){
  if(!elements.has(id))elements.set(id,{textContent:'',hidden:false,value:'',dataset:{}});
  return elements.get(id);
}
const context=vm.createContext({document:{getElementById:element,body:{dataset:{}},documentElement:{}},
  Date, URLSearchParams, AbortSignal, console});
const source=await readFile(new URL('../dashboard/app.js',import.meta.url),'utf8');
const logic=source.replace(/^import .*;\n/,'').split("$('map').addEventListener('pointermove'")[0].replaceAll('import.meta.url',JSON.stringify(new URL('../dashboard/app.js',import.meta.url).href));
vm.runInContext(logic+`\nconst localHost=true;
 globalThis.api={
  set(data,view='metro'){snapshot=data;$('area').value=view;atlas=publishedAtlas(data);return atlas;},
  render:renderSnapshot,
  legend(layer='build'){mapLayer=layer;renderLegend(true,atlas.counts,false);},
  read:readSnapshot,
  atlas(){return atlas;},snapshot(){return snapshot;}
 };`,context);
const {api}=context;
const data={source:'local',updated_utc:'2026-10-03T04:00:00Z',regions:[{label:'Chicagoland'}],
  cell_grid:{cell_size_m:512},rollup:{generated_tiles:1,state:'running'},local:{stages:{}},
  metro:{planned_tiles:2,installed_tiles:2,acknowledged_tiles:1,installed_scan_tiles:1,
    generation_state:'running',active_workers:2,delivery_state:'storage_boundary',queue_stale:false},
  cells:[{tile_id:'-52_-1',state:'generated',installed_in_save:true,appearance_state:'complete',
          scan_upgrade_recorded_in_parent_tile:true,photo_color_applied:false},
         {tile_id:'-51_-1',state:'queued',installed_in_save:true,appearance_state:'pending',
          scan_upgrade_recorded_in_parent_tile:false,photo_color_applied:false}],
  generated_world:{cell_size_m:256,unique_cells:1,photo_colored_cells:0,scan_upgrade_parent_tiles:1,
    cells:[{tile_id:'-103_-2',state:'generated',appearance_state:'pending',scan_upgrade_recorded_in_parent_tile:true}]}};

const atlas=api.set(data);
assert.equal(atlas.area.id,'metro');
assert.equal(atlas.area.left_block,-26624);
assert.equal(atlas.area.top_block,-512);
assert.equal(atlas.area.cell_size_blocks,512);
assert.equal(atlas.counts.green,1);
api.render();
assert.equal(element('geometry').textContent,'2'); // Save union, not publisher-only acknowledgment.
assert.equal(element('sources').textContent,'1');
assert.equal(element('appearance').textContent,'1');
assert.match(element('record-note').textContent,/Street-photo colors are pending/);
assert.match(element('warning').textContent,/storage limit/);
api.legend('appearance');
assert.equal(element('legend-source-label').textContent,'Awaiting scans');
assert.equal(element('legend-finished-label').textContent,'Scan upgrade installed');
assert.equal(element('legend-installed').hidden,true);
api.legend('build');
assert.equal(element('legend-queued-label').textContent,'Planned');
assert.equal(element('legend-installed').hidden,false);

api.set({...data,metro:{...data.metro,generation_state:'waiting_for_storage'}});
api.render();assert.match(element('warning').textContent,/Generation is waiting for storage/);
assert.doesNotMatch(element('warning').textContent,/Generation is running/);
api.set({...data,metro:{...data.metro,save_record_utc:'2026-10-03T03:00:00Z'}});
api.render();assert.match(element('updated').textContent,/Queue .* Save records/);
api.set({...data,metro:{...data.metro,queue_stale:true}});
api.render();const warning=element('warning').textContent;
api.render();assert.equal(element('warning').textContent,warning);
api.set({...data,metro:{...data.metro,delivery_state:'ready',queue_stale:false}});
api.render();assert.equal(element('warning').hidden,true);
assert.equal(element('warning').textContent,'');

const saved=api.set(data,'world');
assert.equal(saved.area.id,'world');
assert.equal(saved.area.left_block,-26368);
assert.equal(saved.area.cell_size_blocks,256);
api.set(data);
const before=api.atlas();
context.fetch=async()=>({ok:false,status:503});
await assert.rejects(api.read(),/metro records unavailable/);
assert.equal(api.atlas(),before);
const requests=[];
context.fetch=async path=>{
 requests.push(path);
 return path.includes('/api/metro')?{ok:false,status:404}:{ok:true,json:async()=>data};
};
await api.read();
assert.equal(api.atlas().area.id,'world');
assert.equal(element('area').value,'world');
assert.deepEqual(requests,['./api/metro?view=metro','./progress/earth.json']);
context.fetch=async()=>({ok:true,json:async()=>data});
await api.read();assert.equal(api.snapshot().source,'local');
console.log('Metro coordinates, independent save/generation counts, honest scan labels, stale warnings, and feed fallback passed');
