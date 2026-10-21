// The published-tile bitmap from the AWS status file decodes to the same tile ids.
import assert from 'node:assert/strict';
import {deflateSync} from 'node:zlib';
import {decodeTileBitmap} from '../dashboard/fresh-tiles.mjs';

const tiles=[[-3,2],[0,0],[4,-1],[4,5]],x0=-3,z0=-1,width=8,height=7;
const bits=new Uint8Array(Math.ceil(width*height/8));
for(const [x,z] of tiles){const i=(z-z0)*width+(x-x0);bits[i>>3]|=1<<(i&7);}
const map={x0,z0,width,height,bitmap:deflateSync(bits).toString('base64')};
assert.deepEqual((await decodeTileBitmap(map)).sort(),tiles.map(([x,z])=>`${x}_${z}`).sort());
assert.deepEqual(await decodeTileBitmap(null),[]);
assert.deepEqual(await decodeTileBitmap({...map,width:1e5,height:1e5}),[]);
console.log('Published-tile bitmap decodes to the published tiles passed');
