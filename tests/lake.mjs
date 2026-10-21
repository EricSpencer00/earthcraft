// Lake tiles built in the browser (dashboard/lake.mjs): bed from bathymetry, water to y=60.
import assert from 'node:assert/strict';
import {LakeSource,LakeClient,LAKE_SURFACE_Y} from '../dashboard/lake.mjs';

// A 4x4 grid around the frame origin, 50 m deep: bed at y=10, water y=11..60.
const projection={method:'transverse_mercator_wgs84',lat_0:41.8972,lon_0:-87.62443,k:1,west:-32,north:33};
const header={width:4,height:4,west:-87.64443,north:41.9172,step_deg:0.01,scale:0.1};
const source=new LakeSource({header,grid:new Int16Array(16).fill(-500),tiles:new Set(['0_0']),projection});
assert.equal(source.bed(10,10),10);
const sections=new Map(source.chunk(0,0).sections.map(s=>[s.y,s]));
const at=(y,i=0)=>{const s=sections.get(y>>4);return s?s.palette[s.indices?s.indices[(y&15)*256+i]:0].name:'(not stored)';};
assert.equal(at(9),'minecraft:sand');
assert.equal(at(10),'minecraft:sand');
assert.equal(at(8),'minecraft:air','one block below the two-block bed is empty');
assert.equal(at(11),'minecraft:water');
assert.equal(sections.get(1).indices,null,'y 16..31 is a single-block water section');
assert.equal(at(LAKE_SURFACE_Y),'minecraft:water');
assert.equal(at(LAKE_SURFACE_Y+1),'minecraft:air');
assert.equal(at(-20),'(not stored)','nothing is stored below the bed section');
// Chunks outside lake tiles are reported like a missing region.
const client=new LakeClient({base:'https://example.test/lake/v1/'});
client.source=Promise.resolve(source);
assert.deepEqual((await client.loadChunks([[0,0],[40,0]])).map(r=>r.status),['ok','no-region']);
console.log('Lake tiles: bed from bathymetry, water to the surface, no-region outside the lake passed');
