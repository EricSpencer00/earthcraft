import assert from 'node:assert/strict';
import {cellRecord,queryCells} from '../dashboard/cell-browser.mjs';
const atlas={source:'published',area:{id:'world',cell_size_blocks:256,left_block:-512,top_block:-256,width_cells:2},cells:[2,2,-1,2],cell_ids:['-2_-1','-1_-1',null,'-1_0'],records:[{latitude:41,longitude:-88,geometry_state:'complete',scan_upgrade_recorded_in_parent_tile:true},{latitude:42,longitude:-87,game_verify_state:'verified'},null,{latitude:43,longitude:-86}]};
const snapshot={updated_utc:'2026-01-01',generated_world:{basis:'installed_save_tile_manifest',manifest_sha256:'a'.repeat(64)}};
assert.equal(queryCells(atlas,snapshot).total,3);assert.equal(queryCells(atlas,snapshot,{filter:'scan'}).total,1);assert.equal(queryCells(atlas,snapshot,{filter:'unverified'}).total,2);
assert.equal(queryCells(atlas,snapshot,{query:'-2_-1'}).rows[0].index,0);assert.equal(queryCells(atlas,snapshot,{bounds:{west:-89,east:-87.5,south:40,north:42}}).total,1);
assert.equal(queryCells(atlas,snapshot,{offset:2,limit:1}).rows.length,1);assert.deepEqual(cellRecord(atlas,0,snapshot).world_bounds_xz,[-512,-256,-256,0]);assert.match(cellRecord(atlas,0,snapshot).limits,/Not a block payload/);
assert.equal(queryCells(null,null).rows.length,0);console.log('Public cell browser filters, geographic bounds, pagination and honest record provenance passed.');
