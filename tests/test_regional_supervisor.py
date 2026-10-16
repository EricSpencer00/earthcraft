import fcntl
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import regional_supervisor as rs


class SupervisorTests(unittest.TestCase):
    def arguments(self,root,*extra):
        return rs.build_parser().parse_args(['--control',str(root/'control'),'--frame',str(root/'frame.json'),
                                             '--scratch',str(root/'scratch'),'--min-free','0',*extra])

    def test_existing_lock_deadline_and_failure_never_spawn_another_supervisor(self):
        with tempfile.TemporaryDirectory() as directory,patch('regional_supervisor.subprocess.Popen') as spawn:
            root=Path(directory);args=self.arguments(root);control=root/'control';control.mkdir()
            with (control/'supervisor.lock').open('a+') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertEqual(rs.ensure(args)['state'],'running')
            (control/'supervisor-deadline.json').write_text('0')
            self.assertEqual(rs.ensure(args)['state'],'generation_deadline')
            (control/'supervisor-deadline.json').unlink()
            (control/'supervisor-status.json').write_text(json.dumps({'failed_restarts':{'base-1':10}}))
            self.assertEqual(rs.ensure(args)['state'],'worker_failure_boundary')
            (control/'supervisor-status.json').write_text(json.dumps({'completed_workers':['base-1']}))
            self.assertEqual(rs.ensure(args)['state'],'complete');spawn.assert_not_called()

    def test_start_is_detached_low_priority_and_keeps_the_guard_settings(self):
        with tempfile.TemporaryDirectory() as directory,patch('regional_supervisor.subprocess.Popen') as spawn:
            root=Path(directory);spawn.return_value.pid=1234
            args=self.arguments(root,'--workers','2','--hf-python','/opt/hf/python','--footprints')
            result=rs.ensure(args)
            self.assertEqual(result['supervisor_pid'],1234)
            command=spawn.call_args.args[0];self.assertNotIn('--ensure',command)
            self.assertEqual(command[command.index('--workers')+1],'2')
            self.assertIn('--footprints',command);self.assertIn('/opt/hf/python',command)
            self.assertTrue(command[0].endswith('taskpolicy') or command[0]=='nice')
            self.assertEqual(command[command.index('--qos')+1],'utility')
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            self.assertEqual(json.loads((root/'control/supervisor-start.json').read_text()),result)

    def test_worker_command_runs_the_one_pass_generator_in_the_background_band(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);args=self.arguments(root,'--lane','scans')
            command=rs.worker_command(args,'scans-1')
            self.assertIn('regional_generate.py',' '.join(command))
            self.assertEqual(command[command.index('--lane')+1],'scans')
            self.assertEqual(command[command.index('--worker-id')+1],'scans-1')
            if sys.platform=='darwin':self.assertEqual(command[:6],['/usr/sbin/taskpolicy','-c','utility','nice','-n','10'])
            strict=rs.worker_command(self.arguments(root,'--qos','background'),'base-1')
            if sys.platform=='darwin':self.assertEqual(strict[:2],['/usr/sbin/taskpolicy','-b'])
            self.assertFalse(any('LaCie' in part or 'bulk' in part for part in command))


if __name__=='__main__':unittest.main()
