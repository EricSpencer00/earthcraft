import {sha256,boundedBytes,validatePublicRelease} from './hf-index.mjs';
const check=(ok,message)=>{if(!ok)throw Error(message);};
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
export const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a.localeCompare(b))):x);
const digest=v=>sha256(new TextEncoder().encode(canonical(v)));
const equal=(a,b)=>canonical(a)===canonical(b);
export function polygonContains(corners,{lng,lat}){let inside=false;for(let i=0,j=corners.length-1;i<corners.length;j=i++){const a=corners[i],b=corners[j];if((a[1]>lat)!==(b[1]>lat)&&lng<(b[0]-a[0])*(lat-a[1])/(b[1]-a[1])+a[0])inside=!inside;}return inside;}
export async function decodeSourceBoundV2(value,manifest){
 check(value?.schema==='earthcraft.source-bound-building-crop.v2'&&manifest?.schema==='earthcraft.source-bound-building-manifest.v2','Unsupported public v2 crop/manifest schema.');
 check(value.publication_allowed===true&&manifest.publication_allowed===true,'Preview is not approved for publication.');
 const {origin,size,palette,blocks}=value;
 check(Array.isArray(origin)&&origin.length===3&&origin.every(Number.isSafeInteger)&&Array.isArray(size)&&size.length===3&&[16,32].includes(size[0])&&[16,32].includes(size[2])&&Number.isInteger(size[1])&&size[1]>0&&size[1]<=384,'Invalid v2 crop bounds.');
 const frame=manifest.frame;
 check(Number.isInteger(frame?.dimension_min_y)&&Number.isInteger(frame.dimension_height)&&frame.dimension_height>0&&frame.dimension_height<=1024&&origin[1]>=frame.dimension_min_y&&origin[1]+size[1]<=frame.dimension_min_y+frame.dimension_height,'Invalid v2 frame limits.');
 check(hash(value.frame_sha256)&&value.frame_sha256===manifest.frame_sha256&&await digest(frame)===manifest.frame_sha256,'V2 frame checksum mismatch.');
 check(hash(value.generation?.id_sha256)&&value.generation.id_sha256===manifest.generation_id_sha256&&await digest(manifest.generation_identity)===manifest.generation_id_sha256,'V2 tile generation mismatch.');
 check(hash(value.generation.dataset_id_sha256)&&value.generation.dataset_id_sha256===manifest.dataset_generation_id_sha256&&await digest(manifest.dataset_generation_identity)===manifest.dataset_generation_id_sha256,'V2 dataset generation mismatch.');
 const identity=manifest.dataset_generation_identity;
 check(identity.frame_sha256===manifest.frame_sha256&&identity.plan_sha256===manifest.plan_sha256&&identity.model_sha256===manifest.model?.sha256&&identity.rights_decision_sha256===manifest.source_rights_review?.decision_sha256,'V2 dataset identity bindings differ.');
 check(manifest.generation_identity.dataset_generation_id_sha256===manifest.dataset_generation_id_sha256&&manifest.generation_identity.model_sha256===identity.model_sha256&&equal(origin,manifest.generation_identity.origin)&&equal(size,manifest.generation_identity.size),'V2 crop generation bounds differ.');
 check(Array.isArray(palette)&&palette.length>0&&palette.length<=256&&palette.every(n=>typeof n==='string'&&n.length<=256&&/^minecraft:[a-z0-9_]+(?:\[[a-z0-9_=,]+\])?$/.test(n)),'Invalid v2 palette.');
 check(Array.isArray(blocks)&&blocks.length<=32768&&value.occupied_total===blocks.length,'Invalid v2 position count.');
 check(Array.isArray(value.block_source_flags)&&value.block_source_flags.length===blocks.length&&value.block_source_flags.every(f=>Number.isInteger(f)&&f>0&&(f&~31)===0),'Invalid v2 source flags.');
 check(Array.isArray(value.block_rgb)&&value.block_rgb.length===blocks.length&&value.block_rgb.every(c=>Array.isArray(c)&&c.length===3&&c.every(n=>Number.isInteger(n)&&n>=0&&n<=255)),'Invalid v2 display colors.');
 const positions=new Map();for(let i=0;i<blocks.length;i++){const b=blocks[i];check(Array.isArray(b)&&b.length===4&&b.every(Number.isInteger)&&b.slice(0,3).every((n,a)=>n>=0&&n<size[a])&&b[3]>=0&&b[3]<palette.length,'Invalid v2 position.');const key=b.slice(0,3).join(',');check(!positions.has(key),'Duplicate v2 position.');positions.set(key,value.block_source_flags[i]);}
 const coverage=manifest.coverage;
 check(equal(value.coverage,coverage)&&coverage.facade_measured===false&&coverage.interiors_measured===false,'V2 coverage or measured facade claim differs.');
 const labels=['direct','interpolated','fitted','unknown'],counts=Object.fromEntries(labels.map(k=>[k,0])),columns=value.roof_support_columns,seen=new Set();
 check(Array.isArray(columns)&&columns.length<=size[0]*size[2]&&columns.length===coverage.mapped_roof_cells,'Invalid v2 roof support count.');
 for(const c of columns){
  check(Array.isArray(c.local_xz)&&c.local_xz.length===2&&c.local_xz.every((n,a)=>Number.isInteger(n)&&n>=0&&n<size[a===0?0:2])&&labels.includes(c.roof_support),'Invalid v2 roof support column.');
  const key=c.local_xz.join(',');check(!seen.has(key),'Duplicate roof support column.');seen.add(key);counts[c.roof_support]++;
  if(c.roof_support==='unknown'){check(c.roof_y===null,'Unknown roof must not claim a height.');continue;}
  check(Number.isInteger(c.roof_y)&&c.roof_y>=origin[1]&&c.roof_y<origin[1]+size[1],'Invalid supported roof height.');
  const flag=positions.get([c.local_xz[0],c.roof_y-origin[1],c.local_xz[1]].join(','));check(flag!==undefined&&(flag&(c.roof_support==='direct'?2:8)),'Roof support has no corresponding flagged block.');
  if(c.roof_support==='direct')check(typeof c.observation_id==='string'&&c.observation_id.length>0&&c.observation_id.length<=256&&hash(c.source_asset_sha256),'Missing direct observation provenance.');
  else check(c.model_id===manifest.model.id&&c.inside_model_domain===true&&Number.isFinite(c.uncertainty_m)&&c.uncertainty_m>0&&Number.isFinite(c.support_distance_m)&&c.support_distance_m>=0,'Missing reconstructed roof model/uncertainty.');
 }
 for(const k of labels)check(Number.isSafeInteger(coverage['roof_cells_'+k])&&coverage['roof_cells_'+k]===counts[k],'V2 roof support totals differ.');
 check(Number.isSafeInteger(coverage.terrain_cells)&&coverage.terrain_cells>=0&&value.block_source_flags.filter(f=>f&1).length===coverage.terrain_cells&&value.block_source_flags.filter(f=>f&16).length===coverage.inferred_shell_voxels,'V2 terrain/shell disclosure differs.');
 const corners=value.wgs84_corners;check(equal(corners,manifest.wgs84_corners_lon_lat)&&Array.isArray(corners)&&corners.length===4&&corners.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)&&Math.abs(p[0])<=180&&Math.abs(p[1])<=90),'Invalid v2 geographic footprint.');
 check(Math.abs(corners.reduce((s,p,i)=>{const q=corners[(i+1)%4];return s+p[0]*q[1]-q[0]*p[1];},0))>1e-12,'Degenerate v2 footprint.');
 check(Array.isArray(value.attribution)&&value.attribution.length>0&&value.attribution.length<=32&&value.attribution.every(a=>typeof a.source==='string'&&a.source.length<=256&&typeof a.license==='string'&&a.license.length<=256),'Missing v2 attribution.');
 return {...value,source_bound:true,evidence:coverage,quality_disclosure:`${size[0]} × ${size[2]} m source-bound crop. Roof cells: ${counts.direct} direct, ${counts.interpolated} interpolated, ${counts.fitted} fitted, ${counts.unknown} unknown. Blue facade shell and materials are illustrative; interiors are not measured. Absolute game Y datum lineage remains unresolved.`};
}
export class SourceBoundV2Client{
 constructor(pin){validatePublicRelease(pin);check(pin.format==='source-bound-json-v2'&&hash(pin.index_sha256)&&hash(pin.dataset_generation_id_sha256)&&hash(pin.rights_decision_sha256)&&hash(pin.visual_review_sha256),'Missing immutable v2 release pins.');this.pin=pin;this.cache=new Map();this.manifests=new Map();}
 url(path){check(typeof path==='string'&&path.length<=256&&/^[a-zA-Z0-9_./-]+\.json$/.test(path)&&!path.startsWith('/')&&!path.split('/').includes('..'),'Unsafe v2 JSON path.');return `https://huggingface.co/datasets/${this.pin.repo}/resolve/${this.pin.revision}/${path}`;}
 async read(path,expected,limit,signal,expectedBytes){check(hash(expected),'Invalid v2 reference hash.');const bytes=await boundedBytes(await fetch(this.url(path),{signal,credentials:'omit',referrerPolicy:'no-referrer'}),limit,signal);check(expectedBytes===undefined||expectedBytes===bytes.length,'V2 byte count differs.');check(await sha256(bytes)===expected,'V2 checksum mismatch.');return {bytes,value:JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes))};}
 async connect(signal){
  const {value}=await this.read('index.json',this.pin.index_sha256,512*1024,signal);
  check(value.schema==='earthcraft.source-bound-preview-index.v2'&&value.format==='source-bound-json-v2'&&value.publication_allowed===true,'Unsupported public v2 index.');
  for(const k of ['frame_sha256','plan_sha256','dataset_generation_id_sha256','rights_decision_sha256'])check(value[k]===this.pin[k],'V2 index release binding differs: '+k);
  check(value.provenance?.sha256===this.pin.provenance_sha256&&['terrain','buildings'].every(l=>value.content_layers?.includes(l)),'V2 provenance/layers differ.');
  for(const [path,pin] of [['qa/geometry-reference.json','geometry_reference_sha256'],['qa/frontend-geometry-qa.json','geometry_report_sha256'],['qa/visual-review.json','visual_review_sha256']])check(value.qa?.[path]?.sha256===this.pin[pin],'V2 QA evidence pin differs.');
  check(Array.isArray(value.tiles)&&value.tiles.length>0&&value.tiles.length<=1000,'V2 tile inventory exceeds bounds.');const ids=new Set();
  for(const tile of value.tiles){check(typeof tile.id==='string'&&tile.id.length<=128&&!ids.has(tile.id)&&hash(tile.crop_sha256)&&hash(tile.manifest_sha256)&&hash(tile.generation_id_sha256)&&Array.isArray(tile.origin)&&tile.origin.length===3&&tile.origin.every(Number.isSafeInteger)&&Array.isArray(tile.size)&&tile.size.length===3&&[16,32].includes(tile.size[0])&&[16,32].includes(tile.size[2])&&Number.isInteger(tile.size[1])&&tile.size[1]>0&&tile.size[1]<=384,'Invalid v2 tile reference.');this.url(tile.crop_path);this.url(tile.manifest_path);ids.add(tile.id);}
  this.index=value;return value.tiles.length;
 }
 async manifest(tile,signal){
  if(this.manifests.has(tile.id))return this.manifests.get(tile.id);
  const {value:m}=await this.read(tile.manifest_path,tile.manifest_sha256,128*1024,signal);
  check(m.schema==='earthcraft.source-bound-building-manifest.v2'&&m.publication_allowed===true&&m.crop?.path===tile.crop_path&&m.crop.sha256===tile.crop_sha256&&Number.isSafeInteger(m.crop.bytes)&&m.crop.bytes>0&&m.crop.bytes<=512*1024&&m.generation_id_sha256===tile.generation_id_sha256,'V2 index/manifest binding differs.');
  for(const k of ['frame_sha256','plan_sha256','dataset_generation_id_sha256'])check(m[k]===this.index[k],'V2 manifest release identity differs.');
  check(equal(m.generation_identity?.origin,tile.origin)&&equal(m.generation_identity?.size,tile.size)&&m.source_rights_review?.distribution_decision_id===this.pin.rights_decision_id&&m.source_rights_review.distribution_license===this.pin.distribution_license&&m.source_rights_review.decision_sha256===this.pin.rights_decision_sha256,'V2 bounds/rights binding differs.');
  for(const path of ['qa/geometry-reference.json','qa/frontend-geometry-qa.json','qa/visual-review.json'])check(equal(m.release_qa?.[path],this.index.qa[path]),'V2 manifest QA binding differs.');
  check(Array.isArray(m.wgs84_corners_lon_lat)&&m.wgs84_corners_lon_lat.length===4&&m.wgs84_corners_lon_lat.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)&&Math.abs(p[0])<=180&&Math.abs(p[1])<=90),'Invalid v2 manifest footprint.');
  this.manifests.set(tile.id,m);return m;
 }
 async coverage(signal){check(this.index.tiles.length<=64,'This release requires a paged coverage manifest.');let next=0;const rows=[];await Promise.all([0,1].map(async()=>{while(next<this.index.tiles.length){signal?.throwIfAborted();const tile=this.index.tiles[next++],manifest=await this.manifest(tile,signal);rows.push({...tile,corners:manifest.wgs84_corners_lon_lat});}}));return rows.sort((a,b)=>a.origin[2]-b.origin[2]||a.origin[0]-b.origin[0]);}
 async crop(tile,signal){
  signal?.throwIfAborted();if(this.cache.has(tile.id)){const cached=this.cache.get(tile.id);this.cache.delete(tile.id);this.cache.set(tile.id,cached);return cached;}
  const manifest=await this.manifest(tile,signal),{value,bytes}=await this.read(tile.crop_path,tile.crop_sha256,512*1024,signal,manifest.crop.bytes),crop=await decodeSourceBoundV2(value,manifest);
  signal?.throwIfAborted();check(equal(crop.origin,tile.origin)&&equal(crop.size,tile.size),'V2 crop/index bounds differ.');crop.transport={repo:this.pin.repo,revision:this.pin.revision,crop_path:tile.crop_path,crop_sha256:tile.crop_sha256,crop_bytes:bytes.length,manifest_path:tile.manifest_path,manifest_sha256:tile.manifest_sha256};
  this.cache.set(tile.id,crop);while(this.cache.size>2)this.cache.delete(this.cache.keys().next().value);return crop;
 }
}
