/** Archive completed generation without restarting workers or reading user saves. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {sshOptions} from './mini_transport.mjs';
import {retryTransport} from './archive_transport_retry.mjs';

const args=process.argv.slice(2);
const config=JSON.parse(await fs.readFile(args[args.indexOf('--config')+1],'utf8'));
const control=path.resolve(args[args.indexOf('--control')+1]);
if(os.hostname().toLowerCase()!=='eric-mb64.local')throw Error('MacBook coordination origin required');
await fs.mkdir(control,{recursive:true});
const hash=raw=>crypto.createHash('sha256').update(raw).digest('hex');
const quote=value=>"'"+String(value).replaceAll("'","'\\''")+"'";
async function atomic(file, value){
 const tmp=file+'.partial';await fs.writeFile(tmp,JSON.stringify(value),{mode:0o600});await fs.rename(tmp,file);
}
async function command(executable,args,input=null,timeout=300000,{binary=false,maxBytes=4*1024**2}={}){
 return new Promise((resolve,reject)=>{
  const child=spawn(executable,args,{stdio:['pipe','pipe','pipe']});let out='',err='',bytes=0;const chunks=[];
  child.stdout.on('data',data=>{bytes+=data.length;if(bytes>maxBytes){child.kill();return;}if(binary)chunks.push(data);else out+=data;});
  child.stderr.on('data',data=>err=(err+data).slice(-4000));
  const timer=setTimeout(()=>child.kill('SIGTERM'),timeout);
  child.on('error',reject);child.on('close',(code,signal)=>{clearTimeout(timer);
   if(code===0&&bytes<=maxBytes)resolve(binary?Buffer.concat(chunks,bytes):out);
   else reject(Object.assign(Error(`${executable}: ${code??signal}: ${err}`),{executable,code,signal}));});
  child.stdin.on('error',()=>{});child.stdin.end(input);
 });
}
async function route(){
 const out=await command('/usr/bin/ssh',['-G',config.host]);
 const v=Object.fromEntries(out.trim().split('\n').map(line=>[line.split(' ')[0],line.slice(line.indexOf(' ')+1)]));
 if(v.hostname!==config.hostname||v.user!==config.user||(v.proxyjump??'none')!=='none'||(v.proxycommand??'none')!=='none')throw Error('Configured personal mini route changed');
 if(v.identityfile!=='~/.ssh/id_ed25519_mac_mini')throw Error('Configured identity changed');
}
async function transport(work){return retryTransport(work,{
 shouldStop:()=>stopping||Date.now()/1000>=config.deadline,
 onRetry:progress=>atomic(path.join(control,'status.local.json'),{time:Date.now()/1000,state:'reconnecting',
   archived_tiles:Object.keys(state.known).length,batches:state.batches,...progress,
   generation_restarted:false,original_world_modified:false,new_paid_compute:false})});}
async function remote(args,input=null){return transport(async()=>{
 await route();return command('/usr/bin/ssh',[...sshOptions(control,config),config.host,args.map(quote).join(' ')],input);});}
const helper=await fs.readFile(config.lock_helper);
if(hash(helper)!==config.lock_helper_sha256)throw Error('Task lock helper changed');
const lockPath=path.join(control,'archive.lock');await fs.writeFile(lockPath,'',{flag:'a'});
const lock=spawn(config.lock_helper,[lockPath],{stdio:['pipe','pipe','pipe']});
await new Promise((resolve,reject)=>{
 lock.stdout.once('data',data=>String(data).trim()==='locked'?resolve():reject(Error('Archive lock failed')));
 lock.once('error',reject);lock.once('close',code=>reject(Error('Archive lock failed with exit code '+code)));
});
const statePath=path.join(control,'state.local.json');
let state;
try{state=JSON.parse(await fs.readFile(statePath,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;state={known:{},deadline:config.deadline,batches:0};}
if(state.deadline!==config.deadline)throw Error('Preserve original resource deadline');
let stopping=false;
process.on('SIGTERM',()=>stopping=true);process.on('SIGINT',()=>stopping=true);
const batchLimit=config.batch_limit??24;
if(!Number.isInteger(batchLimit)||batchLimit<1||batchLimit>32)throw Error('Bounded archive batch required');
const memoryUpload=config.memory_upload===true;
const archiveLimit=memoryUpload?64*2**20:256*2**20;
const once=args.includes('--once');
try{
 while(!stopping&&Date.now()/1000<state.deadline){
  const status={time:Date.now()/1000,state:'running',archived_tiles:Object.keys(state.known).length,batches:state.batches,
                generation_restarted:false,original_world_modified:false,new_paid_compute:false};
  await atomic(path.join(control,'status.local.json'),status);
  try{
   const disk=await fs.statfs(control);
   if(!memoryUpload&&disk.bavail*disk.bsize<(config.reserve_gib??50)*2**30)throw Error('MacBook transport storage reserve reached');
   if(memoryUpload&&disk.bavail*disk.bsize<1024**2)throw Error('Metadata storage unavailable');
   if(!state.pending){
    const preflight=JSON.parse(await remote([config.python,'-c',
     "import os,shutil,json; print(json.dumps({'mounted':os.path.ismount('/Volumes/LaCie'),'free':shutil.disk_usage('/Volumes/LaCie').free}))"]));
    if(!preflight.mounted||preflight.free<150*2**30)throw Error('LaCie generation reserve reached');
    const record=JSON.parse(await remote([config.python,config.hf_exporter,'--control',config.control,'--bulk',config.bulk,
     '--output',config.bulk+'/hf-generated','--limit',String(batchLimit)],JSON.stringify(state.known)+'\n'));
    if(!record.tiles){status.state='caught_up';await atomic(path.join(control,'status.local.json'),status);
     for(let i=0;i<60&&!stopping;i++)await new Promise(r=>setTimeout(r,1000));continue;}
    if(record.plan_sha256!==config.plan_sha256||record.bytes>archiveLimit||
       record.archive!==config.bulk+'/hf-generated/'+record.sha256+'.tar'||!/^[a-f0-9]{64}$/.test(record.sha256))throw Error('Generated export bounds differ');
    state.pending=record;await atomic(statePath,state);
   }
   const record=state.pending;const archive=path.join(control,record.sha256+'.tar');
   if(record.plan_sha256!==config.plan_sha256||!Number.isInteger(record.bytes)||record.bytes<1||record.bytes>archiveLimit||
      !/^[a-f0-9]{64}$/.test(record.sha256)||record.archive!==config.bulk+'/hf-generated/'+record.sha256+'.tar')throw Error('Pending archive bounds differ');
   let payload=null;
   if(memoryUpload){
    payload=await transport(async()=>{await route();return command('/usr/bin/ssh',[...sshOptions(control,config),config.host,
     ['cat',record.archive].map(quote).join(' ')],null,300000,{binary:true,maxBytes:record.bytes});});
    if(payload.length!==record.bytes||hash(payload)!==record.sha256)throw Error('In-memory transport checksum differs');
   }else{
    let available=false;try{available=hash(await fs.readFile(archive))===record.sha256;}catch(e){if(e.code!=='ENOENT')throw e;}
    if(!available){await transport(async()=>{await route();return command('/usr/bin/rsync',['-az','--timeout=120','-e',
      ['/usr/bin/ssh',...sshOptions(control,config)].map(quote).join(' '),config.host+':'+record.archive,archive]);});}
    if(hash(await fs.readFile(archive))!==record.sha256)throw Error('Transport checksum differs');
   }
   const proof=path.join(control,record.sha256+'.proof.local.json');
   await command(config.hf_python,[config.hf_uploader,...(memoryUpload?['--archive-stdin']:['--archive',archive]),'--sha256',record.sha256,
     '--plan-sha256',config.plan_sha256,'--repo-id',config.hf_repo,'--proof',proof],payload,600000);
   const uploaded=JSON.parse(await fs.readFile(proof,'utf8'));
   if(uploaded.state!=='verified'||uploaded.archive_sha256!==record.sha256||uploaded.personal_save_uploaded!==false)throw Error('Verified remote upload required');
   for(const item of record.entries)state.known[item.tile]=item.receipt_sha256;
   state.batches++;state.revision=uploaded.revision;delete state.pending;await atomic(statePath,state);
   // Preserve all existing local and LaCie archives; memory mode creates no local tar.
   payload=null;
   status.archived_tiles=Object.keys(state.known).length;status.batches=state.batches;status.revision=uploaded.revision;
   status.memory_upload=memoryUpload;status.chunk_bytes=record.bytes;status.chunk_sha256=record.sha256;
  }catch(e){status.state='blocked';status.reason=e.message;await atomic(path.join(control,'status.local.json'),status);throw e;}
  await atomic(path.join(control,'status.local.json'),status);
  if(once)break;
 }
}finally{lock.stdin.end();}
