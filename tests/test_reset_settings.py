import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from shoko.engine import Engine
from shoko.server import Server
from shoko.storage import AppError, Store, atomic_json, defaults, file_hash
from test_mvp import fixtures


class ResetSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name) / 'profile')
        self.engine.save_settings({'prostoi': 'keep.xlsx', 'appearance': 'apple', 'developer': True})
        self.engine.google.vault.write_bytes(b'protected-fixture-login')

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def test_reset_requires_explicit_boolean_confirmation(self):
        server = Server(self.engine, self.temp.name)
        before = file_hash(self.engine.store.config_path)
        try:
            for value in (None, False, 'true', 1):
                with self.assertRaisesRegex(AppError, 'Подтвердите'):
                    server.action({'action': 'reset-settings', 'confirmed': value})
                self.assertEqual(file_hash(self.engine.store.config_path), before)
                self.assertTrue(self.engine.google.vault.exists())
        finally: server.close()

    def test_reset_is_blocked_during_every_active_stage(self):
        before = file_hash(self.engine.store.config_path)
        for phase in ('preparing', 'writing', 'checking', 'connecting'):
            self.engine.state['phase'] = phase
            with self.assertRaisesRegex(AppError, 'Дождитесь'):
                self.engine.reset_settings(True)
            self.assertEqual(file_hash(self.engine.store.config_path), before)
            self.assertTrue(self.engine.google.vault.exists())
        self.engine.state['phase'] = 'idle'

    def test_reset_preserves_data_and_copies_blocks_old_approval_and_survives_restart(self):
        paths = fixtures(Path(self.temp.name) / 'inputs')
        self.engine.save_settings(paths)
        self.engine.store.config['obsolete_setting'] = 'remove-me'
        backup = self.engine.store.backup_file(paths['target'])
        self.engine.prepare('', paths['last_date'], False)
        self.engine.worker.join(15)
        self.assertEqual(self.engine.state['phase'], 'preview', self.engine.state['error'])
        plan = self.engine.plan
        self.engine.checks = {'prostoi': {'ok': True}}
        self.engine.developer_results = {'space': {'bytes': 1}}
        root = self.engine.store.root
        atomic_json(root / 'plans/old.result.json', {'id': 'old', 'created': '2026-10-16T08:00:00+00:00', 'status': 'done'})
        kept = [p for sub in ('plans', 'backups', 'inputs', 'output') for p in (root / sub).rglob('*') if p.is_file()]
        kept += [Path(paths[k]) for k in ('prostoi', 'drivers', 'csv', 'target')]
        kept += [root / 'logs/activity.json']
        hashes = {p: file_hash(p) for p in kept}
        server = Server(self.engine, self.temp.name)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Reset must stay offline')):
            try: result = server.action({'action': 'reset-settings', 'confirmed': True})
            finally: server.close()
        self.assertEqual(result['config'], defaults())
        self.assertFalse(result['google_auth']['connected'])
        self.assertFalse(result['target_allowed'])
        self.assertIsNone(self.engine.plan)
        self.assertEqual(self.engine.checks, {})
        self.assertEqual(self.engine.developer_results, {})
        self.assertFalse(self.engine.google.vault.exists())
        self.assertEqual({p: file_hash(p) for p in kept}, hashes)
        self.assertEqual(self.engine.store.copies()[0]['id'], backup['id'])
        with self.assertRaises(AppError): self.engine.write(plan['id'], plan['checksum'])
        self.engine.close()
        self.engine = Engine(root)
        self.assertEqual(self.engine.config(), defaults())
        self.assertEqual(len(list(root.glob('settings*'))), 1)

    def test_vault_permission_error_leaves_settings_and_login_intact(self):
        before = file_hash(self.engine.store.config_path)
        with patch.object(self.engine.google, 'disconnect', side_effect=PermissionError('private-error')):
            with self.assertRaisesRegex(AppError, 'Настройки не удалены'):
                self.engine.reset_settings(True)
        self.assertEqual(file_hash(self.engine.store.config_path), before)
        self.assertTrue(self.engine.google.vault.exists())

    def test_readonly_journal_does_not_report_a_completed_reset_as_failed(self):
        with patch.object(self.engine.store, 'log', side_effect=PermissionError('private-error')):
            result = self.engine.reset_settings(True)
        self.assertEqual(result['config'], defaults())
        self.assertFalse(self.engine.google.vault.exists())

    def test_settings_permission_error_explains_partial_reset_without_losing_paths(self):
        before = file_hash(self.engine.store.config_path)
        with patch('shoko.storage.atomic_json', side_effect=PermissionError('private-error')):
            with self.assertRaisesRegex(AppError, 'Вход Google отключён'):
                self.engine.reset_settings(True)
        self.assertEqual(file_hash(self.engine.store.config_path), before)
        self.assertEqual(self.engine.config()['prostoi'], 'keep.xlsx')
        self.assertFalse(self.engine.google.vault.exists())


if __name__ == '__main__': unittest.main()
