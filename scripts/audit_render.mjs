#!/usr/bin/env node
// Render tiles/v2 tiles at any dataset revision (main, a commit, or a pull request's refs/pr/N)
// through dashboard/blocks.html, three views per tile, for scripts/audit.py.
//
//   node scripts/audit_render.mjs --tiles -52_-1,-60_4 --revision refs/pr/12 --out runs/scratch/audit/x
//
// Writes <out>/<tile>-<view>.png and <out>/renders.json. Capped at 100 MB of output.
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir,readdir,stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const args=Object.fromEntries(process.argv.slice(2).reduce((acc,arg,i,all)=>{
  if(arg.startsWith('--'))acc.push([arg.slice(2),all[i+1]&&!all[i+1].startsWith('--')?all[i+1]:'1']);return acc;},[]));
const tiles=(args.tiles||'').split(',').filter(t=>/^-?\d+_-?\d+$/.test(t));
if(!tiles.length)throw Error('Pass --tiles X_Z[,X_Z...]');
const revision=encodeURIComponent(args.revision||'main');
const out=args.out||'runs/scratch/audit/render',maxBytes=Number(args['max-bytes']||100e6);
const views=['iso','top','street'];

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..','dashboard');
const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2','.bin':'application/octet-stream'};
const server=createServer(async(req,res)=>{
  const file=path.resolve(root,'.'+decodeURIComponent(new URL(req.url,'http://x').pathname));
  if(!file.startsWith(root+path.sep)){res.writeHead(403).end();return;}
  try{res.writeHead(200,{'content-type':types[path.extname(file)]||'application/octet-stream'}).end(await readFile(file));}
  catch{res.writeHead(404).end();}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const site=`http://127.0.0.1:${server.address().port}`;
const {chromium}=await import('@playwright/test').catch(()=>import('playwright'));
await mkdir(out,{recursive:true});
const browser=await chromium.launch({args:['--use-angle=swiftshader','--enable-unsafe-swiftshader'],
  ...(args.chromium||process.env.CHROME_BIN?{executablePath:args.chromium||process.env.CHROME_BIN}:{})});
const page=await browser.newPage({viewport:{width:960,height:640}});
let used=0;
const results=[];
for(const tile of tiles){
  const [tx,tz]=tile.split('_').map(Number),base=`https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/${revision}/tiles/v2/${tile}/region/`;
  const entry={tile,images:{},states:{}};
  for(const view of views){
    if(used>maxBytes)throw Error('Render output reached its cap');
    const url=`${site}/blocks.html?qa=1&base=${encodeURIComponent(base)}&layout=flat-auto&block=${tx*512+256},${tz*512+256}`+
      `&radius=${view==='street'?2:8}&view=${view}&yaw=35&w=960&h=640`;
    await page.goto(url);
    const state=await page.waitForFunction(()=>window.__earthcraftRender?.status!=='loading'&&window.__earthcraftRender,null,{timeout:120000})
      .then(h=>h.jsonValue()).catch(error=>({status:'error',error:String(error).slice(0,300)}));
    entry.states[view]=state;
    if(state.status==='done'){
      const file=`${tile}-${view}.png`,png=await page.locator('canvas').first().screenshot();
      await writeFile(path.join(out,file),png);used+=png.length;entry.images[view]=path.resolve(out,file);
    }
  }
  results.push(entry);
  process.stdout.write(`${tile} ${Object.keys(entry.images).length}/3 views\n`);
}
await browser.close();server.close();
await writeFile(path.join(out,'renders.json'),JSON.stringify({schema:'earthcraft.audit-renders.v1',revision:decodeURIComponent(revision),tiles:results},null,1));
