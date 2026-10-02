/** Reuse one task-owned SSH connection on the MacBook, keeping keys here. */
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';

export function sshOptions(control,config={}){
  const timeout=config.ssh_connect_timeout??60;
  const family=config.ssh_address_family??'inet';
  const task=crypto.createHash('sha256').update(path.resolve(control)).digest('hex').slice(0,8);
  // OpenSSH adds a temporary suffix; macOS sockaddr_un allows only 104 bytes.
  const socket=path.join(os.homedir(),'.ssh','ec-'+task+'-%C');
  if(!Number.isInteger(timeout)||timeout<1||timeout>90||!['inet','inet6','any'].includes(family))throw Error('Invalid bounded mini connection settings');
  return ['-o','BatchMode=yes','-o','ConnectTimeout='+timeout,'-o','AddressFamily='+family,
    '-o','ControlMaster=auto','-o','ControlPersist=60','-o','ControlPath='+socket];
}
