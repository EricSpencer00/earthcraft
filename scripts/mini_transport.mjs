/** Reuse one task-owned SSH connection on the MacBook, keeping keys here. */
import path from 'node:path';

export function sshOptions(control,config={}){
  const timeout=config.ssh_connect_timeout??60;
  const family=config.ssh_address_family??'inet';
  if(!Number.isInteger(timeout)||timeout<1||timeout>90||!['inet','inet6','any'].includes(family))throw Error('Invalid bounded mini connection settings');
  return ['-o','BatchMode=yes','-o','ConnectTimeout='+timeout,'-o','AddressFamily='+family,
    '-o','ControlMaster=auto','-o','ControlPersist=60','-o','ControlPath='+path.join(control,'ssh-%C')];
}
