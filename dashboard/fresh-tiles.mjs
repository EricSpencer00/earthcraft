// Bitmaps in status/pipeline.json (scripts/aws_pipeline_setup.sh status) and progress/world.json
// (scripts/site_cache.py): base64(zlib(bits)), bit i = (z - z0) * width + (x - x0), least
// significant bit first. A few KB stand in for lists of hundreds of thousands of ids.
export async function decodeBits(map){
  if(!map?.bitmap||!Number.isSafeInteger(map.width*map.height)||map.width*map.height>16e6)return null;
  const packed=Uint8Array.from(atob(map.bitmap),c=>c.charCodeAt(0));
  const bits=new Uint8Array(await new Response(new Blob([packed]).stream().pipeThrough(new DecompressionStream('deflate'))).arrayBuffer());
  return {x0:map.x0,z0:map.z0,width:map.width,height:map.height,bits,
    has(x,z){x-=map.x0;z-=map.z0;if(x<0||z<0||x>=map.width||z>=map.height)return false;const i=z*map.width+x;return Boolean(bits[i>>3]>>(i&7)&1);},
    each(fn){for(let i=0;i<map.width*map.height;i++)if(bits[i>>3]>>(i&7)&1)fn(map.x0+i%map.width,map.z0+Math.floor(i/map.width));}};
}
export async function decodeTileBitmap(map){
  const decoded=await decodeBits(map),ids=[];
  decoded?.each((x,z)=>ids.push(`${x}_${z}`));
  return ids;
}
