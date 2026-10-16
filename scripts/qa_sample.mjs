#!/usr/bin/env node
// Sample chunks from the Earthcraft dataset and render them through dashboard/blocks.html for review.
//
//   node scripts/qa_sample.mjs --count 24 --out runs/scratch/qa/<id> [--base URL] [--revision SHA]
//        [--site URL] [--seed N] [--region X,Z] [--chromium PATH]
//
// Without --site, dashboard/ is served from this checkout on a free local port. Writes
// <out>/samples.json and three PNGs per sample (iso, top, street). Output is capped by
// --max-bytes (default 200 MB) and a free-space floor (--min-free, default 25 GB); the run
// stops before either is crossed. Review the images with .claude/commands/qa-chunks.md, then
// upload the run with scripts/hf_store.py push-files.
import {mkdir,writeFile,stat,readdir,readFile} from 'node:fs/promises';
import {statfsSync} from 'node:fs';
import {createServer} from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const args=Object.fromEntries(process.argv.slice(2).reduce((acc,arg,i,all)=>{
  if(arg.startsWith('--'))acc.push([arg.slice(2),all[i+1]&&!all[i+1].startsWith('--')?all[i+1]:'1']);return acc;},[]));
const DATASET='https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/';
const revision=args.revision||'main';
const base=args.base||`${DATASET}${revision}/world/region/`;
const indexUrl=args.index||`${DATASET}${revision}/world/index/regions.json`;
const out=args.out||`runs/scratch/qa/${new Date().toISOString().replace(/[:.]/g,'-')}`;
const count=Number(args.count||24),maxBytes=Number(args['max-bytes']||200e6),minFree=Number(args['min-free']||25e9);
const views=['iso','top','street'];

// Deterministic PRNG so a QA run can be repeated exactly.
let seed=Number(args.seed||Date.now()%2147483647)||1;
const rand=()=>(seed=seed*48271%2147483647)/2147483647;

function present(bitmap){
  const bytes=Buffer.from(bitmap,'base64'),chunks=[];
  for(let i=0;i<1024;i++)if(bytes[i>>3]>>(i&7)&1)chunks.push([i&31,i>>5]);
  return chunks;
}

// Stratify by chunk density so dense downtown regions and sparse edges are both sampled.
function pick(regions){
  const only=args.region?.split(',').map(Number);
  const pool=regions.filter(r=>r.chunk_bitmap&&(!only||(r.x===only[0]&&r.z===only[1])));
  if(!pool.length)throw Error('No regions with chunks in the index.');
  pool.sort((a,b)=>a.chunks-b.chunks);
  const strata=4,picks=[];
  for(let i=0;i<count;i++){
    const s=i%strata,lo=Math.floor(pool.length*s/strata),hi=Math.max(lo+1,Math.floor(pool.length*(s+1)/strata));
    const region=pool[lo+Math.floor(rand()*(hi-lo))],chunks=present(region.chunk_bitmap);
    const [lx,lz]=chunks[Math.floor(rand()*chunks.length)];
    picks.push({region:[region.x,region.z],chunk:[region.x*32+lx,region.z*32+lz],stratum:s,
      yaw:Math.round(rand()*360)});
  }
  return picks;
}

async function bytesUnder(dir){
  let total=0;
  for(const name of await readdir(dir).catch(()=>[]))total+=(await stat(path.join(dir,name))).size;
  return total;
}

function guard(dir,used){
  const fs=statfsSync(dir),free=fs.bavail*fs.bsize;
  if(free<minFree)throw Error(`Free space ${(free/1e9).toFixed(1)} GB is below the ${(minFree/1e9).toFixed(0)} GB floor.`);
  if(used>maxBytes)throw Error(`QA output reached its ${(maxBytes/1e6).toFixed(0)} MB cap.`);
}

// Serve dashboard/ without a CSP so blocks.html can read huggingface.co.
async function serveDashboard(){
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..','dashboard');
  const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css',
    '.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'};
  const server=createServer(async(req,res)=>{
    const file=path.resolve(root,'.'+decodeURIComponent(new URL(req.url,'http://x').pathname));
    if(!file.startsWith(root+path.sep)){res.writeHead(403).end();return;}
    try{res.writeHead(200,{'content-type':types[path.extname(file)]||'application/octet-stream'}).end(await readFile(file));}
    catch{res.writeHead(404).end();}
  });
  await new Promise(done=>server.listen(0,'127.0.0.1',done));
  return {server,url:`http://127.0.0.1:${server.address().port}`};
}

const served=args.site?null:await serveDashboard();
const site=args.site||served.url;
const {chromium}=await import('@playwright/test').catch(()=>import('playwright'));
await mkdir(out,{recursive:true});
const index=await (await fetch(indexUrl)).json();
const regions=index.regions.map(r=>({...r,chunks:present(r.chunk_bitmap).length}));
const samples=pick(regions);
const browser=await chromium.launch({args:['--use-angle=swiftshader','--enable-unsafe-swiftshader'],
  ...(args.chromium?{executablePath:args.chromium}:{})});
const page=await browser.newPage({viewport:{width:960,height:640}});
const results=[];
for(const [i,sample] of samples.entries()){
  const entry={id:`s${String(i).padStart(3,'0')}`,...sample,images:{},status:'pending'};
  for(const view of views){
    guard(out,await bytesUnder(out));
    const url=`${site}/blocks.html?qa=1&base=${encodeURIComponent(base)}&region=${sample.region}`+
      `&chunk=${sample.chunk}&radius=${view==='street'?2:3}&view=${view}&yaw=${sample.yaw}&w=960&h=640`;
    await page.goto(url);
    const state=await page.waitForFunction(()=>window.__earthcraftRender?.status!=='loading'&&window.__earthcraftRender,
      null,{timeout:90000}).then(h=>h.jsonValue()).catch(error=>({status:'error',error:String(error)}));
    entry[view]=state;
    if(state.status==='done'){
      const file=`${entry.id}-${view}.png`;
      await writeFile(path.join(out,file),await page.locator('canvas').first().screenshot());
      entry.images[view]=file;
    }
  }
  entry.status=views.every(v=>entry[v]?.status==='done')?'rendered':'render-failed';
  results.push(entry);
  process.stdout.write(`${entry.id} ${entry.status} region ${sample.region} chunk ${sample.chunk}\n`);
}
await browser.close();
served?.server.close();
await writeFile(path.join(out,'samples.json'),JSON.stringify({
  schema:'earthcraft.qa-samples.v1',created_at:new Date().toISOString(),revision,base,seed:args.seed||null,
  samples:results},null,1));
console.log(`Wrote ${results.length} samples to ${out}`);
