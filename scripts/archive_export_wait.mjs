/** Wait on the existing lock without launching another exporter or touching bulk IO. */
export const lockProbeProgram=`import fcntl,json,os,stat,sys
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW)
try:
 info=os.fstat(fd)
 if not stat.S_ISREG(info.st_mode):raise ValueError('Persistent export lock is not regular')
 state='busy'
 try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);state='available'
 except BlockingIOError:pass
 print(json.dumps({'state':state,'inode':info.st_ino}))
finally:os.close(fd)
`;

export function checkedExportLock(lock,expectedInode) {
 if(!Number.isSafeInteger(expectedInode)||expectedInode<1)throw Error('Verified persistent export lock identity required');
 if(lock.inode!==expectedInode)throw Error('Persistent export lock inode changed; halt before exporter launch');
 if(!['available','busy'].includes(lock.state))throw Error('Invalid persistent export lock readiness');
 return lock.state==='available';
}

export async function waitForExportLock({read,publish,pause,shouldStop,expectedInode}) {
 while(!shouldStop()) {
  await pause();
  if(shouldStop())return false;
  let lock;
  try{lock=await read();}
  catch(error){await publish({state:'waiting_on_storage',phase:'export_lock_probe',reason:'Lock readiness unavailable; original work remains preserved'});continue;}
  if(shouldStop())return false;
  if(checkedExportLock(lock,expectedInode))return true;
  await publish({state:'waiting_on_storage',phase:'unresolved_export_io',reason:'Prior IO worker still holds its inherited lock'});
 }
 return false;
}
