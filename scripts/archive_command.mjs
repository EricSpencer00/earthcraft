/** Bound archive subprocesses without depending on inherited pipes closing. */
import {spawn} from 'node:child_process';
import {sshOptions} from './mini_transport.mjs';

// An auto-forked SSH master can retain this command's stderr after its child
// exits. Keep archive SSH in the foreground; other jobs retain their settings.
export function archiveSshOptions(control,config={}){
 const source=sshOptions(control,config),options=[];
 for(let i=0;i<source.length;i+=2){
  if(!/^Control(?:Master|Persist|Path)=/i.test(source[i+1]))options.push(source[i],source[i+1]);
 }
 return [...options,'-o','ControlMaster=no','-o','ControlPersist=no','-o','ControlPath=none'];
}

export function command(executable,args,input=null,timeout=300000,
 {binary=false,maxBytes=4*1024**2,killGraceMs=1000}={}){
 if(!Number.isInteger(timeout)||timeout<1||!Number.isInteger(maxBytes)||maxBytes<1||
    !Number.isInteger(killGraceMs)||killGraceMs<1||killGraceMs>5000)throw Error('Bounded command settings required');
 return new Promise((resolve,reject)=>{
  const grouped=process.platform!=='win32';
  const child=spawn(executable,args,{stdio:['pipe','pipe','pipe'],detached:grouped});
  let settled=false,failure=null,bytes=0,err='',killTimer;
  const chunks=[];
  const signal=kind=>{
   if(!child.pid)return;
   try{if(grouped)process.kill(-child.pid,kind);else child.kill(kind);}
   catch(error){if(error.code!=='ESRCH')child.kill(kind);}
  };
  const destroyPipes=()=>{child.stdin.destroy();child.stdout.destroy();child.stderr.destroy();};
  const finish=(error,value)=>{
   if(settled)return;
   settled=true;clearTimeout(timer);clearTimeout(killTimer);destroyPipes();
   if(error)reject(error);else resolve(value);
  };
  const abort=error=>{
   if(settled||failure)return;
   failure=error;clearTimeout(timer);signal('SIGTERM');
   killTimer=setTimeout(()=>{signal('SIGKILL');finish(failure);},killGraceMs);
   // Detached or inherited writers cannot hold the promise open past this bound.
   destroyPipes();
  };
  const timer=setTimeout(()=>abort(Object.assign(
   Error(`${executable}: timeout after ${timeout}ms: ${err}`),
   {executable,code:'ETIMEDOUT',timedOut:true})),timeout);
  child.stdout.on('data',data=>{
   bytes+=data.length;
   if(bytes>maxBytes){abort(Object.assign(Error(`${executable}: output exceeds ${maxBytes} bytes`),
    {executable,code:'ERR_OUTPUT_LIMIT'}));return;}
   chunks.push(data);
  });
  child.stderr.on('data',data=>err=(err+data).slice(-4000));
  child.once('error',error=>finish(Object.assign(error,{executable})));
  child.once('close',(code,closeSignal)=>{
   if(failure){
    failure.exitCode=code;failure.signal=closeSignal;signal('SIGKILL');finish(failure);return;
   }
   if(code!==0){finish(Object.assign(Error(`${executable}: ${code??closeSignal}: ${err}`),
    {executable,code,signal:closeSignal}));return;}
   const raw=Buffer.concat(chunks,bytes);finish(null,binary?raw:raw.toString('utf8'));
  });
  child.stdin.on('error',()=>{});child.stdin.end(input);
 });
}
