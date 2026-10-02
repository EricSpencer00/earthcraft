/** Lightweight MacBook coordination; all block comparisons execute on the mini. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import {spawn} from 'node:child_process';
import {sshOptions} from './mini_transport.mjs';

const argv=process.argv.slice(2);
const config=JSON.parse(await fs.readFile(argv[argv.indexOf('--config')+1],'utf8'));
const control=path.resolve(argv[argv.indexOf('--control')+1]);
await fs.mkdir(control,{recursive:true});
const digest=raw=>crypto.createHash('sha256').update(raw).digest('hex');
async function atomic(target,raw) {
  await fs.mkdir(path.dirname(target),{recursive:true});
  const temp=target+'.partial'; const file=await fs.open(temp,'w',0o600);
  try {await file.writeFile(raw);await file.sync();} finally {await file.close();}
  await fs.rename(temp,target);
}
async function installFile(target,source,raw) {
  if(!config.native_region_compression||process.platform!=='darwin'||!target.endsWith('.mca'))return atomic(target,raw);
  // Files remain ordinary Anvil files. APFS transparently decodes native
  // filesystem compression, avoiding each chunk's sector-padding disk cost.
  const temp=target+'.partial';await fs.mkdir(path.dirname(target),{recursive:true});
  await command('/usr/bin/ditto',['--hfsCompression',source,temp]);
  if(digest(await fs.readFile(temp))!==digest(raw))throw new Error('Native region compression changed readable bytes');
  const file=await fs.open(temp,'r');try{await file.sync();}finally{await file.close();}
  await fs.rename(temp,target);
}
async function readOptional(target) {try{return await fs.readFile(target);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
function quote(value) {return "'"+String(value).replaceAll("'","'\\''")+"'";}
async function command(executable,args,input=null,timeout=240000) {
  return await new Promise((resolve,reject)=>{
    const child=spawn(executable,args,{stdio:['pipe','pipe','pipe']});let out='',err='';
    child.stdout.on('data',data=>{out+=data;if(out.length>4*1024*1024)child.kill();});
    child.stderr.on('data',data=>{err=(err+data).slice(-4000);});
    const timer=setTimeout(()=>child.kill('SIGTERM'),timeout);
    child.on('error',reject);child.on('close',(code,signal)=>{clearTimeout(timer);code===0?resolve(out):reject(new Error(`${executable}: ${code??signal}: ${err}`));});
    child.stdin.on('error',()=>{});child.stdin.end(input);
  });
}
async function route() {
  const output=await command('/usr/bin/ssh',['-G',config.host]);
  const values=Object.fromEntries(output.trim().split('\n').map(line=>[line.split(' ')[0],line.slice(line.indexOf(' ')+1)]));
  if(values.hostname!==config.hostname||values.user!==config.user||(values.proxyjump??'none')!=='none')throw new Error('Personal mini SSH route changed');
}
async function remote(args,input=null) {
  await route();return command('/usr/bin/ssh',[...sshOptions(control,config),config.host,args.map(quote).join(' ')],input);
}
const rsyncShell=()=>['/usr/bin/ssh',...sshOptions(control,config)].map(quote).join(' ');
async function transfer(source,destination) {await route();await command('/usr/bin/rsync',['-az','--timeout=120','-e',rsyncShell(),source,destination]);}
async function fence(target) {
  const binary=await fs.readFile(config.lock_helper);
  if(digest(binary)!==config.lock_helper_sha256)throw new Error('Save-lock helper changed');
  const child=spawn(config.lock_helper,[target],{stdio:['pipe','pipe','pipe']});
  const released=new Promise(resolve=>child.on('close',resolve));
  await new Promise((resolve,reject)=>{child.stdout.once('data',data=>String(data).trim()==='locked'?resolve():reject(new Error('Save lock failed')));
    child.once('error',reject);child.once('close',code=>reject(new Error(code===3?'world_open':'Save lock failed')));});
  return async()=>{child.stdin.end();await released;};
}
function safeName(name) {
  if(!/^(region\/r\.-?\d+\.-?\d+\.mca|level\.dat|data\/world_border\.dat|city-coverage\.json|regional-quality\.json)$/.test(name))throw new Error('Unexpected save file');
  return name;
}
const publisherLock=path.join(control,'publisher-node.lock');await fs.writeFile(publisherLock,'',{flag:'a'});
const releasePublisher=await fence(publisherLock);
const statePath=path.join(control,'publisher-state.json');
const saved=await readOptional(statePath);
const state=saved?JSON.parse(saved):{known:{},deadline:Date.now()/1000+45*86400,batches:0};
await atomic(statePath,JSON.stringify(state));
let stopping=false,failures=0;
const batchLimit=config.batch_limit??24;
if(!Number.isInteger(batchLimit)||batchLimit<1||batchLimit>32)throw new Error('Bounded delivery batch required');
process.on('SIGTERM',()=>stopping=true);process.on('SIGINT',()=>stopping=true);
async function acknowledge(record, transport) {
 for(const entry of record.entries)state.known[entry.tile]=entry.receipt_sha256;
 delete state.pending;state.batches++;
 state.cleanup=transport;
 // Save the acknowledgement before removing transport bytes. A restart may
 // repeat cleanup, but must never retry a bundle that has already been removed.
 await atomic(statePath,JSON.stringify(state));
}
async function cleanup() {
 const pending=state.cleanup;if(!pending)return;
 const code=`import shutil; from pathlib import Path; `+
  (pending.destination?`shutil.rmtree(${JSON.stringify(pending.destination)},ignore_errors=True); `:'')+
  `Path(${JSON.stringify(pending.bundle)}).unlink(missing_ok=True)`;
 await remote([config.python,'-c',code]);
 if(pending.local)await fs.rm(pending.local,{recursive:true,force:true});
 delete state.cleanup;await atomic(statePath,JSON.stringify(state));
}
try {
 while(!stopping&&Date.now()/1000<state.deadline) {
  const status={time:Date.now()/1000,delivered_tiles:Object.keys(state.known).length,deadline:state.deadline,state:'running'};
  try {
   if(config.supervisor) {
    const args=config.supervisor;
    status.generation=JSON.parse(await remote(['/usr/bin/env',
      'EARTHCRAFT_BULK_ROOT='+args.bulk_root,'EARTHCRAFT_CLIENT_JAR='+args.client_jar,
      ...(args.building_index?['EARTHCRAFT_BUILDING_INDEX='+args.building_index]:[]),
      config.python,config.root+'/scripts/regional_supervisor.py','--ensure','--control',config.control,
      '--bulk',config.bulk,'--frame',args.frame,'--illinois',args.illinois,'--reserve-gib','150',
      '--base-workers',String(args.base_workers??1),'--scan-workers',String(args.scan_workers??1)]));
   }
   await cleanup();
   const disk=await fs.statfs(config.world);
   if(disk.bavail*disk.bsize<(config.reserve_gib??50)*2**30){status.state='storage_boundary';throw new Error('Local storage reserve reached');}
   if(!state.pending) {
    const legacy=(Array.isArray(config.legacy)?config.legacy:[config.legacy]).flatMap(directory=>['--legacy',directory]);
    const record=JSON.parse(await remote([config.python,config.root+'/scripts/regional_bundle.py','--control',config.control,
      '--bulk',config.bulk,...legacy,'--limit',String(batchLimit)],JSON.stringify(state.known)+'\n'));
    if(record.bytes>512*2**20||path.dirname(record.bundle)!==config.control+'/exports'||!/^batch-\d+\.tar$/.test(path.basename(record.bundle)))throw new Error('Export exceeded task bounds');
    state.pending=record;await atomic(statePath,JSON.stringify(state));
   }
   const record=state.pending;
   if(record.entries.length) {
    const releaseWorld=await fence(config.world+'/session.lock');
    try {
     const attempt=record.sha256+'-'+Date.now(); const local=path.join(control,'transfers',attempt);
     const replica=path.join(local,'current');await fs.mkdir(replica,{recursive:true});
     const names=new Set(['level.dat','data/world_border.dat','city-coverage.json','regional-quality.json']);
     for(const entry of record.entries)for(const name of entry.regions)names.add(safeName('region/'+name));
     const originals={},absent=[];
     for(const name of names) {
      safeName(name);const raw=await readOptional(path.join(config.world,name));
      if(raw===null){absent.push(name);continue;} originals[name]=digest(raw);await atomic(path.join(replica,name),raw);
     }
     await atomic(path.join(replica,'session.lock'),Buffer.alloc(3));
     await atomic(path.join(local,'request.json'),JSON.stringify({originals,absent}));
     const destination=config.control+'/deliveries/'+attempt;
     await remote([config.python,'-c',`from pathlib import Path; Path(${JSON.stringify(destination)}).mkdir(parents=True,exist_ok=True)`]);
     await transfer(local+'/',config.host+':'+destination+'/');
     await remote([config.python,config.root+'/scripts/regional_merge.py','--task',destination,'--bundle',record.bundle]);
     await transfer(config.host+':'+destination+'/result.json',local+'/');
     const result=JSON.parse(await fs.readFile(path.join(local,'result.json'),'utf8'));
     if(JSON.stringify(result.originals)!==JSON.stringify(originals))throw new Error('Merge used another save snapshot');
     const files=Object.keys(result.changed);for(const name of files)safeName(name);
     const list=path.join(local,'changed-files.txt');await atomic(list,files.join('\n')+'\n');
     await route();await command('/usr/bin/rsync',['-az','--timeout=120','-e',rsyncShell(),'--files-from='+list,config.host+':'+destination+'/current/',local+'/result/']);
     for(const [name,checksum] of Object.entries(originals))if(digest(await fs.readFile(path.join(config.world,name)))!==checksum)throw new Error('Save changed inside the session fence');
     for(const name of absent)if(await readOptional(path.join(config.world,name))!==null)throw new Error('Absent save file appeared inside fence');
     const backup=path.join(control,'installation-backups',attempt);await fs.mkdir(backup,{recursive:true});
     for(const name of files) {
      const incoming=await fs.readFile(path.join(local,'result',name));if(digest(incoming)!==result.changed[name])throw new Error('Merged file transfer differs');
      const before=await readOptional(path.join(config.world,name));
      if(before!==null) {
       const compressed=zlib.gzipSync(before,{level:1});await atomic(path.join(backup,name+'.gz'),compressed);
       if(digest(zlib.gunzipSync(await fs.readFile(path.join(backup,name+'.gz'))))!==digest(before))throw new Error('Private region backup differs');
      }
      await installFile(path.join(config.world,name),path.join(local,'result',name),incoming);
      if(digest(await fs.readFile(path.join(config.world,name)))!==result.changed[name])throw new Error('Installed file differs');
     }
     result.native_region_compression=Boolean(config.native_region_compression);
     await atomic(path.join(backup,'installation.json'),JSON.stringify(result,null,2));
     await acknowledge(record,{destination,local,bundle:record.bundle});
     // Acknowledged transport replicas are disposable; verified private
     // pre-update backups and delivery reports stay in this local control.
     await cleanup();
    } finally {await releaseWorld();}
   } else {
    await acknowledge(record,{bundle:record.bundle});await cleanup();
   }
   failures=0;
   status.batch_tiles=record.entries.length;
  } catch(error) {
   status.reason=error.message;
   if(error.message==='world_open')status.state='waiting_for_closed_world';
   else if(status.state!=='storage_boundary'){status.state='retrying';status.failures=++failures;}
  }
  status.delivered_tiles=Object.keys(state.known).length;await atomic(path.join(control,'publisher-status.json'),JSON.stringify(status));
  if(status.state==='storage_boundary'||failures>=10)break;
  // Drain a generated backlog continuously. Empty queues/open saves/failures
  // keep bounded polling so coordination does not consume the MacBook.
  const pause=status.state==='running'&&status.batch_tiles===batchLimit?1:
    status.state==='running'&&status.batch_tiles>0?5:Math.min(600,120*Math.max(1,failures));
  for(let i=0;i<pause&&!stopping;i++)await new Promise(resolve=>setTimeout(resolve,1000));
 }
} finally {await releasePublisher();}
