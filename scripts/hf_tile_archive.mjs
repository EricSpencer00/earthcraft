/** Archive completed generation without restarting workers or reading user saves. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {command,archiveSshOptions} from './archive_command.mjs';
import {retryTransport} from './archive_transport_retry.mjs';
import {legacyRateReceipt,reserveAttempt,recordRetry,markVerified,waitForSlot} from './archive_hf_retry.mjs';
import {fileURLToPath} from 'node:url';
import {cleanupIntent,checkedRestore,completeCleanup,pruneProgram} from './archive_cleanup.mjs';
import {exportProgress} from './archive_export_state.mjs';
import {checkedExportLock,waitForExportLock,lockProbeProgram} from './archive_export_wait.mjs';
import {localCapacityWait} from './archive_local_capacity.mjs';

const args=process.argv.slice(2);
const config=JSON.parse(await fs.readFile(args[args.indexOf('--config')+1],'utf8'));
const control=path.resolve(args[args.indexOf('--control')+1]);
if(os.hostname().toLowerCase()!=='eric-mb64.local')throw Error('MacBook coordination origin required');
await fs.mkdir(control,{recursive:true});
const hash=raw=>crypto.createHash('sha256').update(raw).digest('hex');
const quote=value=>"'"+String(value).replaceAll("'","'\\''")+"'";
async function atomic(file, value){
 const tmp=file+'.partial';const handle=await fs.open(tmp,'w',0o600);
 try{await handle.writeFile(JSON.stringify(value));await handle.sync();}finally{await handle.close();}
 await fs.rename(tmp,file);const directory=await fs.open(path.dirname(file),'r');
 try{await directory.sync();}finally{await directory.close();}
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
async function remote(args,input=null,guardDispatch=false){return transport(async()=>{
 await route();
 if(guardDispatch&&(stopping||Date.now()/1000>=state.deadline))throw Error('Original deadline or stop reached before export dispatch');
 return command('/usr/bin/ssh',[...archiveSshOptions(control,config),config.host,args.map(quote).join(' ')],input);});}
const helper=await fs.readFile(config.lock_helper);
if(hash(helper)!==config.lock_helper_sha256)throw Error('Task lock helper changed');
const lockPath=path.join(control,'archive.lock');await fs.writeFile(lockPath,'',{flag:'a'});
const lock=spawn(config.lock_helper,[lockPath],{stdio:['pipe','pipe','pipe']});
await new Promise((resolve,reject)=>{
 lock.stdout.once('data',data=>String(data).trim()==='locked'?resolve():reject(Error('Archive lock failed')));
 lock.once('error',reject);lock.once('close',code=>reject(Error('Archive lock failed with exit code '+code)));
});
const statePath=path.join(control,'state.local.json');
const statusPath=path.join(control,'status.local.json');
let state;
try{state=JSON.parse(await fs.readFile(statePath,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;state={known:{},deadline:config.deadline,batches:0};}
if(state.deadline!==config.deadline)throw Error('Preserve original resource deadline');
// The old controller logged no raw HTTP headers. Anchor its one-hour hold to
// the status file write time (after the failure), not the batch start time.
if(!state.hf_retry){
 let previous,modified;
 try{previous=JSON.parse(await fs.readFile(statusPath,'utf8'));modified=(await fs.stat(statusPath)).mtimeMs/1000;}
 catch(e){if(e.code!=='ENOENT')throw e;}
 const receipt=legacyRateReceipt(previous,modified);
 if(receipt){state.hf_retry=receipt;state.hf_failures=1;await atomic(statePath,state);}
}
let stopping=false;
process.on('SIGTERM',()=>stopping=true);process.on('SIGINT',()=>stopping=true);
const batchLimit=config.batch_limit??24;
if(!Number.isInteger(batchLimit)||batchLimit<1||batchLimit>32)throw Error('Bounded archive batch required');
const memoryUpload=config.memory_upload===true;
const archiveLimit=memoryUpload?64*2**20:256*2**20;
const once=args.includes('--once');
const exportLockIdentity=JSON.parse(await fs.readFile(path.join(control,'archive-export-lock.local.json'),'utf8'));
if(exportLockIdentity.control!==config.control||exportLockIdentity.host!==config.hostname||
 !Number.isSafeInteger(exportLockIdentity.inode)||exportLockIdentity.inode<1)throw Error('Installed persistent exporter lock identity differs');
const interval=config.hf_commit_interval_seconds??60;
if(!Number.isFinite(interval)||interval<60||interval>3600)throw Error('HF commit interval must be 60..3600 seconds');
const maxFailures=config.hf_max_retry_failures??3;
const restoreScript=fileURLToPath(new URL('./verify_generated_restore.py',import.meta.url));
async function verifyRestore(intent){
 const proof=path.join(control,intent.sha256+'.proof.local.json');
 const restored=JSON.parse(await command(config.hf_python,[restoreScript,'--proof',proof,'--sha256',intent.sha256,
  '--bytes',String(intent.bytes),'--plan-sha256',intent.plan_sha256,'--repo-id',intent.repo],null,300000));
 checkedRestore(restored,intent);return restored;
}
const cleanup={restore:verifyRestore,prune:async intent=>JSON.parse(await remote([config.python,'-c',pruneProgram],JSON.stringify(intent))),
 persist:value=>atomic(statePath,value)};
async function readExportLock(){
 return JSON.parse(await transport(async()=>{
  await route();return command('/usr/bin/ssh',[...archiveSshOptions(control,config),config.host,
   [config.python,'-c',lockProbeProgram,config.control+'/hf-export.lock'].map(quote).join(' ')],null,30000,{maxBytes:4096});}));
}
async function awaitExportLock(status){
 return waitForExportLock({expectedInode:exportLockIdentity.inode,
  shouldStop:()=>stopping||Date.now()/1000>=state.deadline,
  pause:async()=>{for(let i=0;i<60&&!stopping&&Date.now()/1000<state.deadline;i++)await new Promise(r=>setTimeout(r,1000));},
  publish:progress=>atomic(statusPath,{...status,...progress,time:Date.now()/1000}),read:readExportLock});
}
try{
 while(!stopping&&Date.now()/1000<state.deadline){
  try{
  const localDisk=await fs.statfs(control);
  const localWait=localCapacityWait(localDisk.bavail*localDisk.bsize,memoryUpload);
  if(localWait){
   // Do not turn scarce metadata space into a persistent failure/restart loop.
   // Leave pending upload and cleanup identities intact until capacity returns.
   if(localWait.available_bytes>=1024**2){
    try{await atomic(statusPath,{time:Date.now()/1000,state:'waiting_on_local_storage',
      ...localWait,archived_tiles:Object.keys(state.known).length,batches:state.batches,
      pending_preserved:!!state.pending,cleanup_pending_preserved:!!state.cleanup_pending,
      generation_restarted:false,original_world_modified:false,new_paid_compute:false});}
    catch(error){if(error.code!=='ENOSPC')throw error;}
   }
   if(once)break;
   await new Promise(resolve=>setTimeout(resolve,60000));
   continue;
  }
  if(state.cleanup_pending){
   try{
    const intent=state.cleanup_pending;
    const uploaded=JSON.parse(await fs.readFile(path.join(control,intent.sha256+'.proof.local.json'),'utf8'));
    cleanupIntent({...intent,tiles:intent.entries.length},uploaded,config);
    await completeCleanup(state,cleanup);
   }catch(error){
    await atomic(statusPath,{time:Date.now()/1000,state:'blocked',phase:'restore_or_cleanup',reason:error.message,
     archived_tiles:Object.keys(state.known).length,batches:state.batches,cleanup_pending_preserved:true,
     generation_restarted:false,original_world_modified:false,new_paid_compute:false});throw error;
   }
  }
  const status={time:Date.now()/1000,state:'running',archived_tiles:Object.keys(state.known).length,batches:state.batches,
                generation_restarted:false,original_world_modified:false,new_paid_compute:false};
  if(!await waitForSlot(state,{once,maxFailures,shouldStop:()=>stopping||Date.now()/1000>=state.deadline,
    publish:progress=>atomic(statusPath,{...status,...progress})}))break;
  await atomic(statusPath,status);
  let uploadAttempt=null;
  try{
   const disk=await fs.statfs(control);
   if(!memoryUpload&&disk.bavail*disk.bsize<(config.reserve_gib??50)*2**30)throw Error('MacBook transport storage reserve reached');
   if(!state.pending){
    // The exporter checks the same 150 GiB mounted-volume reserve in its
    // bounded IO child, under the persistent export lock.
    const liveLock=await readExportLock();
    if(stopping||Date.now()/1000>=state.deadline)break;
    if(!checkedExportLock(liveLock,exportLockIdentity.inode)){
     status.state='waiting_on_storage';status.phase='unresolved_export_io';await atomic(statusPath,status);
     if(once)break;
     if(!await awaitExportLock(status))break;
     continue;
    }
    const record=JSON.parse(await remote([config.python,config.hf_exporter,'--control',config.control,'--bulk',config.bulk,
     '--output',config.bulk+'/hf-generated','--limit',String(batchLimit),'--lock-inode',String(exportLockIdentity.inode)],JSON.stringify(state.known)+'\n',true));
    Object.assign(status,exportProgress(record,batchLimit));
    if(record.state==='busy'){
     status.state='waiting_on_storage';await atomic(statusPath,status);
     if(once)break;
     const ready=await awaitExportLock(status);
     if(!ready)break;
     continue;
    }
    if(!record.tiles){await atomic(path.join(control,'status.local.json'),status);
     if(once)break;
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
    payload=await transport(async()=>{await route();return command('/usr/bin/ssh',[...archiveSshOptions(control,config),config.host,
     ['cat',record.archive].map(quote).join(' ')],null,300000,{binary:true,maxBytes:record.bytes});});
    if(payload.length!==record.bytes||hash(payload)!==record.sha256)throw Error('In-memory transport checksum differs');
   }else{
    let available=false;try{available=hash(await fs.readFile(archive))===record.sha256;}catch(e){if(e.code!=='ENOENT')throw e;}
    if(!available){await transport(async()=>{await route();return command('/usr/bin/rsync',['-az','--timeout=120','-e',
      ['/usr/bin/ssh',...archiveSshOptions(control,config)].map(quote).join(' '),config.host+':'+record.archive,archive]);});}
    if(hash(await fs.readFile(archive))!==record.sha256)throw Error('Transport checksum differs');
   }
   const proof=path.join(control,record.sha256+'.proof.local.json');
   const attemptId=crypto.randomUUID();
   uploadAttempt=attemptId;
   // Persist pacing before any uploader request, including unknown outcomes.
   reserveAttempt(state,attemptId,Date.now()/1000,interval);await atomic(statePath,state);
   await command(config.hf_python,[config.hf_uploader,...(memoryUpload?['--archive-stdin']:['--archive',archive]),'--sha256',record.sha256,
     '--plan-sha256',config.plan_sha256,'--repo-id',config.hf_repo,'--proof',proof,
     '--retry-state',path.join(control,'hf-retry.local.json'),'--attempt-id',attemptId],payload,600000);
   const uploaded=JSON.parse(await fs.readFile(proof,'utf8'));
   if(uploaded.state!=='verified'||uploaded.archive_sha256!==record.sha256||uploaded.plan_sha256!==config.plan_sha256||
      uploaded.repo!==config.hf_repo||uploaded.private!==true||uploaded.personal_save_uploaded!==false||
      uploaded.original_world_modified!==false)throw Error('Verified private remote upload required');
   const intent=cleanupIntent(record,uploaded,config);
   const restored=await verifyRestore(intent);
   // Persist the verified tile identities and cleanup intent before deleting
   // a transport copy. On restart only cleanup repeats, never the upload.
   state.cleanup_pending=intent;
   for(const item of record.entries)state.known[item.tile]=item.receipt_sha256;
   state.batches++;state.revision=uploaded.revision;delete state.pending;markVerified(state);await atomic(statePath,state);
   const pruned=await completeCleanup(state,cleanup,restored);
   // Memory mode creates no local tar. Only the restored, newly committed
   // remote transport copy is pruned; existing unique sources remain.
   payload=null;
   status.archived_tiles=Object.keys(state.known).length;status.batches=state.batches;status.revision=uploaded.revision;
   status.memory_upload=memoryUpload;status.chunk_bytes=record.bytes;status.chunk_sha256=record.sha256;
   status.restore_verified=true;status.transport_copy_cleanup=pruned.state;
  }catch(e){
   let receipt;
   try{receipt=JSON.parse(await fs.readFile(path.join(control,'hf-retry.local.json'),'utf8'));}
   catch(readError){if(readError.code!=='ENOENT')throw readError;}
   if(uploadAttempt&&e.executable===config.hf_python&&receipt?.attempt_id===uploadAttempt&&recordRetry(state,receipt,Date.now()/1000)){
    await atomic(statePath,state);
    await atomic(statusPath,{...status,state:'rate_limited',retry_kind:state.hf_retry.kind,
     retry_failures:state.hf_failures,next_retry_at:new Date(state.hf_retry.not_before*1000).toISOString(),pending_preserved:!!state.pending});
    if(once)break;
    continue;
   }
   status.state='blocked';status.reason=e.message;await atomic(statusPath,status);throw e;
  }
  await atomic(path.join(control,'status.local.json'),status);
  if(once)break;
  }catch(error){
   // A concurrent writer can consume disk after statfs. Retain in-memory
   // pending/cleanup state and wait; the next iteration rechecks free space.
   if(error.code!=='ENOSPC')throw error;
   if(once)break;
   await new Promise(resolve=>setTimeout(resolve,60000));
  }
 }
}finally{lock.stdin.end();}
