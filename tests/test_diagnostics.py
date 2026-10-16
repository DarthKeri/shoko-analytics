import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from requests.exceptions import ReadTimeout, SSLError
from shoko.adapters import google_http_request
from shoko.storage import Store, AppError, file_hash
from shoko.engine import Engine
from shoko.diagnostics import occupied_space,health
from shoko.selftest import run_selftest
from test_mvp import fixtures


class DiagnosticsTests(unittest.TestCase):
    def test_http_error_is_sanitized_and_persisted_across_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp)
            response=SimpleNamespace(status_code=403,json=lambda:{'error':{'message':'private-body-token', 'status':'PERMISSION_DENIED','errors':[{'reason':'insufficientFilePermissions'},{'reason':'secret-with-url=https://private'}]}})
            session=SimpleNamespace(request=Mock(return_value=response),shoko_diagnostics=store.google_event)
            google_http_request(session,'POST','https://www.googleapis.com/drive/v3/files/private-id/copy?secret=private-token',json={'private':'payload'},timeout=(45,180))
            errors=Store(temp).google_errors();self.assertEqual(len(errors),1);record=errors[0]
            self.assertEqual(record['operation'],'Создание резервной копии');self.assertEqual(record['status'],403)
            self.assertEqual(record['read_timeout'],180);self.assertIn('insufficientFilePermissions',record['reason'])
            text=json.dumps(errors);self.assertNotIn('private',text);self.assertNotIn('payload',text);self.assertNotIn('https:',text)
            session.request.assert_called_once()

    def test_transport_retries_are_recorded_but_writes_are_never_replayed(self):
        records=[];response=SimpleNamespace(status_code=200)
        session=SimpleNamespace(request=Mock(side_effect=[ReadTimeout('private'),response]),shoko_diagnostics=records.append)
        with patch('shoko.adapters.time.sleep'):google_http_request(session,'GET','https://sheets.googleapis.com/v4/spreadsheets/private')
        self.assertEqual(len(records),1);self.assertTrue(records[0]['retry']);self.assertEqual(records[0]['transport'],'ReadTimeout')
        records.clear();session.request=Mock(side_effect=ReadTimeout('private-token'))
        with self.assertRaises(ReadTimeout):google_http_request(session,'POST','https://sheets.googleapis.com/v4/spreadsheets/private:batchUpdate',timeout=(45,180))
        session.request.assert_called_once();self.assertFalse(records[0]['retry']);self.assertEqual(records[0]['attempts'],1)

    def test_tls_error_is_recorded_without_retry_or_private_exception_text(self):
        records=[];session=SimpleNamespace(request=Mock(side_effect=SSLError('private-token')),shoko_diagnostics=records.append)
        with self.assertRaises(SSLError):google_http_request(session,'GET','https://sheets.googleapis.com/v4/spreadsheets/private')
        self.assertFalse(records[0]['retry']);session.request.assert_called_once();self.assertNotIn('private',json.dumps(records))

    def test_error_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp)
            for n in range(105):store.google_event({'attempt':n})
            self.assertEqual(len(store.google_errors()),100);self.assertEqual(store.google_errors()[0]['attempt'],5)

    def test_space_counts_without_modifying_or_deleting_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'inputs').mkdir();(root/'backups').mkdir();(root/'inputs/a.csv').write_bytes(b'x'*123);(root/'backups/b.json').write_bytes(b'x'*234);(root/'settings.local.json').write_bytes(b'{}')
            before={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()};result=occupied_space(root)
            self.assertEqual(result['bytes'],359);self.assertEqual(result['files'],3);self.assertFalse(result['partial'])
            self.assertEqual(before,{str(p):file_hash(p) for p in root.rglob('*') if p.is_file()})
            self.assertTrue(occupied_space(root,max_files=1)['partial'])

    def test_space_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temp,tempfile.TemporaryDirectory() as outside:
            Path(outside,'secret').write_bytes(b'x'*1000)
            try:Path(temp,'link').symlink_to(outside,target_is_directory=True)
            except OSError:self.skipTest('Symlink creation unavailable')
            result=occupied_space(temp);self.assertEqual(result['bytes'],0);self.assertEqual(result['skipped'],1)

    def test_selftest_never_connects_to_google_or_touches_user_settings(self):
        with patch('requests.sessions.Session.request',side_effect=AssertionError('Network forbidden')) as network:
            result=run_selftest();network.assert_not_called()
        self.assertTrue(result['ok'],result);self.assertEqual(len(result['items']),6)

    def test_developer_diagnostics_preserve_reviewed_plan_and_target(self):
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(Path(temp)/'data')
            try:
                paths=fixtures(Path(temp)/'files');engine.save_settings(dict(paths,developer=True));engine.prepare('',paths['last_date']);engine.worker.join(20)
                checksum=engine.plan['checksum'];before=file_hash(paths['target'])
                for kind in ('health','space','selftest'):
                    engine.developer_action(kind);engine.worker.join(30)
                    self.assertEqual(engine.state['phase'],'preview',engine.state['error']);self.assertEqual(engine.plan['checksum'],checksum)
                    self.assertIn(kind,engine.public_state()['developer']);self.assertEqual(file_hash(paths['target']),before)
                    self.assertEqual(engine.store.copies(),[])
            finally:engine.close()

    def test_diagnostics_require_developer_mode_and_respect_busy_state(self):
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp)
            try:
                with self.assertRaisesRegex(AppError,'разработчика'):engine.developer_action('selftest')
                engine.save_settings({'developer':True});engine.state['phase']='writing'
                with self.assertRaisesRegex(AppError,'операция'):engine.developer_action('space')
                with self.assertRaisesRegex(AppError,'Неизвестная'):engine.developer_action('other')
            finally:engine.close()

if __name__=='__main__':unittest.main()
