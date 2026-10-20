import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from shoko.engine import Engine
from shoko.server import Server
from shoko.storage import AppError,Store,atomic_json,file_hash
from test_mvp import fixtures

class ManualCsvOnlyTests(unittest.TestCase):
    def test_legacy_settings_migrate_in_place_preserving_google_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'sessions').mkdir()
            vault=root/'sessions'/'google.vault';vault.write_bytes(b'fixture-only')
            config={'initialized':True,'prostoi':'keep.xlsx','csv':'keep.csv','target':'keep-target','google_backup_folder':'keep-folder','telegram_mode':'web','telegram_bot':'@old','telegram_api_hash':'old','last_url':'https://garage.yandex.ru/moskva'}
            atomic_json(root/'settings.local.json',config)
            before=file_hash(vault);store=Store(root)
            for key in ('prostoi','csv','target','google_backup_folder'):self.assertEqual(store.config[key],config[key])
            self.assertFalse(any(k.startswith('telegram_') or k=='last_url' for k in store.config))
            self.assertEqual(file_hash(vault),before)
            self.assertEqual(len(list(root.glob('settings*'))),1)
            self.assertEqual(Store(root).config,store.config)
            self.assertEqual(store.copies(),[])

    def test_legacy_automatic_mode_cannot_bypass_missing_manual_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=Engine(Path(folder)/'data')
            try:
                paths=fixtures(Path(folder)/'files');engine.save_settings(dict(paths,csv='',telegram_mode='web',telegram_bot='@old'))
                with patch.object(engine,'launch') as launch:
                    with self.assertRaisesRegex(AppError,'CSV Гаража'):engine.prepare('',paths['last_date'])
                    launch.assert_not_called()
                self.assertIsNone(engine.plan);self.assertEqual(engine.store.copies(),[])
            finally:engine.close()

    def test_removed_connection_actions_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=Engine(folder);server=Server(engine,Path(folder))
            try:
                for action in ('telegram','telegram-web'):
                    with self.assertRaisesRegex(AppError,'Неизвестное действие'):server.action({'action':action,'step':'connect'})
                self.assertNotIn('telegram_web',engine.public_state())
                self.assertFalse(hasattr(engine,'telegram'));self.assertFalse(hasattr(engine,'telegram_web'))
            finally:server.close();engine.close()

if __name__=='__main__':unittest.main()
