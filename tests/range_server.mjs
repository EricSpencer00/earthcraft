// Dev-only static server with HTTP Range support (python -m http.server ignores Range).
// Serves dashboard/ at / and region files under /data/ so blocks.html can read them as it reads HF:
//   node tests/range_server.mjs --data /path/to/world/region [--port 8765]
//   open http://127.0.0.1:8765/blocks.html?base=/data/&chunk=2,2&radius=3
// Read-only: it never writes, and it refuses paths outside the mounted directories.
import {createServer} from 'node:http';
import {open,stat} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2','.txt':'text/plain'};
export const CSP="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; worker-src 'self' blob:; frame-ancestors 'none'";
// mounts: [{prefix:'/data/', dir:'/abs/dir'} | {path:'/data/0/r.0.0.mca', file:'/abs/file'}]
export function createRangeServer({mounts,headers={}}){
  return createServer(async(req,res)=>{
    const send=(status,extra={},body='')=>{res.writeHead(status,{'Content-Security-Policy':CSP,'X-Content-Type-Options':'nosniff','Access-Control-Allow-Origin':'*','Access-Control-Expose-Headers':'Content-Range, Content-Length, Accept-Ranges',...headers,...extra});res.end(body);};
    if(req.method==='OPTIONS'){send(204,{'Access-Control-Allow-Headers':'range','Access-Control-Allow-Methods':'GET, HEAD'});return;}
    if(req.method!=='GET'&&req.method!=='HEAD'){send(405);return;}
    let pathname;try{pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);}catch{send(400);return;}
    let filename=null;
    for(const m of mounts){
      if(m.file&&pathname===m.path){filename=m.file;break;}
      if(m.dir&&pathname.startsWith(m.prefix)){const dir=resolve(m.dir),f=resolve(dir,pathname.slice(m.prefix.length)||m.index||'index.html');if(f===dir||!f.startsWith(dir+sep)){send(403);return;}filename=f;break;}
    }
    if(!filename){send(404,{},'Not found');return;}
    let info;try{info=await stat(filename);if(!info.isFile())throw Error();}catch{send(404,{},'Not found');return;}
    const type=MIME[extname(filename)]||'application/octet-stream',size=info.size,range=req.headers.range;
    let start=0,end=size-1,status=200;
    if(range){
      const m=/^bytes=(\d*)-(\d*)$/.exec(range.trim());
      if(!m||(!m[1]&&!m[2])){send(416,{'Content-Range':`bytes */${size}`});return;}
      if(m[1]){start=Number(m[1]);end=m[2]?Math.min(Number(m[2]),size-1):size-1;}else{start=Math.max(0,size-Number(m[2]));}
      if(start>=size||end<start){send(416,{'Content-Range':`bytes */${size}`});return;}
      status=206;
    }
    const length=end-start+1,extra={'Content-Type':type,'Content-Length':String(length),'Accept-Ranges':'bytes','Cache-Control':'no-store'};
    if(status===206)extra['Content-Range']=`bytes ${start}-${end}/${size}`;
    res.writeHead(status,{'Content-Security-Policy':CSP,'X-Content-Type-Options':'nosniff','Access-Control-Allow-Origin':'*','Access-Control-Expose-Headers':'Content-Range, Content-Length, Accept-Ranges',...headers,...extra});
    if(req.method==='HEAD'||!length){res.end();return;}
    const handle=await open(filename,'r');
    try{const buffer=Buffer.alloc(Math.min(length,4*1024*1024));let at=start;while(at<=end){const {bytesRead}=await handle.read(buffer,0,Math.min(buffer.length,end-at+1),at);if(!bytesRead)break;if(!res.write(buffer.subarray(0,bytesRead)))await new Promise(done=>res.once('drain',done));at+=bytesRead;}}
    finally{await handle.close();res.end();}
  });
}

if(process.argv[1]&&fileURLToPath(import.meta.url)===resolve(process.argv[1])){
  const arg=name=>{const i=process.argv.indexOf(name);return i>0?process.argv[i+1]:null;};
  const dashboard=fileURLToPath(new URL('../dashboard/',import.meta.url)),data=arg('--data'),port=Number(arg('--port')||8765);
  const mounts=[...(data?[{prefix:'/data/',dir:resolve(data)}]:[]),{prefix:'/',dir:dashboard}];
  const server=createRangeServer({mounts});
  server.listen(port,'127.0.0.1',()=>console.log(`Serving dashboard${data?' and '+resolve(data)+' at /data/':''} on http://127.0.0.1:${port}/blocks.html${data?'?base=/data/':''}`));
}
