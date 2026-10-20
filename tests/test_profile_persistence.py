import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from shoko.google_auth import GoogleAuth,client_config
from shoko.storage import Store,digest
from shoko.drive_backup import backup_name
from test_google_auth import client_fixture,credentials,fake_protect

class ProfilePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
        self.path=Path(self.temp.name)/'oauth.json';client_fixture(self.path)
        self.auth=GoogleAuth(self.store);self.protection=patch('shoko.google_auth.protect',side_effect=fake_protect);self.protection.start()
        self.auth.save({'credentials':json.loads(credentials().to_json()),'email':'fixture@example.test','client_fingerprint':digest(client_config(str(self.path)))})
    def tearDown(self):self.protection.stop();self.temp.cleanup()
    def test_refresh_does_not_overwrite_a_later_profile_photo(self):
        config={'google_oauth_client':str(self.path)};session=self.auth.session(config)
        latest=self.auth.load(str(self.path));self.auth.save(dict(latest,name='Fixture',avatar='data:image/png;base64,cG5n',profile_loaded=True))
        def refresh(*args,**kwargs):session.credentials.token='refreshed';return SimpleNamespace(status_code=200)
        with patch('google.auth.transport.requests.AuthorizedSession.request',side_effect=refresh):session.request('GET','https://www.googleapis.com/drive/v3/about')
        saved=self.auth.load(str(self.path));self.assertEqual(saved['name'],'Fixture');self.assertTrue(saved['profile_loaded']);self.assertEqual(saved['credentials']['token'],'refreshed');session.close()
    def test_refresh_of_old_session_does_not_recreate_a_disconnected_login(self):
        session=self.auth.session({'google_oauth_client':str(self.path)});self.auth.disconnect()
        def refresh(*args,**kwargs):session.credentials.token='refreshed';return SimpleNamespace(status_code=200)
        with patch('google.auth.transport.requests.AuthorizedSession.request',side_effect=refresh):session.request('GET','https://www.googleapis.com/drive/v3/about')
        self.assertFalse(self.auth.vault.exists());session.close()
    def test_copy_prefix_uses_moscow_date_even_across_utc_midnight(self):
        moment=datetime(2026,10,15,22,3,4,tzinfo=timezone.utc)
        self.assertEqual(backup_name('Рабочая таблица',moment),'[Shoko_2026-10-16_01-03-04] Рабочая таблица')

if __name__=='__main__':unittest.main()
