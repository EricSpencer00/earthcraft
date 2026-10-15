import {decodeSourceBoundV2} from './source-bound-v2.mjs';
import {decodeCrop,drawCrop,MAX_BYTES} from './block-preview.mjs?v=hf-v2-1';
const canonical=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const hash=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
export async function readLocalCrop(cropFile,manifestFile){
 if(!cropFile||!manifestFile)throw Error('Choose both a crop JSON file and its manifest.');
 if(cropFile.size>MAX_BYTES||manifestFile.size>65536)throw Error('Crop limit is 512 KiB; manifest limit is 64 KiB.');
 const [bytes,manifestBytes]=await Promise.all([cropFile.arrayBuffer(),manifestFile.arrayBuffer()]);
 const manifest=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(manifestBytes));
 const digest=await hash(bytes);
 if(!manifest.crop||manifest.crop.sha256!==digest||manifest.crop.bytes!==bytes.byteLength)throw Error('Crop bytes or checksum do not match this manifest.');
 const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
 for(const key of ['origin','size','generation'])if(canonical(value[key])!==canonical(manifest[key]))throw Error('Crop '+key+' does not match this manifest.');
 const audience=value.schema==='earthcraft.private-review-block-crop.v1'?'private-review':'public';
 const crop=decodeCrop(value,{audience,dimensionMinY:manifest.dimension_min_y??-64,dimensionHeight:manifest.dimension_height??384});
 return {crop,digest,manifestDigest:await hash(manifestBytes)};
}
export function installLocalFiles(root){
 let crop=null,rotation=0,sequence=0;
 const find=id=>root.querySelector('#'+id),status=find('local-status');
 find('local-load').onclick=async()=>{
  const current=++sequence;crop=null;find('local-canvas').hidden=true;find('local-turn').hidden=true;find('local-detail').textContent='';status.textContent='Checking local files…';
  try{
   const result=await readLocalCrop(find('local-crop').files[0],find('local-manifest').files[0]);if(current!==sequence)return;
   crop=result.crop;rotation=0;drawCrop(find('local-canvas'),crop);find('local-canvas').hidden=false;find('local-turn').hidden=false;
   status.textContent=`Local ${crop.source_bound?'source-bound crop':'tile '+crop.generation.tile}: ${crop.blocks.length.toLocaleString()} recorded blocks. Files stayed in this browser. ${crop.publication_allowed===false?'Private data; public distribution is not approved.':''}`;
   find('local-detail').textContent=`World origin: ${crop.origin.join(', ')}. Crop size: ${crop.size.join(' × ')}. Coverage: ${crop.source_bound?crop.quality_disclosure:crop.coverage}. Generation: ${crop.source_bound?crop.generation.id_sha256:crop.generation.repo+'@'+crop.generation.revision}. Crop SHA-256: ${result.digest}. Manifest SHA-256: ${result.manifestDigest}. Material colors are illustrative. Source records: ${crop.attribution.map(a=>a.source+' ('+a.license+')').join('; ')}. File hashes verify the selected pair; source authenticity requires your trusted manifest.`;
  }catch(error){if(current===sequence)status.textContent='Could not open local data: '+error.message;}
 };
 find('local-turn').onclick=()=>{if(crop)drawCrop(find('local-canvas'),crop,rotation=(rotation+1)%4);};
 find('local-clear').onclick=()=>{sequence++;crop=null;find('local-crop').value='';find('local-manifest').value='';find('local-canvas').hidden=true;find('local-turn').hidden=true;find('local-detail').textContent='';status.textContent='Local files cleared from this viewer.';};
}
