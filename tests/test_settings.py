import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from shoko.engine import Engine
from shoko.storage import AppError, Store, atomic_json, file_hash, resolve_root
from shoko.server import Server
from test_mvp import fixtures


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'data'
        self.engine = Engine(self.root)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def wait(self):
        if self.engine.worker:
            self.engine.worker.join(15)
        self.assertFalse(self.engine.busy())

    def test_partial_paths_survive_restart_without_backups(self):
        self.assertFalse(self.engine.config()['initialized'])
        self.engine.save_settings({'prostoi': 'C:/data/Средние_цены_простои_2026.xlsx', 'drivers': 'C:/data/Начисление_08-26.xlsx', 'telegram_mode': 'auto'})
        self.engine.close()
        self.engine = Engine(self.root)
        self.assertTrue(self.engine.config()['initialized'])
        self.assertEqual(self.engine.config()['drivers'], 'C:/data/Начисление_08-26.xlsx')
        self.assertEqual(self.engine.config()['telegram_bot'], '')
        self.assertEqual(list((self.root / 'backups').iterdir()), [])
        self.assertEqual(len(list(self.root.glob('settings*'))), 1)

    def test_finish_incomplete_setup_never_checks_network_or_files(self):
        with patch.object(self.engine, 'check_one') as check:
            self.engine.finish_setup()
            check.assert_not_called()
        self.assertTrue(Store(self.root).config['initialized'])

    def test_repeated_save_uses_one_file_and_no_settings_copies(self):
        self.engine.save_settings({'prostoi': 'a.xlsx'})
        saved_time = self.engine.store.config_path.stat().st_mtime_ns
        self.engine.save_settings({'prostoi': 'a.xlsx'})
        self.assertEqual(self.engine.store.config_path.stat().st_mtime_ns, saved_time)
        self.engine.save_settings({'prostoi': 'b.xlsx'})
        self.assertEqual(Store(self.root).config['prostoi'], 'b.xlsx')
        self.assertEqual(list((self.root / 'backups').iterdir()), [])

    def test_existing_partial_setup_is_migrated_without_losing_paths(self):
        self.engine.close()
        atomic_json(self.root / 'settings.local.json', {'initialized': False, 'prostoi': 'legacy.xlsx'})
        self.engine = Engine(self.root)
        self.assertTrue(self.engine.config()['initialized'])
        self.assertEqual(self.engine.config()['prostoi'], 'legacy.xlsx')
        self.assertEqual(self.engine.store.copies(), [])

    def test_checks_keep_other_results_when_next_source_is_saved(self):
        paths = fixtures(Path(self.temp.name) / 'files')
        self.engine.save_settings({'prostoi': paths['prostoi'], 'last_date': paths['last_date']})
        self.engine.check('prostoi', paths['last_date']); self.wait()
        self.assertTrue(self.engine.checks['prostoi']['ok'])
        self.engine.save_settings({'drivers': paths['drivers']})
        self.engine.check('drivers', paths['last_date']); self.wait()
        self.assertTrue(self.engine.checks['prostoi']['ok'])
        self.assertTrue(self.engine.checks['drivers']['ok'])
        self.engine.save_settings({'prostoi': 'missing.xlsx'})
        self.assertNotIn('prostoi', self.engine.checks)
        self.assertTrue(self.engine.checks['drivers']['ok'])

    def test_failed_check_can_be_fixed_and_retried(self):
        paths = fixtures(Path(self.temp.name) / 'files')
        self.engine.check('prostoi', paths['last_date']); self.wait()
        self.assertFalse(self.engine.checks['prostoi']['ok'])
        self.engine.save_settings({'prostoi': paths['prostoi']})
        self.engine.check('prostoi', paths['last_date']); self.wait()
        self.assertTrue(self.engine.checks['prostoi']['ok'])
        self.assertEqual(self.engine.state['error'], '')

    def test_update_with_unconfigured_telegram_cannot_start_or_write(self):
        paths = fixtures(Path(self.temp.name) / 'files')
        before = file_hash(paths['target'])
        self.engine.save_settings(dict(paths, telegram_mode='auto'))
        self.engine.finish_setup()
        with patch.object(self.engine, 'launch') as launch:
            with self.assertRaisesRegex(AppError, 'Бот Telegram'):
                self.engine.prepare('https://garage.example.test/fleet/1', paths['last_date'])
            launch.assert_not_called()
        self.assertEqual(file_hash(paths['target']), before)
        self.assertIsNone(self.engine.plan)
        self.assertEqual(self.engine.store.copies(), [])
        self.engine.save_settings({'telegram_bot': '@fixture_bot'})
        with patch('telethon.TelegramClient') as client:
            self.engine.prepare('https://garage.example.test/fleet/1', paths['last_date'])
            self.wait()
            client.assert_not_called()
        self.assertIn('Telegram не подключён', self.engine.state['error'])
        self.assertEqual(file_hash(paths['target']), before)
        self.assertIsNone(self.engine.plan)
        self.assertEqual(self.engine.store.copies(), [])

    def test_old_settings_copies_are_hidden_but_excel_backups_remain(self):
        paths = fixtures(Path(self.temp.name) / 'files')
        self.engine.store.backup_json({'old': True}, 'settings', 'settings.local.json')
        backup = self.engine.store.backup_file(paths['target'])
        self.assertEqual([c['id'] for c in self.engine.store.copies()], [backup['id']])
        self.engine.store.copy(backup['id'])

    def test_working_folder_has_one_settings_file_after_relocation(self):
        server = Server(self.engine, self.root)
        new_root = Path(self.temp.name) / 'chosen'
        self.engine.store.save_config({'prostoi': 'source.xlsx'})
        try:
            server.action({'action': 'root', 'path': str(new_root)})
            self.assertEqual(resolve_root(self.root), new_root.resolve())
            self.assertFalse((self.root / 'settings.local.json').exists())
            self.assertEqual(Store(new_root).config['prostoi'], 'source.xlsx')
            self.assertNotIn('prostoi', json.loads((self.root / 'workspace.location.json').read_text()))
        finally:
            server.close()

    def test_old_root_redirect_uses_latest_settings_and_removes_duplicate(self):
        new_root = Path(self.temp.name) / 'chosen'
        atomic_json(self.root / 'settings.local.json', {'redirect_root': str(new_root), 'prostoi': 'old.xlsx'})
        atomic_json(new_root / 'settings.local.json', {'prostoi': 'new.xlsx'})
        self.assertEqual(resolve_root(self.root), new_root.resolve())
        self.assertFalse((self.root / 'settings.local.json').exists())
        self.assertEqual(Store(new_root).config['prostoi'], 'new.xlsx')

    def test_locator_cycle_is_reported_without_changing_settings(self):
        atomic_json(self.root / 'settings.local.json', {'redirect_root': str(self.root), 'prostoi': 'keep.xlsx'})
        before = file_hash(self.root / 'settings.local.json')
        with self.assertRaisesRegex(AppError, 'Цикл'):
            resolve_root(self.root)
        self.assertEqual(file_hash(self.root / 'settings.local.json'), before)

    def test_incomplete_all_status_checks_return_each_result_without_write(self):
        paths = fixtures(Path(self.temp.name) / 'files')
        before = file_hash(paths['target'])
        self.engine.save_settings({'prostoi': paths['prostoi'], 'last_date': paths['last_date']})
        self.engine.check('all', paths['last_date']); self.wait()
        self.assertTrue(self.engine.checks['prostoi']['ok'])
        self.assertFalse(self.engine.checks['drivers']['ok'])
        self.assertFalse(self.engine.checks['csv']['ok'])
        self.assertEqual(file_hash(paths['target']), before)
        self.assertEqual(self.engine.store.copies(), [])
