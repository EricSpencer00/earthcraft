"""Install only the tested importer jar while the single player world is closed.

Preserves the old jar and hashes every world file before/after installation.
Does not rewrite the save, change profiles, start Java, or publish new patches.
"""
import argparse
import fcntl
import json
from pathlib import Path
import shutil

from live_city import ROOT, atomic, sha
from world_replay import files_snapshot


def upgrade(evidence_path,output):
    evidence_path,output=Path(evidence_path),Path(output)
    evidence=json.loads(evidence_path.read_text())
    for gate in ('passed','two_load_save_cycles_verified','player_edit_preserved',
                 'block_entity_preserved','unowned_chunk_preserved','replay_already_target_verified'):
        if evidence.get(gate) is not True:raise ValueError('Missing native verification: '+gate)
    if evidence.get('saved_block_cells_compared',0)<=0:raise ValueError('Native block readback required')
    jar=ROOT/'vendor/live/earthcraft-live-0.1.0.jar'
    installed=ROOT/'runtime/traversal/mods'/jar.name
    if sha(jar)!=evidence['mod_sha256']:raise ValueError('Importer is not the exact verified binary')
    world=ROOT/'runtime/traversal/saves/Earthcraft'
    if output.exists():raise FileExistsError(output)
    with (world/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=files_snapshot(world)
        output.mkdir(parents=True)
        shutil.copy2(installed,output/'previous-importer.jar')
        old_hash=sha(installed)
        if sha(output/'previous-importer.jar')!=old_hash:raise ValueError('Importer backup failed')
        atomic(installed,jar.read_bytes())
        if sha(installed)!=evidence['mod_sha256'] or files_snapshot(world)!=before:
            raise ValueError('Installed jar or untouched world verification failed')
        result={'installed_jar_sha256':sha(installed),'previous_jar_sha256':old_hash,
                'native_evidence_sha256':sha(evidence_path),'world_files_before_after_identical':True,
                'world_snapshot':before,'requires_client_restart':True,'world_files_written':0}
        atomic(output/'verification.json',json.dumps(result,indent=2).encode())
        return {k:v for k,v in result.items() if k!='world_snapshot'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(upgrade(a.evidence,a.output),indent=2))
