// Browse only the already public atlas record. No dataset or user-file uploads.
export function cellRecord(atlas,index,snapshot){
 const size=atlas.area.cell_size_blocks,x=atlas.area.left_block+(index%atlas.area.width_cells)*size,z=atlas.area.top_block+Math.floor(index/atlas.area.width_cells)*size;
 const source=atlas.records?.[index]||{},fields={};
 for(const key of ['latitude','longitude','source_state','geometry_state','appearance_state','game_verify_state','scan_upgrade_recorded_in_parent_tile'])if(['number','string','boolean'].includes(typeof source[key]))fields[key]=source[key];
 return {schema:'earthcraft.public-cell-record.v1',cell_id:atlas.cell_ids?.[index]||Math.floor(x/size)+'_'+Math.floor(z/size),view:atlas.area.id,world_bounds_xz:[x,z,x+size,z+size],size_m:size,record:fields,provenance:{basis:atlas.area.id==='world'?snapshot?.generated_world?.basis:'published_progress_snapshot',snapshot_updated_utc:snapshot?.updated_utc||null,public_snapshot:'./progress/earth.json',...(atlas.area.id==='world'?{manifest_sha256:snapshot?.generated_world?.manifest_sha256||null,frame_sha256:snapshot?.generated_world?.frame_sha256||null}:{})},limits:'Build record only. Not a block payload, current block-fill verification, or current generator progress.'};
}
export function queryCells(atlas,snapshot,{query='',filter='all',bounds=null,offset=0,limit=30}={}){
 if(!atlas)return {total:0,rows:[]};const needle=query.trim().toLowerCase().slice(0,80),rows=[];let total=0;
 for(let i=0;i<atlas.cells.length;i++){
  if(atlas.cells[i]<0||!atlas.records?.[i])continue;
  const record=atlas.records[i];
  if(filter==='scan'&&!record.scan_upgrade_recorded_in_parent_tile)continue;
  if(filter==='unverified'&&['verified','complete'].includes(record.game_verify_state))continue;
  if(bounds&&(!Number.isFinite(record.latitude)||!Number.isFinite(record.longitude)||record.longitude<bounds.west||record.longitude>bounds.east||record.latitude<bounds.south||record.latitude>bounds.north))continue;
  if(needle){const size=atlas.area.cell_size_blocks,x=atlas.area.left_block+(i%atlas.area.width_cells)*size,z=atlas.area.top_block+Math.floor(i/atlas.area.width_cells)*size,id=atlas.cell_ids?.[i]||Math.floor(x/size)+'_'+Math.floor(z/size);if(!`${id} ${x}, ${z} ${record.latitude} ${record.longitude}`.toLowerCase().includes(needle))continue;}
  if(total>=offset&&rows.length<Math.min(limit,40))rows.push({index:i,value:cellRecord(atlas,i,snapshot)});total++;
 }
 return {total,rows};
}
export function installCellBrowser(root,{select,visibleBounds}){
 const find=id=>root.querySelector('#'+id);let atlas=null,snapshot=null,offset=0;
 const render=()=>{
  const bounds=find('browse-visible').checked?visibleBounds():null;
  const result=queryCells(atlas,snapshot,{query:find('browse-query').value,filter:find('browse-filter').value,bounds,offset});
  find('browse-results').replaceChildren();
  find('browse-summary').textContent=!atlas?'No cells to show yet. Refresh or choose another map view.':result.total?`${result.total.toLocaleString()} matching cells. Showing ${offset+1}–${Math.min(offset+30,result.total)}. ${bounds?'Only what’s on screen.':'Whole map.'}`:'No matching cells. Clear the search, move the map, or turn off Only what’s on screen.';
  for(const row of result.rows){const item=document.createElement('li'),button=document.createElement('button'),detail=document.createElement('span');button.type='button';button.textContent='Cell '+row.value.cell_id.replace('_',', ');detail.textContent=`${row.value.size_m} m · X ${row.value.world_bounds_xz[0]}, Z ${row.value.world_bounds_xz[1]}${row.value.record.scan_upgrade_recorded_in_parent_tile?' · extra scan detail':''}`;button.append(detail);button.onclick=()=>select(row.index);item.append(button);find('browse-results').append(item);}
  find('browse-prev').disabled=offset===0;find('browse-next').disabled=offset+30>=result.total;
  find('browse-provenance').textContent=snapshot?`${snapshot.updated_utc?'Map updated '+new Date(snapshot.updated_utc).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'})+'. ':''}The map can lag behind the newest building work.`:'';
 };
 for(const id of ['browse-query','browse-filter','browse-visible'])find(id).addEventListener(id==='browse-query'?'input':'change',()=>{offset=0;render();});
 find('browse-prev').onclick=()=>{offset=Math.max(0,offset-30);render();};find('browse-next').onclick=()=>{offset+=30;render();};
 return {setData(a,s){atlas=a;snapshot=s;offset=0;render();},refresh(){offset=0;render();}};
}
