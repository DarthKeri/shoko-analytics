import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import startup_diagnostics as diagnostic
import bootstrap

ROOT = Path(__file__).resolve().parent.parent


class StartupDiagnosticsTests(unittest.TestCase):
    def test_credentials_urls_and_user_home_are_redacted(self):
        secrets = ["client_secret='secret-1'", '"refresh_token": "secret-2"',
                   'Authorization: Bearer secret-3', 'password=secret-4',
                   'https://google.example/callback?code=secret-5&state=secret-6',
                   'ya29.secret-7', 'GOCSPX-secret-8', 'Bearer secret-9']
        with patch.dict(os.environ, {'USERPROFILE': r'C:\Users\PrivateName'}):
            text = diagnostic.sanitize('\n'.join(secrets) + '\n' + r'C:\Users\PrivateName\folder')
        for i in range(1, 10): self.assertNotIn('secret-' + str(i), text)
        self.assertNotIn('PrivateName', text)
        self.assertIn('[REDACTED]', text)

    def test_traceback_keeps_exception_and_does_not_collect_local_values(self):
        try:
            private_local = 'private-local-value'
            raise RuntimeError('missing component')
        except RuntimeError as error:
            text = diagnostic.exception_text(error)
        self.assertIn('RuntimeError: missing component', text)
        self.assertNotIn('private-local-value', text)

    def test_stage_is_noop_without_launcher_context(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'open') as opened:
            diagnostic.stage('test')
        opened.assert_not_called()

    def test_stage_bounds_details_and_masks_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'details'
            with patch.dict(os.environ, {'SHOKO_STARTUP_DETAILS': str(path)}):
                diagnostic.stage('phase; refresh_token=private-token')
                self.assertNotIn('private-token', path.read_text(encoding='utf-8'))
                path.write_bytes(b'x'*131072)
                diagnostic.stage('must not grow')
                self.assertEqual(path.stat().st_size, 131072)

    def test_report_write_failure_does_not_crash_startup(self):
        with patch.dict(os.environ, {'SHOKO_STARTUP_DETAILS': 'blocked'}), patch.object(Path, 'open', side_effect=PermissionError('blocked')):
            diagnostic.stage('some stage')

    def test_workspace_validation_does_not_modify_settings_or_copy_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'ShokoAnalytics'; root.mkdir()
            path=root/'settings.local.json'
            path.write_text(json.dumps({'google_oauth_client':'private-configuration','password':'private-password'}))
            before=path.read_bytes(); output=io.StringIO()
            with patch.dict(os.environ, {'LOCALAPPDATA':temp}), contextlib.redirect_stdout(output):
                self.assertTrue(diagnostic.check_workspace())
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn('private-configuration', output.getvalue())
            self.assertNotIn('private-password', output.getvalue())

    def test_early_import_failure_has_traceback_before_engine_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'details'
            with patch.dict(os.environ, {'SHOKO_STARTUP_DETAILS':str(path)}), \
                 patch.object(diagnostic, 'initialize'), \
                 patch.object(bootstrap.runpy, 'run_path', side_effect=ModuleNotFoundError('missing startup library')), \
                 patch.object(sys, 'argv', ['bootstrap.py']), patch.object(sys,'stderr',io.StringIO()):
                self.assertEqual(bootstrap.run(), 1)
            text=path.read_text(encoding='utf-8')
            self.assertIn('ModuleNotFoundError', text)
            self.assertIn('missing startup library', text)

    def test_successful_bootstrap_runs_entry_without_exit_wrapper(self):
        called=[]
        with patch.object(diagnostic, 'initialize'), patch.object(diagnostic, 'stage'), \
             patch.object(bootstrap.runpy,'run_path',return_value={'main':lambda:called.append(True)}), \
             patch.object(sys,'argv',['bootstrap.py']):
            self.assertEqual(bootstrap.run(),0)
        self.assertEqual(called,[True])


@unittest.skipUnless(os.name=='nt','Windows EXE integration')
class LauncherReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            cls.temp.cleanup(); raise unittest.SkipTest('C# compiler unavailable')
        cls.exe=cls.root/'launcher.exe'
        result=subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64',
                               '/codepage:65001','/reference:System.Windows.Forms.dll',
                               '/out:'+str(cls.exe),str(ROOT/'tools/Launcher.cs')],capture_output=True)
        if result.returncode:raise RuntimeError(result.stdout.decode(errors='replace'))
        candidates=[Path(sys.executable).parent, ROOT/'dist/ShokoAnalytics/runtime']
        cls.runtime=next((p for p in candidates if (p/'python313.zip').is_file()),None)
        if cls.runtime is None:
            cls.temp.cleanup(); raise unittest.SkipTest('Embeddable Python unavailable')

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.instance=tempfile.TemporaryDirectory(dir=self.root)
        self.app=Path(self.instance.name)/'Папка приложения с пробелом'
        (self.app/'app').mkdir(parents=True); (self.app/'runtime').mkdir()
        shutil.copy2(self.exe,self.app/'ShokoAnalytics.exe')
        for p in self.runtime.iterdir():
            if p.is_file() and p.suffix.lower() in ('.exe','.dll','.zip','._pth'):
                shutil.copy2(p,self.app/'runtime'/p.name)
        for name in ('bootstrap.py','startup_diagnostics.py'):
            shutil.copy2(ROOT/name,self.app/'app'/name)

    def tearDown(self): self.instance.cleanup()

    def launch(self, source):
        (self.app/'app/main.py').write_text(source,encoding='utf-8')
        result=subprocess.run([str(self.app/'ShokoAnalytics.exe'),'--smoke'],cwd=self.app,timeout=20)
        reports=list((self.app/'diagnostics').glob('*.txt'))
        return result.returncode, reports

    def test_import_failure_creates_readable_automatic_report(self):
        code,reports=self.launch('import nonexistent_shoko_startup_module\n')
        self.assertNotEqual(code,0); self.assertEqual(len(reports),1)
        text=reports[0].read_text(encoding='utf-8-sig')
        self.assertIn('ModuleNotFoundError',text)
        self.assertIn('nonexistent_shoko_startup_module',text)
        self.assertIn('0x00000001',text)
        self.assertIn('Загрузка точки входа',text)

    def test_missing_python_is_reported_without_running_python(self):
        (self.app/'runtime/pythonw.exe').unlink()
        code,reports=self.launch('def main(): pass\n')
        self.assertNotEqual(code,0); self.assertEqual(len(reports),1)
        self.assertIn('НЕТ ФАЙЛА: runtime/pythonw.exe',reports[0].read_text(encoding='utf-8-sig'))

    def test_process_termination_retains_last_stage_and_exit_code(self):
        code,reports=self.launch("from startup_diagnostics import stage\nimport os\ndef main():\n stage('native process stopped')\n os._exit(23)\n")
        self.assertEqual(code,23)
        text=reports[0].read_text(encoding='utf-8-sig')
        self.assertIn('native process stopped',text)
        self.assertIn('0x00000017',text)

    def test_reports_do_not_leak_tokens_from_stdout_stderr_or_exception(self):
        code,reports=self.launch("import sys\ndef main():\n print('access_token=never-show-stdout')\n print('Bearer never-show-stderr',file=sys.stderr)\n raise RuntimeError('client_secret=never-show-exception')\n")
        self.assertNotEqual(code,0)
        text=reports[0].read_text(encoding='utf-8-sig')
        for value in ('never-show-stdout','never-show-stderr','never-show-exception'):self.assertNotIn(value,text)
        self.assertIn('[REDACTED]',text)

    def test_large_stdout_and_stderr_do_not_deadlock_and_report_is_bounded(self):
        code,reports=self.launch("import sys\ndef main():\n print('o'*90000)\n print('e'*90000,file=sys.stderr)\n raise RuntimeError('output test complete')\n")
        self.assertNotEqual(code,0)
        self.assertLess(reports[0].stat().st_size,300000)
        self.assertIn('output test complete',reports[0].read_text(encoding='utf-8-sig'))

    def test_success_does_not_leave_false_failure_reports(self):
        code,reports=self.launch('def main(): pass\n')
        self.assertEqual(code,0); self.assertEqual(reports,[])
        self.assertEqual(list((self.app/'diagnostics').glob('*.details')),[])
