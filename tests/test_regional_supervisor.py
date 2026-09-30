import fcntl
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from regional_supervisor import ensure


class SupervisorTests(unittest.TestCase):
    def arguments(self,root):
        return SimpleNamespace(control=root,bulk=root/'bulk',frame=root/'frame.json',
                               illinois=root/'source.osm.pbf',reserve_gib=150)

    def test_existing_lock_deadline_and_failure_never_spawn_another_supervisor(self):
        with tempfile.TemporaryDirectory() as directory,patch('regional_supervisor.subprocess.Popen') as spawn:
            root=Path(directory);args=self.arguments(root)
            with (root/'supervisor.lock').open('a+') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertEqual(ensure(args)['state'],'running')
            (root/'supervisor-deadline.json').write_text('0')
            self.assertEqual(ensure(args)['state'],'generation_deadline')
            (root/'supervisor-deadline.json').unlink()
            (root/'supervisor-status.json').write_text(json.dumps({'failed_restarts':{'scans-1':10}}))
            self.assertEqual(ensure(args)['state'],'worker_failure_boundary')
            (root/'supervisor-status.json').write_text(json.dumps({'completed_workers':['base-1','base-2','scans-1']}))
            self.assertEqual(ensure(args)['state'],'complete');spawn.assert_not_called()

    def test_start_is_detached_and_retains_original_task_paths(self):
        with tempfile.TemporaryDirectory() as directory,patch('regional_supervisor.subprocess.Popen') as spawn:
            root=Path(directory);spawn.return_value.pid=1234;args=self.arguments(root)
            result=ensure(args)
            self.assertEqual(result['supervisor_pid'],1234)
            command=spawn.call_args.args[0];self.assertNotIn('--ensure',command)
            self.assertIn(str(args.control.resolve()),command)
            for path in (args.bulk,args.frame,args.illinois):self.assertIn(str(path),command)
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            self.assertEqual(json.loads((root/'supervisor-start.json').read_text()),result)


if __name__=='__main__':unittest.main()
