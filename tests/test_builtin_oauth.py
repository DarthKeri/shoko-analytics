"""A bundled Desktop app identity never contains user's OAuth tokens."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from shoko.google_auth import GoogleAuth, client_config
from shoko.storage import Store, AppError, digest
from test_google_auth import client_fixture, credentials, fake_protect
from test_packaging import package


class BuiltinOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = self.root / 'external-client.json'
        self.default = self.root / 'bundled-client.json'
        self.configuration = client_fixture(self.original)
        client_fixture(self.default)
        self.store = Store(self.root / 'workspace')
        self.auth = GoogleAuth(self.store)
        self.source = patch('shoko.google_auth.client_source_path', side_effect=lambda path='': Path(path) if path else self.default)
        self.source.start()
        self.protection = patch('shoko.google_auth.protect', side_effect=fake_protect)
        self.protection.start()

    def tearDown(self):
        self.source.stop(); self.protection.stop(); self.temp.cleanup()

    def test_default_configuration_works_without_user_file(self):
        self.original.unlink()
        self.assertEqual(client_config(''), self.configuration)

    def test_existing_login_survives_migration_to_same_bundled_client(self):
        self.auth.save({'credentials': json.loads(credentials().to_json()), 'email': 'fixture@example.test',
                        'client_fingerprint': digest(client_config(str(self.original)))})
        self.original.unlink()
        result = self.auth.status({'google_auth_mode': 'oauth', 'google_oauth_client': ''})
        self.assertTrue(result['connected'])
        self.assertTrue(result['built_in_client'])
        self.assertTrue(result['client_configured'])
        for secret in ('fixture-refresh-token', 'fixture-access-token', 'fixture-client-secret'):
            self.assertNotIn(secret, json.dumps(result))

    def test_missing_bundled_client_is_actionable_and_never_marked_ready(self):
        self.default.unlink()
        result = self.auth.status({'google_auth_mode': 'oauth', 'google_oauth_client': ''})
        self.assertFalse(result['connected'])
        self.assertFalse(result['built_in_client'])
        self.assertFalse(result['client_configured'])
        self.assertIn('не настроен для этой сборки', result['message'])

    def test_corrupted_bundled_client_is_not_advertised_as_ready(self):
        self.default.write_text('broken', encoding='utf-8')
        result = self.auth.status({'google_auth_mode': 'oauth', 'google_oauth_client': ''})
        self.assertFalse(result['client_configured'])
        self.assertFalse(result['built_in_client'])

    def test_changed_default_client_blocks_previous_token(self):
        self.auth.save({'credentials': json.loads(credentials().to_json()), 'email': 'fixture@example.test',
                        'client_fingerprint': digest(client_config(''))})
        config = self.configuration
        config['installed']['client_id'] = 'another.apps.googleusercontent.com'
        self.default.write_text(json.dumps(config), encoding='utf-8')
        self.assertFalse(self.auth.status({'google_auth_mode': 'oauth', 'google_oauth_client': ''})['connected'])

    def test_private_builder_copies_only_normalized_desktop_configuration(self):
        dirty = dict(self.configuration, refresh_token='must-not-bundle', private_key='must-not-bundle')
        dirty['installed']['access_token'] = 'must-not-bundle'
        self.original.write_text(json.dumps(dirty), encoding='utf-8')
        app = self.root / 'app'
        (app / 'assets').mkdir(parents=True)
        package.include_oauth_client(app, self.original)
        result = json.loads((app / 'assets/oauth-desktop.json').read_text(encoding='utf-8'))
        self.assertEqual(set(result), {'installed'})
        self.assertEqual(set(result['installed']), {'client_id', 'client_secret', 'auth_uri', 'token_uri'})
        self.assertNotIn('must-not-bundle', json.dumps(result))

    def test_private_builder_rejects_service_account_keys(self):
        self.original.write_text(json.dumps({'type': 'service_account', 'private_key': 'must-not-bundle'}), encoding='utf-8')
        app = self.root / 'app'
        (app / 'assets').mkdir(parents=True)
        with self.assertRaises(AppError):
            package.include_oauth_client(app, self.original)
        self.assertFalse((app / 'assets/oauth-desktop.json').exists())


if __name__ == '__main__':
    unittest.main()
