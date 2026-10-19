"""Exercise the updater with real local Git changes and a fake service manager."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/aws_pipeline_setup.sh'
UPDATE='update() {'+SCRIPT.read_text().split('update() {',1)[1].split('\nvalidate() {',1)[0]


class PipelineUpdateTests(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.DEVNULL,text=True).strip()

    def case(self, changed, fail_restart=False):
        with tempfile.TemporaryDirectory(prefix='earthcraft-update-test-') as tmp:
            root=Path(tmp);origin=root/'origin';worker=root/'worker';origin.mkdir()
            self.git(origin,'init','-b','main');self.git(origin,'config','user.name','Update test');self.git(origin,'config','user.email','update@example.invalid')
            self.git(origin,'config','commit.gpgsign','false')
            for name in ['dashboard/app.js','scripts/worker.py','scripts/aws_pipeline_setup.sh','requirements-test.txt']:
                p=origin/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('before\n')
            self.git(origin,'add','.');self.git(origin,'commit','-m','Initial fixture')
            old=self.git(origin,'rev-parse','HEAD');self.git(root,'clone',str(origin),str(worker))
            (origin/changed).write_text('after\n');self.git(origin,'add',changed);self.git(origin,'commit','-m','Change fixture')
            new=self.git(origin,'rev-parse','HEAD');marker=root/'deployed';marker.write_text(old+'\n')
            installed=root/'installed';installed.write_text('before\n');calls=root/'calls'
            # Only system locations change; the actual Git fetch/diff/fast-forward logic runs unchanged.
            function=UPDATE.replace('/var/lib/earthcraft/deployed',str(marker)).replace('/usr/local/sbin/earthcraft-pipeline',str(installed))
            shell='set -eu\nas_user() { "$@"; }\nshard() { :; }\ninstall_requirements() { echo requirements >> "$CALLS"; }\nsystemctl() { echo "$*" >> "$CALLS"; [ "${FAIL_RESTART:-0}" != 1 ]; }\n'+function+'\nupdate\n'
            env={**os.environ,'ROOT':str(worker),'BRANCH':'main','CALLS':str(calls),'FAIL_RESTART':'1' if fail_restart else '0'}
            result=subprocess.run(['bash','-c',shell],env=env,capture_output=True,text=True)
            self.assertEqual(self.git(worker,'rev-parse','HEAD'),new)
            if fail_restart:
                self.assertNotEqual(result.returncode,0);self.assertEqual(marker.read_text().strip(),old)
                env['FAIL_RESTART']='0';result=subprocess.run(['bash','-c',shell],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(marker.read_text().strip(),new)
            return calls.read_text() if calls.exists() else ''

    def test_frontend_commit_does_not_restart_generator(self):
        self.assertEqual(self.case('dashboard/app.js'),'')

    def test_worker_commit_restarts_generator(self):
        self.assertIn('restart earthcraft-pipeline.service',self.case('scripts/worker.py'))

    def test_dependency_commit_installs_then_restarts(self):
        self.assertEqual(self.case('requirements-test.txt'),'requirements\nrestart earthcraft-pipeline.service\n')

    def test_failed_restart_keeps_marker_and_retries(self):
        self.assertEqual(self.case('scripts/worker.py',True).count('restart earthcraft-pipeline.service'),2)


if __name__=='__main__':
    unittest.main()
