import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from requests.exceptions import SSLError, ReadTimeout, ConnectTimeout, ConnectionError
from google.auth.exceptions import TransportError, RefreshError
from urllib3.exceptions import ProtocolError
from shoko.adapters import GoogleTarget
from shoko.storage import AppError
from shoko.calculation import TARGET


class GoogleTransportTests(unittest.TestCase):
    def adapter(self, error):
        adapter = object.__new__(GoogleTarget)
        adapter.base = 'https://sheets.googleapis.com/v4/spreadsheets/test-copy'
        adapter.session = SimpleNamespace(request=Mock(side_effect=error))
        return adapter

    def test_tls_failure_is_distinct_from_timeout_and_sanitized(self):
        secret = 'fixture-private-jwt-never-display'
        for original in [SSLError(secret), ReadTimeout(secret), ConnectTimeout(secret), ConnectionError(secret), RefreshError(secret)]:
            wrapped = TransportError('wrapped')
            wrapped.__cause__ = original
            adapter = self.adapter(wrapped)
            with self.assertRaises(AppError) as caught:
                adapter.request('GET')
            text = str(caught.exception)
            self.assertNotIn(secret, text)
            expected = 'HTTPS-сертификат' if isinstance(original, SSLError) else 'не ответил вовремя' if isinstance(original, (ReadTimeout, ConnectTimeout)) else 'установить соединение' if isinstance(original, ConnectionError) else 'авторизацию'
            self.assertIn(expected, text)
            self.assertEqual(adapter.session.request.call_args.kwargs['timeout'], (10, 45))
            self.assertNotIn('verify', adapter.session.request.call_args.kwargs)

    def test_status_only_reads_metadata_without_cell_download(self):
        adapter = self.adapter(None)
        adapter.session.request.side_effect = None
        adapter.session.request.return_value = SimpleNamespace(status_code=200, json=lambda: {'sheets': [{'properties': {'title': TARGET}}]})
        adapter.check()
        adapter.session.request.assert_called_once()
        call = adapter.session.request.call_args
        self.assertEqual(call.args[0], 'GET')
        self.assertEqual(call.kwargs['params'], {'includeGridData': 'false', 'fields': 'spreadsheetId,sheets(properties,merges)'})

    def test_status_rejects_missing_required_sheet(self):
        adapter = self.adapter(None)
        adapter.session.request.side_effect = None
        adapter.session.request.return_value = SimpleNamespace(status_code=200, json=lambda: {'sheets': [{'properties': {'title': 'Other'}}]})
        with self.assertRaisesRegex(AppError, TARGET):
            adapter.check()

    def test_nested_socket_upload_timeout_is_sanitized_and_not_retried(self):
        secret = 'fixture-jwt-and-private-payload'
        error = ConnectionError(ProtocolError(secret, TimeoutError(secret)))
        adapter = self.adapter(error)
        with self.assertRaises(AppError) as caught:
            adapter.request('POST', ':batchUpdate', data=b'fixture', timeout=(45, 180))
        text = str(caught.exception)
        self.assertIn('не подтвердил результат записи', text)
        self.assertIn('Данные могли быть записаны', text)
        self.assertIn('45 секунд', text)
        self.assertIn('180 секунд', text)
        self.assertIn('автоматический повтор не выполняется', text)
        self.assertIn('ConnectionError / ProtocolError / TimeoutError', text)
        self.assertNotIn(secret, text)
        adapter.session.request.assert_called_once()

    def test_connection_reset_keeps_numeric_code_without_exception_text(self):
        adapter = self.adapter(ConnectionError(ConnectionResetError(10054, 'fixture-secret-url')))
        with self.assertRaises(AppError) as caught:
            adapter.request('POST', ':batchUpdate', timeout=(45, 180))
        self.assertIn('errno=10054', str(caught.exception))
        self.assertNotIn('fixture-secret-url', str(caught.exception))
        adapter.session.request.assert_called_once()

    def test_nested_builtin_timeout_during_get_is_recognized(self):
        adapter = self.adapter(ConnectionError(ProtocolError('secret', TimeoutError('secret'))))
        with self.assertRaises(AppError) as caught:
            adapter.request('GET')
        self.assertIn('не ответил вовремя', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows system trust integration')
    def test_windows_entrypoint_keeps_tls_verification(self):
        # Isolate global SSL injection from tests that recreate native-library threads.
        code = '''
import main
import ssl
import requests
import truststore
context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
assert isinstance(context, truststore.SSLContext)
assert context.verify_mode == ssl.CERT_REQUIRED
assert context.check_hostname
assert requests.Session().verify is True
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
