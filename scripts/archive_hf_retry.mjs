/** Persisted HF admission control; no network, credential, or deletion operations. */
const kinds=new Set(['repository_commit_limit','api_rate_limit','concurrent_index_change','transient_or_unknown_outcome']);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export function legacyRateReceipt(status,mtimeSeconds){
 if(!status||status.state!=='blocked'||!/429\s+Too Many Requests/i.test(status.reason??''))return null;
 const observed=Math.max(Number(status.time)||0,mtimeSeconds);
 if(!Number.isFinite(observed)||observed<=0)throw Error('Recorded HF failure time required');
 const repository=/repository commits|repo commits/i.test(status.reason);
 const seconds=[...status.reason.matchAll(/retry after\s+(\d+)\s+seconds/gi)].map(m=>Number(m[1]));
 const wait=Math.max(repository?3600:300,...seconds);
 return {schema:'earthcraft.hf-retry.v1',kind:repository?'repository_commit_limit':'api_rate_limit',
  observed_at:observed,not_before:Math.ceil(observed+wait+(repository?30:5)),
  headers:{},source:'legacy_status_file_mtime',server_reset_confirmed:false};
}
export function slotTime(state){
 const times=[state.next_upload_at??0,state.hf_retry?.not_before??0];
 if(times.some(time=>!Number.isFinite(time)||time<0))throw Error('Invalid persisted HF retry time');
 return Math.max(...times);
}
export function reserveAttempt(state,id,now,interval=60){
 if(!Number.isFinite(interval)||interval<60||interval>3600)throw Error('HF commit interval must be 60..3600 seconds');
 if(now<slotTime(state))throw Error('HF retry window has not elapsed');
 state.next_upload_at=Math.ceil(now+interval);
 state.last_upload_attempt={id,time:now,sha256:state.pending?.sha256};
}
export function recordRetry(state,receipt,now){
 if(receipt?.schema!=='earthcraft.hf-retry.v1'||!kinds.has(receipt.kind)||
    receipt.attempt_id!==state.last_upload_attempt?.id||receipt.archive_sha256!==state.pending?.sha256||
    !Number.isFinite(receipt.observed_at)||receipt.observed_at<state.last_upload_attempt.time||
    !Number.isFinite(receipt.not_before)||receipt.not_before<receipt.observed_at)return false;
 const failures=(state.hf_failures??0)+1;
 const base=receipt.kind==='repository_commit_limit'?3600:receipt.kind==='api_rate_limit'?300:60;
 const safety=receipt.kind==='repository_commit_limit'?30:receipt.kind==='api_rate_limit'?5:0;
 const notBefore=Math.ceil(Math.max(slotTime(state),receipt.not_before,now+base*2**Math.min(failures-1,2)+safety));
 state.hf_failures=failures;state.hf_retry={...receipt,not_before:notBefore};
 return true;
}
export function markVerified(state){
 state.hf_failures=0;delete state.hf_retry;
}
export async function waitForSlot(state,{now=()=>Date.now()/1000,pause=sleep,shouldStop=()=>false,
 publish=async()=>{},once=false,maxFailures=3}={}){
 if(!Number.isInteger(maxFailures)||maxFailures<1||maxFailures>5)throw Error('Bounded HF retry count required');
 if((state.hf_failures??0)>=maxFailures){
  await publish({state:'blocked',reason:'Bounded HF retries exhausted; operator review required',
   next_retry_at:new Date(slotTime(state)*1000).toISOString(),retry_failures:state.hf_failures});return false;
 }
 let lastStatus=-Infinity;
 while(now()<slotTime(state)&&!shouldStop()){
  if(now()-lastStatus>=60){lastStatus=now();await publish({state:state.hf_retry?'rate_limited':'pacing',
   retry_kind:state.hf_retry?.kind,next_retry_at:new Date(slotTime(state)*1000).toISOString(),
   retry_failures:state.hf_failures??0,pending_preserved:!!state.pending});}
  if(once)return false;
  await pause(Math.min(1000,Math.ceil((slotTime(state)-now())*1000)));
 }
 return !shouldStop();
}
