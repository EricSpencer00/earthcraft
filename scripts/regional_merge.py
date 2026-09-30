"""Compute one private save-region merge on the mini; never touch the local save."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from regional_install import install
from regional_publish_unpack import unpack
from region_expansion import atomic,sha


def merge(task,bundle):
    task=Path(task);replica=task/'current';root=task/'candidate'
    if shutil.disk_usage(task).free<12*2**30:raise ValueError('Preserve private merge staging disk reserve')
    request=json.loads((task/'request.json').read_text())
    for name,checksum in request['originals'].items():
        if sha(replica/name)!=checksum:raise ValueError('Transferred current-save snapshot differs')
    entries=unpack(bundle,root);reports=[]
    for entry in entries:
        candidate=root/entry['tile']
        if sha(candidate/'receipt.json')!=entry['receipt_sha256']:raise ValueError('Frozen candidate receipt changed')
        reports.append(install(replica,candidate/'world',candidate/'receipt.json',task/'backups',
            [root/name for name in entry['baselines']] or None))
    names=set(request['originals'])
    for entry in entries:
        receipt=json.loads((root/entry['tile']/'receipt.json').read_text())
        names.update('region/'+name for name in receipt['regions'])
    names.update(('regional-quality.json','city-coverage.json','level.dat','data/world_border.dat'))
    changed={name:sha(replica/name) for name in sorted(names) if (replica/name).is_file() and
             sha(replica/name)!=request['originals'].get(name)}
    result={'originals':request['originals'],'changed':changed,'reports':reports,'entries':entries}
    atomic(task/'result.json',json.dumps(result,indent=2).encode());return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True)
    a=p.parse_args();result=merge(a.task,a.bundle)
    print(json.dumps({'result':str(a.task/'result.json'),'changed_files':len(result['changed'])}))
