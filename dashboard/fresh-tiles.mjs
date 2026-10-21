// Decode the published-tile bitmap in status/pipeline.json (scripts/aws_pipeline_setup.sh status):
// base64(zlib(bits)), bit i = (z - z0) * width + (x - x0), least significant bit first.
export async function decodeTileBitmap(map){
  if(!map?.bitmap||!Number.isSafeInteger(map.width*map.height)||map.width*map.height>4e6)return [];
  const packed=Uint8Array.from(atob(map.bitmap),c=>c.charCodeAt(0));
  const bits=new Uint8Array(await new Response(new Blob([packed]).stream().pipeThrough(new DecompressionStream('deflate'))).arrayBuffer());
  const ids=[];
  for(let i=0;i<map.width*map.height;i++)if(bits[i>>3]>>(i&7)&1)ids.push(`${map.x0+i%map.width}_${map.z0+Math.floor(i/map.width)}`);
  return ids;
}
