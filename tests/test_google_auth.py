import base64
import hashlib
import http.client
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlparse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from shoko.google_auth import GoogleAuth, OAuthCallback, SCOPES, TOKEN_URI, client_config, SavedSession
from shoko.google_errors import http_error
from shoko.storage import Store, AppError, digest, atomic_json
from shoko.engine import Engine


def client_fixture(path):
    value = {'installed': {'client_id': 'fixture.apps.googleusercontent.com', 'client_secret': 'fixture-client-secret',
                          'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': TOKEN_URI}}
    Path(path).write_text(json.dumps(value), encoding='utf-8')
    return value


def credentials(scopes=None):
    return Credentials('fixture-access-token', refresh_token='fixture-refresh-token', token_uri=TOKEN_URI,
                       client_id='fixture.apps.googleusercontent.com', client_secret='fixture-client-secret',
                       scopes=SCOPES, granted_scopes=SCOPES if scopes is None else scopes)


def fake_protect(value, decrypt=False):
    # Tests never store real credentials; production DPAPI is checked in a subprocess.
    return bytes(byte ^ 0x5A for byte in value)


class FakeFlow:
    def __init__(self, state, granted=None):
        self.state = state
        self.credentials = credentials(granted)
        self.fetch_token = Mock()

    def authorization_url(self, **kwargs):
        self.parameters = kwargs
        return 'https://accounts.google.com/o/oauth2/auth?' + urlencode({'state': self.state, 'redirect_uri': self.redirect_uri}), self.state


class GoogleAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.path = Path(self.temp.name) / 'oauth-client.json'
        self.configuration = client_fixture(self.path)
        self.auth = GoogleAuth(self.store)
        self.patch = patch('shoko.google_auth.protect', side_effect=fake_protect)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def save(self):
        self.auth.save({'credentials': json.loads(credentials().to_json()), 'email': 'fixture@example.test',
                        'client_fingerprint': digest(client_config(str(self.path)))})

    def test_client_rejects_service_web_invalid_json_and_external_token_endpoints(self):
        for value in [{'type': 'service_account'}, {'web': self.configuration['installed']}, [],
                      {'installed': dict(self.configuration['installed'], token_uri='https://evil.example/token')}]:
            self.path.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(AppError):
                client_config(str(self.path))
        self.path.write_text('broken', encoding='utf-8')
        with self.assertRaises(AppError):
            client_config(str(self.path))

    def test_real_google_library_uses_s256_pkce_and_offline_consent(self):
        flow = InstalledAppFlow.from_client_config(self.configuration, scopes=SCOPES, state='test-state', autogenerate_code_verifier=True)
        flow.redirect_uri = 'http://127.0.0.1:12345/'
        url, state = flow.authorization_url(access_type='offline', prompt='consent select_account')
        query = parse_qs(urlparse(url).query)
        expected = base64.urlsafe_b64encode(hashlib.sha256(flow.code_verifier.encode()).digest()).decode().rstrip('=')
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['code_challenge'], [expected])
        self.assertEqual(query['access_type'], ['offline'])
        self.assertEqual(state, 'test-state')

    def test_saved_session_survives_restart_without_public_token_or_settings_copy(self):
        self.save()
        status = GoogleAuth(self.store).status({'google_auth_mode': 'oauth', 'google_oauth_client': str(self.path)})
        self.assertTrue(status['connected'])
        self.assertEqual(status['email'], 'fixture@example.test')
        for secret in ('fixture-access-token', 'fixture-refresh-token', 'fixture-client-secret'):
            self.assertNotIn(secret, self.auth.vault.read_text())
            self.assertNotIn(secret, json.dumps(status))
        self.assertEqual(self.store.copies(), [])

    def test_ui_status_polls_do_not_repeatedly_decrypt_credentials(self):
        self.save()
        config = {'google_auth_mode': 'oauth', 'google_oauth_client': str(self.path)}
        with patch('shoko.google_auth.protect', side_effect=fake_protect) as protect:
            for _ in range(120):
                status = self.auth.status(config)
                self.assertTrue(status['connected'])
                self.assertEqual(status['email'], 'fixture@example.test')
            self.assertEqual(protect.call_count, 1)
            self.assertTrue(protect.call_args.kwargs['decrypt'])
            status['email'] = 'tampered-ui@example.test'
            self.assertEqual(self.auth.status(config)['email'], 'fixture@example.test')
            self.assertNotIn('fixture-access-token', json.dumps(self.auth._status_value))

    def test_cached_status_is_invalidated_by_save_disconnect_and_vault_change(self):
        self.save()
        config = {'google_auth_mode': 'oauth', 'google_oauth_client': str(self.path)}
        self.assertTrue(self.auth.status(config)['connected'])
        saved = self.auth.load(str(self.path))
        self.auth.save(dict(saved, email='second@example.test'))
        self.assertEqual(self.auth.status(config)['email'], 'second@example.test')
        self.auth.vault.write_text('{"dpapi":"invalid"}', encoding='utf-8')
        self.assertFalse(self.auth.status(config)['connected'])
        self.auth.save(saved)
        self.assertTrue(self.auth.status(config)['connected'])
        self.auth.disconnect()
        self.assertFalse(self.auth.status(config)['connected'])

    def test_actual_session_revalidates_vault_instead_of_using_public_cache(self):
        self.save()
        config = {'google_auth_mode': 'oauth', 'google_oauth_client': str(self.path)}
        self.assertTrue(self.auth.status(config)['connected'])
        with patch('shoko.google_auth.protect', side_effect=fake_protect) as protect:
            session = self.auth.session(config)
            session.close()
            self.assertEqual(protect.call_count, 1)
        self.auth.vault.write_text('{"dpapi":"invalid"}', encoding='utf-8')
        with self.assertRaises(AppError):
            self.auth.session(config)

    def test_cached_status_is_invalidated_when_oauth_client_changes(self):
        self.save()
        config = {'google_auth_mode': 'oauth', 'google_oauth_client': str(self.path)}
        self.assertTrue(self.auth.status(config)['connected'])
        self.configuration['installed']['client_secret'] = 'different-client-secret'
        self.path.write_text(json.dumps(self.configuration), encoding='utf-8')
        self.assertFalse(self.auth.status(config)['connected'])

    def test_concurrent_status_polls_only_decrypt_once(self):
        self.save()
        config = {'google_auth_mode': 'oauth', 'google_oauth_client': str(self.path)}
        results = []
        with patch('shoko.google_auth.protect', side_effect=fake_protect) as protect:
            threads = [threading.Thread(target=lambda: results.append(self.auth.status(config))) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
            self.assertEqual(len(results), 20)
            self.assertTrue(all(result['connected'] for result in results))
            self.assertEqual(protect.call_count, 1)

    def test_changed_oauth_client_cannot_reuse_old_account(self):
        self.save()
        self.configuration['installed']['client_secret'] = 'different'
        self.path.write_text(json.dumps(self.configuration), encoding='utf-8')
        with self.assertRaisesRegex(AppError, 'другой OAuth'):
            self.auth.load(str(self.path))

    def test_disconnect_removes_local_login(self):
        self.save()
        self.auth.disconnect()
        self.assertFalse(self.auth.vault.exists())
        with self.assertRaisesRegex(AppError, 'не подключён'):
            self.auth.load(str(self.path))

    def test_damaged_vault_is_not_exposed(self):
        self.auth.vault.write_text('{"dpapi":"not base64 -- fixture-secret"}', encoding='utf-8')
        with self.assertRaises(AppError) as caught:
            self.auth.load(str(self.path))
        self.assertNotIn('fixture-secret', str(caught.exception))

    def test_refresh_token_changes_are_saved_to_the_same_vault(self):
        self.save()
        session = self.auth.session({'google_oauth_client': str(self.path)})
        def refreshed(*args, **kwargs):
            session.credentials.token = 'fixture-refreshed-token'
            return SimpleNamespace(status_code=200)
        with patch('google.auth.transport.requests.AuthorizedSession.request', side_effect=refreshed):
            session.request('GET', 'https://www.googleapis.com/drive/v3/about')
        self.assertEqual(self.auth.load(str(self.path))['credentials']['token'], 'fixture-refreshed-token')
        self.assertEqual(len(list((self.store.root / 'sessions').glob('google*'))), 1)
        session.close()

    def connect(self, *, response_error=False, granted=None, token_error=False, timeout=2):
        flow_holder = {}
        callback_threads = []
        def factory(*args, **kwargs):
            flow = FakeFlow(kwargs['state'], granted)
            if token_error:
                flow.fetch_token.side_effect = ValueError('fixture-private-token-body')
            flow_holder['flow'] = flow
            return flow
        def browser(url, **kwargs):
            query = parse_qs(urlparse(url).query)
            redirect = urlparse(query['redirect_uri'][0])
            def callback():
                conn = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=3)
                values = {'state': query['state'][0], 'error': 'access_denied'} if response_error else {'state': query['state'][0], 'code': 'fixture-private-code'}
                conn.request('GET', redirect.path + '?' + urlencode(values))
                conn.getresponse().read()
                conn.close()
            thread = threading.Thread(target=callback)
            callback_threads.append(thread)
            thread.start()
            return True
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.request.return_value = SimpleNamespace(status_code=200, json=lambda: {'user': {'emailAddress': 'fixture@example.test'}})
        try:
            with patch('shoko.google_auth.InstalledAppFlow.from_client_config', side_effect=factory), patch('shoko.google_auth.webbrowser.open', side_effect=browser), patch('shoko.google_auth.AuthorizedSession', return_value=session):
                result = self.auth.connect(str(self.path), threading.Event(), timeout=timeout)
                self.assertTrue(result['connected'])
                self.assertEqual(flow_holder['flow'].parameters['access_type'], 'offline')
                flow_holder['flow'].fetch_token.assert_called_once_with(code='fixture-private-code', timeout=(10, 45))
                return result
        finally:
            for thread in callback_threads:
                thread.join(3)

    def test_connect_receives_callback_and_stores_verified_account(self):
        self.connect()
        self.assertEqual(self.auth.load(str(self.path))['email'], 'fixture@example.test')

    def test_denied_consent_never_saves_login(self):
        with self.assertRaisesRegex(AppError, 'отменён'):
            self.connect(response_error=True)
        self.assertFalse(self.auth.vault.exists())

    def test_partial_permissions_never_save_login(self):
        with self.assertRaisesRegex(AppError, 'полный доступ'):
            self.connect(granted=[SCOPES[0]])
        self.assertFalse(self.auth.vault.exists())

    def test_space_separated_granted_scopes_are_accepted(self):
        self.connect(granted=' '.join(SCOPES))
        self.assertEqual(self.auth.load(str(self.path))['credentials']['scopes'], SCOPES)

    def test_token_exchange_failure_hides_code_and_token(self):
        with self.assertRaises(AppError) as caught:
            self.connect(token_error=True)
        self.assertNotIn('fixture-private', str(caught.exception))
        self.assertFalse(self.auth.vault.exists())

    def test_cancelled_or_timed_out_login_is_not_saved(self):
        cancelled = threading.Event(); cancelled.set()
        with patch('shoko.google_auth.webbrowser.open') as browser:
            with self.assertRaisesRegex(AppError, 'отменён'):
                self.auth.connect(str(self.path), cancelled)
            browser.assert_not_called()
        with patch('shoko.google_auth.webbrowser.open', return_value=True):
            with self.assertRaisesRegex(AppError, 'истекло'):
                self.auth.connect(str(self.path), threading.Event(), timeout=0.01)
        self.assertFalse(self.auth.vault.exists())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI')
    def test_real_dpapi_roundtrip_in_isolated_process(self):
        code = "from shoko.google_auth import protect; value=b'fixture-only'; encrypted=protect(value); assert encrypted!=value; assert protect(encrypted,decrypt=True)==value"
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


class CallbackSecurityTests(unittest.TestCase):
    def test_callback_rejects_wrong_state_host_duplicates_and_path(self):
        with OAuthCallback('correct-state') as server:
            with socket.socket() as competing:
                with self.assertRaises(OSError):
                    competing.bind(('127.0.0.1', server.server_port))
            finished = threading.Event()
            def serve():
                while not finished.is_set() and server.result is None:
                    server.handle_request()
            thread = threading.Thread(target=serve)
            thread.start()
            def get(path, host=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                connection.request('GET', path, headers={'Host': host} if host else {})
                response = connection.getresponse()
                body = response.read().decode('utf-8')
                result = response.status
                self.assertNotIn('fixture-private-code', body)
                connection.close()
                return result
            try:
                self.assertEqual(get('/wrong?state=correct-state&code=fixture-private-code'), 404)
                self.assertEqual(get('/?state=correct-state&code=fixture-private-code', 'evil.example'), 404)
                self.assertEqual(get('/?state=bad&code=fixture-private-code'), 400)
                self.assertEqual(get('/?state=correct-state&state=bad&code=fixture-private-code'), 400)
                self.assertIsNone(server.result)
                self.assertEqual(get('/?state=correct-state&code=fixture-private-code'), 200)
                self.assertEqual(server.result, {'code': 'fixture-private-code'})
            finally:
                finished.set()
                thread.join(3)


class ErrorAndEngineTests(unittest.TestCase):
    def test_api_reasons_are_distinct_and_raw_payloads_are_not_shown(self):
        for reason, expected in [('storageQuotaExceeded', 'квоты'), ('SERVICE_DISABLED', 'не включён'), ('insufficientFilePermissions', 'нет прав'), ('ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'заново')]:
            response = SimpleNamespace(status_code=403, json=lambda r=reason: {'error': {'errors': [{'reason': r, 'message': 'fixture-secret'}]}})
            message = http_error(response)
            self.assertIn(expected, message)
            self.assertIn(reason, message)
            self.assertNotIn('fixture-secret', message)

    def test_account_disconnect_invalidates_reviewed_plan_and_status(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder)
            try:
                engine.plan = {'id': 'reviewed-plan', 'checksum': 'checksum'}
                engine.state.update(phase='preview', plan_id='reviewed-plan')
                engine.checks = {'target': {'ok': True}, 'prostoi': {'ok': True}, 'backup': {'ok': True}}
                engine.google_auth_action('disconnect')
                self.assertIsNone(engine.plan)
                self.assertNotIn('target', engine.checks)
                self.assertIn('prostoi', engine.checks)
                with self.assertRaises(AppError):
                    engine.write('reviewed-plan', 'checksum')
            finally:
                engine.close()

    def test_account_replacement_blocks_old_approved_google_plan_before_any_request(self):
        with tempfile.TemporaryDirectory() as folder, patch('shoko.google_auth.protect', side_effect=fake_protect):
            engine = Engine(folder)
            try:
                path = Path(folder) / 'client.json'; client_fixture(path)
                source = 'fixture_google_spreadsheet_12345'
                engine.save_settings({'target_mode': 'google', 'target': source, 'test_copy_confirmed': True, 'google_oauth_client': str(path)})
                saved = {'credentials': json.loads(credentials().to_json()), 'email': 'first@example.test', 'client_fingerprint': digest(client_config(str(path)))}
                engine.google.save(saved)
                plan = {'id': 'reviewed-plan', 'config_digest': digest(engine.config()), 'google_identity': engine.google.identity(engine.config())}
                plan['checksum'] = digest(plan)
                engine.plan = plan
                engine.state['phase'] = 'preview'
                engine.google.save(dict(saved, email='second@example.test'))
                with patch('shoko.engine.target') as adapter:
                    engine.write(plan['id'], plan['checksum'])
                    if engine.worker: engine.worker.join(5)
                    adapter.assert_not_called()
                self.assertIn('аккаунт изменился', engine.state['error'])
            finally:
                engine.close()

    def test_cancel_login_returns_to_idle_without_a_secret_or_write(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder)
            try:
                path = Path(folder) / 'client.json'; client_fixture(path)
                engine.save_settings({'google_oauth_client': str(path)})
                def waiting(path, cancelled, progress):
                    cancelled.wait(3)
                    raise AppError('Вход Google отменён.')
                with patch.object(engine.google, 'connect', side_effect=waiting):
                    engine.google_auth_action('connect')
                    engine.cancel_google()
                    if engine.worker: engine.worker.join(5)
                self.assertEqual(engine.state['phase'], 'idle')
                self.assertEqual(engine.state['error'], '')
                self.assertFalse(engine.google.vault.exists())
                self.assertEqual(engine.store.copies(), [])
            finally:
                engine.close()
