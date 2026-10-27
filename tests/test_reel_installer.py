import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import contextlib
import io
import ast

SPEC=importlib.util.spec_from_file_location('reel_installer_checks',Path(__file__).parents[1]/'scripts/install-reel-worker.py')
i=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(i)


class InstallationTests(unittest.TestCase):
    def test_stale_staging_is_preserved_and_source_is_read_once(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            home=Path(d);source=Path(i.__file__).with_name('reel-worker.py');data=source.read_bytes();digest=i.hashlib.sha256(data).hexdigest()
            stale=home/'.local/libexec/k2b-reel'/('staging-'+digest+'-interrupted');stale.mkdir(parents=True);(stale/'worker.py').write_bytes(b'incomplete')
            account=types.SimpleNamespace(pw_dir=str(home),pw_name='keithmbpm2');actual=Path.read_bytes;reads=[]
            def read(path):
                if path==source:reads.append(path)
                return actual(path)
            with patch.object(i.pwd,'getpwuid',return_value=account),patch.object(Path,'read_bytes',read),patch.object(Path,'chmod'),patch.object(i.subprocess,'check_output',return_value=json.dumps({'worker_sha256':digest})),contextlib.redirect_stdout(io.StringIO()):
                i.install('home',digest)
            release=home/'.local/libexec/k2b-reel'/digest
            self.assertEqual(reads,[source]);self.assertEqual((release/'worker.py').read_bytes(),data)
            self.assertEqual(json.loads((release/'config.json').read_text())['worker_sha256'],digest)
            self.assertEqual((stale/'worker.py').read_bytes(),b'incomplete')
            rules=home/'.codex/rules/k2b-reel-v1.rules'
            patterns=[ast.literal_eval(next(k.value for k in statement.value.keywords if k.arg=='pattern')) for statement in ast.parse(rules.read_text()).body]
            launcher=str(home/'.local/libexec/k2b-reel-v1')
            self.assertEqual(patterns,[[launcher,action] for action in ['version','status','inventory','process','save-digest']])
            worker_spec=importlib.util.spec_from_file_location('installer_argv_worker',source);worker=importlib.util.module_from_spec(worker_spec);worker_spec.loader.exec_module(worker)
            run='reel-123-20261027T000000Z-aabbccdd'
            cases=[['version'],['inventory'],['status',run],['capture','https://www.facebook.com/reel/123/'],['process',run,'0'*64],['save-digest',run,'0'*64]]
            for arguments in cases:
                worker.arguments(arguments)
                with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):worker.arguments([*arguments,'--output','/tmp/forbidden'])
            self.assertNotIn('recover-local',[pattern[1] for pattern in patterns])

    def test_concurrent_install_is_rejected_before_release_writes(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            home=Path(d);state=home/'.local/state/k2b';state.mkdir(parents=True)
            account=types.SimpleNamespace(pw_dir=str(home),pw_name='keithmbpm2')
            with (state/'reel-install.lock').open('a') as held:
                i.fcntl.flock(held,i.fcntl.LOCK_EX|i.fcntl.LOCK_NB)
                with patch.object(i.pwd,'getpwuid',return_value=account),self.assertRaises(SystemExit):i.install('home','0'*64)
            self.assertFalse((home/'.local/libexec').exists())

    def test_atomic_write_syncs_the_parent_directory(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            target=Path(d)/'file'
            actual=i.sync_directory;calls=[]
            def sync(path):calls.append(path);actual(path)
            with patch.object(i,'sync_directory',side_effect=sync):i.atomic_write(target,b'complete',0o600)
            self.assertEqual(calls,[target.parent]);self.assertEqual(target.read_bytes(),b'complete')
            self.assertEqual(target.stat().st_mode&0o777,0o600)


if __name__=='__main__':unittest.main()
