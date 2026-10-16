import test from 'node:test';
import assert from 'node:assert/strict';
import {retryTransport} from '../scripts/archive_transport_retry.mjs';
const failure = (code = 255, message = 'Connection reset', executable = '/usr/bin/ssh') =>
  Object.assign(new Error(message), {code, executable});

test('connection recovery repeats transport with bounded backoff', async () => {
  let calls=0;const delays=[];const notices=[];
  const value=await retryTransport(async()=>{if(++calls<3)throw failure();return 'same-checkpoint';},
    {sleep:async ms=>delays.push(ms),onRetry:async info=>notices.push(info)});
  assert.equal(value,'same-checkpoint');assert.equal(calls,3);
  assert.deepEqual(delays,[5000,10000]);assert.equal(notices.length,2);
});
test('authentication and exporter failures are never retried', async () => {
  for(const error of [failure(255,'Permission denied (publickey)'),failure(1,'Coordinate mismatch')]){
    let calls=0;
    await assert.rejects(retryTransport(async()=>{calls++;throw error;},{sleep:async()=>{}}));
    assert.equal(calls,1);
  }
});
test('persistent transport failure reaches a finite boundary',async()=>{
  let calls=0;
  await assert.rejects(retryTransport(async()=>{calls++;throw failure(30,'timeout','/usr/bin/rsync');},
    {sleep:async()=>{}}));assert.equal(calls,4);
});
test('resource deadline stops retries',async()=>{
  let calls=0;
  await assert.rejects(retryTransport(async()=>{calls++;throw failure();},
    {shouldStop:()=>true,sleep:async()=>assert.fail('Deadline must stop immediately')}));
  assert.equal(calls,1);
});
test('explicit transport timeouts retain the four-attempt limit and backoff',async()=>{
 for(const executable of ['/usr/bin/ssh','/usr/bin/rsync']){
  let calls=0;const delays=[];
  const error=Object.assign(failure('ETIMEDOUT','command timeout',executable),{timedOut:true});
  await assert.rejects(retryTransport(async()=>{calls++;throw error;},{sleep:async ms=>delays.push(ms)}));
  assert.equal(calls,4);assert.deepEqual(delays,[5000,10000,20000]);
 }
});
test('uploader timeouts, authentication errors and output bounds are not transport retries',async()=>{
 for(const error of [Object.assign(failure('ETIMEDOUT','timeout','/fake/uploader'),{timedOut:true}),
  Object.assign(failure('ETIMEDOUT','Permission denied (publickey)'),{timedOut:true}),
  failure('ERR_OUTPUT_LIMIT','output exceeds bound')]){
  let calls=0;
  await assert.rejects(retryTransport(async()=>{calls++;throw error;},{sleep:async()=>{}}));
  assert.equal(calls,1);
 }
});
