/** Lightweight bounded cloud collection; scan validation/merges stay on mini. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {sshOptions} from './mini_transport.mjs';
const exec=promisify(execFile),aws=process.env.EARTHCRAFT_AWS_CLI??path.join(process.env.HOME,'.local/bin/aws');
const control=path.resolve(process.argv[2]);
const publisher=JSON.parse(await fs.readFile(path.join(control,'../publisher-config.json'),'utf8'));
const resource=JSON.parse(await fs.readFile(control+'/resources.json','utf8'));
const request=JSON.parse(await fs.readFile(control+'/request.json','utf8'));
const remoteRoot=publisher.root,remoteControl=publisher.control,bulk=publisher.bulk;
const statePath=control+'/collection.json';
let state;try{state=JSON.parse(await fs.readFile(statePath,'utf8'));}catch{state={accepted:{},resource};}
state.resource=resource;
// Keep the native AWS login-session reference, without reading credentials or
// tokens. A standalone transfer config otherwise loses secure CLI login.
const login=(await exec(aws,['configure','get','login_session'])).stdout.trim();
if(!login.startsWith('arn:aws:'))throw new Error('Native AWS login-session reference unavailable');
await fs.writeFile(control+'/transfer-config','[default]\nregion = us-east-1\nlogin_session = '+login+'\ns3 =\n    max_bandwidth = 5MB/s\n    preferred_transfer_client = classic\n',{mode:0o600});
const env={...process.env,AWS_CONFIG_FILE:control+'/transfer-config'};
const command=async(binary,args,timeout=900000)=>exec(binary,args,{env,maxBuffer:4*2**20,timeout});
const quote=v=>"'"+String(v).replaceAll("'","'\\''")+"'";
async function route(){const {stdout}=await command('/usr/bin/ssh',['-G',publisher.host]);
 const values=Object.fromEntries(stdout.trim().split('\n').map(l=>[l.split(' ')[0],l.slice(l.indexOf(' ')+1)]));
 if(values.hostname!==publisher.hostname||values.user!==publisher.user||(values.proxyjump??'none')!=='none')throw new Error('Mini route changed');}
async function remote(args){await route();return command('/usr/bin/ssh',[...sshOptions(path.dirname(control),publisher),publisher.host,
 ['/usr/bin/env','PYTHONPATH='+remoteRoot+'/scripts','EARTHCRAFT_CLIENT_JAR='+publisher.supervisor.client_jar,
  'EARTHCRAFT_BULK_ROOT='+publisher.supervisor.bulk_root,...args].map(quote).join(' ')]);}
async function transfer(source,destination){await route();const shell=['/usr/bin/ssh',...sshOptions(path.dirname(control),publisher)].map(quote).join(' ');return command('/usr/bin/rsync',['-az','--timeout=120','-e',shell,source,destination]);}
const cloud=async(service,operation,args)=>command(aws,[service,operation,...args,'--region','us-east-1','--output','json']);
const hash=async(file)=>crypto.createHash('sha256').update(await fs.readFile(file)).digest('hex');
async function save(){await fs.writeFile(statePath+'.partial',JSON.stringify({...state,time:Date.now()/1000},null,2));await fs.rename(statePath+'.partial',statePath);}
async function retireCompute() {
 if(state.compute_terminated)return;
 const identity=JSON.parse((await cloud('sts','get-caller-identity',[])).stdout);
 if(identity.Account!==resource.account)throw new Error('Task AWS account changed');
 await cloud('ec2','terminate-instances',['--instance-ids',resource.instance]);
 state.compute_terminated=true;await save();
}
async function retireAccess() {
 if(state.identity_cleaned&&state.network_cleaned)return;
 const identity=JSON.parse((await cloud('sts','get-caller-identity',[])).stdout);
 if(identity.Account!==resource.account)throw new Error('Task AWS account changed');
 const remove=async(service,operation,args)=>{
  try{return await cloud(service,operation,args);}
  catch(error){if(!error.message.includes('NoSuchEntity'))throw error;}
 };
 if(!state.identity_cleaned) {
  await remove('iam','remove-role-from-instance-profile',['--instance-profile-name',resource.role,'--role-name',resource.role]);
  await remove('iam','delete-instance-profile',['--instance-profile-name',resource.role]);
  await remove('iam','detach-role-policy',['--role-name',resource.role,'--policy-arn','arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore']);
  await remove('iam','delete-role-policy',['--role-name',resource.role,'--policy-name','task-bucket-only']);
  await remove('iam','delete-role',['--role-name',resource.role]);
  state.identity_cleaned=true;await save();
 }
 if(!state.network_cleaned) {
  try{await cloud('ec2','delete-security-group',['--group-id',resource.group]);state.network_cleaned=true;await save();}
  catch(error){
   if(error.message.includes('InvalidGroup.NotFound')){state.network_cleaned=true;await save();}
   else if(!error.message.includes('DependencyViolation'))throw error;
  }
 }
}
const remoteRequest=remoteControl+'/aws-fast-inputs.tar.json';
let lastRenew=0;
while(Date.now()/1000<resource.deadline) {
 try {
  if(Date.now()/1000-lastRenew>120){await remote([remoteRoot+'/.venv/bin/python',remoteRoot+'/scripts/regional_cloud_exchange.py','renew','--control',remoteControl,'--request',remoteRequest]);lastRenew=Date.now()/1000;}
  let status;
  try{await command(aws,['s3','cp','s3://'+resource.bucket+'/status.json',control+'/status.json','--only-show-errors']);
      status=JSON.parse(await fs.readFile(control+'/status.json','utf8'));}
  catch{state.state='waiting_for_cloud';await save();await new Promise(r=>setTimeout(r,20000));continue;}
  state.cloud=status;
  if(status.complete) {
   const original=JSON.parse((await cloud('s3api','head-object',['--bucket',resource.bucket,'--key','source-cache.tar'])).stdout);
   if(original.ContentLength!==status.source_cache_bytes||!/^[a-f0-9]{64}$/.test(status.source_cache_sha256))throw new Error('Original scan archive is not retained');
   // Stop paid compute as soon as all candidate/source objects are uploaded.
   // Collection can keep waiting for LaCie without holding an idle instance.
   await retireCompute();
   await retireAccess();
  }
  const storage=await remote([remoteRoot+'/.venv/bin/python','-c',
   `from pathlib import Path; import json; p=Path('/Volumes/LaCie'); print(json.dumps({'mounted':p.is_mount()}))`]);
  if(!JSON.parse(storage.stdout).mounted) {
   state.state='waiting_for_storage';delete state.error;await save();
   await new Promise(resolve=>setTimeout(resolve,20000));continue;
  }
  for(const record of status.records) {
   if(record.result==='failed'||state.accepted[record.tile])continue;
   const local=control+'/'+record.tile+'.tar.gz';
   await command(aws,['s3','cp','s3://'+resource.bucket+'/results/'+record.tile+'.tar.gz',local,'--only-show-errors']);
   if(await hash(local)!==record.sha256)throw new Error('Cloud artifact transfer changed');
   const target=remoteControl+'/cloud-received/'+request.owner;
   await remote(['/bin/mkdir','-p',target]);await transfer(local,publisher.host+':'+target+'/');
   const {stdout}=await remote([remoteRoot+'/.venv/bin/python',remoteRoot+'/scripts/regional_cloud_exchange.py','accept',
    '--control',remoteControl,'--bulk',bulk,'--request',remoteRequest,'--archive',target+'/'+record.tile+'.tar.gz','--tile='+record.tile]);
   state.accepted[record.tile]={time:Date.now()/1000,result:stdout.trim()};delete state.error;await save();await fs.unlink(local);
  }
  if(status.complete){state.state='collecting_originals';await save();break;}
  state.state='running';await save();
 }catch(error){state.state='retrying';state.error=error.message.slice(-3000);await save();}
 await new Promise(resolve=>setTimeout(resolve,20000));
}
if(state.cloud?.complete&&Object.keys(state.accepted).length===request.jobs.length) {
 const local=control+'/source-cache.tar';
 await command(aws,['s3','cp','s3://'+resource.bucket+'/source-cache.tar',local,'--only-show-errors'],7200000);
 // Streaming digest keeps original archive verification from using bulk RAM.
 const {createReadStream}=await import('node:fs');const digest=crypto.createHash('sha256');
 for await(const buffer of createReadStream(local))digest.update(buffer);
 if(digest.digest('hex')!==state.cloud.source_cache_sha256)throw new Error('Original source archive changed');
 const target=bulk+'/cloud-originals/'+request.owner;
 await remote([remoteRoot+'/.venv/bin/python','-c',`from pathlib import Path; from regional_cloud_exchange import require_mini_bulk; require_mini_bulk(${JSON.stringify(bulk)}); Path(${JSON.stringify(target)}).mkdir(parents=True,exist_ok=True)`]);
 await transfer(local,publisher.host+':'+target+'/source-cache.tar');
 await remote([remoteRoot+'/.venv/bin/python','-c',`from pathlib import Path; from region_expansion import sha; p=Path(${JSON.stringify(target+'/source-cache.tar')}); assert sha(p)==${JSON.stringify(state.cloud.source_cache_sha256)}`]);
 state.originals_retained_on_lacie=true;await save();await fs.unlink(local);
}
// Release only this batch's remaining tokens, then remove only owned resources.
await remote([remoteRoot+'/.venv/bin/python','-c',`import sqlite3; db=sqlite3.connect(${JSON.stringify(remoteControl+'/jobs.sqlite')}); db.execute("UPDATE jobs SET state='pending',token=NULL,owner=NULL,expires=NULL WHERE state='running' AND owner=?",(${JSON.stringify(request.owner)},)); db.commit()`]);
await retireCompute();
state.state=Object.keys(state.accepted).length===request.jobs.length?'complete':'partial';await save();
if(state.originals_retained_on_lacie&&Object.keys(state.accepted).length===request.jobs.length) {
 await command(aws,['s3','rm','s3://'+resource.bucket,'--recursive','--only-show-errors']);
 await cloud('s3api','delete-bucket',['--bucket',resource.bucket]);
 state.bucket_cleaned=true;await save();
}
// Termination can take a little time before the task security group detaches.
for(let attempt=0;attempt<8;attempt++) {
 await retireAccess();
 if(state.network_cleaned)break;
 await new Promise(r=>setTimeout(r,15000));
}
