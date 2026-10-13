import asyncio
import os
import tempfile
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from shoko.storage import AppError, Store
from shoko.telegram import Telegram, protect


class FakeConversation:
    def __init__(self, reply): self.reply = reply
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def send_message(self, url): return SimpleNamespace(id=100)
    async def get_response(self, **kwargs): return self.reply


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.telegram=Telegram(Store(self.temp.name))

    def tearDown(self): self.telegram.close();self.temp.cleanup()

    @unittest.skipUnless(os.name=='nt','DPAPI is Windows-only')
    def test_session_is_encrypted_and_roundtrips(self):
        # Windows integration test uses a complete, isolated application lifecycle.
        script = """
import tempfile
from types import SimpleNamespace
from shoko.storage import Store
from shoko.telegram import Telegram
with tempfile.TemporaryDirectory() as folder:
    telegram = Telegram(Store(folder))
    try:
        values = {'api_id':123, 'api_hash':'fixture-api-hash', 'session':'fixture-session'}
        telegram.save(values)
        assert 'fixture-session' not in telegram.vault.read_text()
        assert telegram.load() == values
        async def entity(bot): return SimpleNamespace(bot=True)
        async def connect(): return SimpleNamespace(get_entity=entity)
        telegram.connect = connect
        telegram.call(telegram.check('@fixture'))
    finally:
        telegram.close()
print('DPAPI_AND_CHECK_OK')
"""
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DPAPI_AND_CHECK_OK', result.stdout)

    def client(self, response):
        entity=SimpleNamespace(bot=True,id=42)
        async def download(response,file):
            Path(file).write_text('a;b\n1;2',encoding='utf-8')
        return SimpleNamespace(get_entity=AsyncMock(return_value=entity),conversation=lambda *a,**kw:FakeConversation(response),download_media=AsyncMock(side_effect=download))

    def response(self, reply=100, name='garage.csv'):
        return SimpleNamespace(file=SimpleNamespace(name=name,size=100),reply_to_msg_id=reply,raw_text='',id=200)

    def test_correlated_csv_is_saved_for_exact_request(self):
        client=self.client(self.response())
        with patch.object(self.telegram,'connect',AsyncMock(return_value=client)):
            path=self.telegram.call(self.telegram.download({'telegram_bot':'@fixture','telegram_timeout':15},'https://garage.example.test','run1',threading.Event()))
        self.assertTrue(Path(path).is_file())
        self.assertIn('https://garage.example.test',(Path(path).parent/'telegram-source.json').read_text())

    def test_unrelated_delayed_csv_is_rejected(self):
        client=self.client(self.response(reply=99))
        with patch.object(self.telegram,'connect',AsyncMock(return_value=client)):
            with self.assertRaises(AppError):self.telegram.call(self.telegram.download({'telegram_bot':'@fixture','telegram_timeout':15},'https://garage.example.test','run2',threading.Event()))
        client.download_media.assert_not_called()

    def test_wrong_file_type_is_rejected(self):
        client=self.client(self.response(name='export.xlsx'))
        with patch.object(self.telegram,'connect',AsyncMock(return_value=client)):
            with self.assertRaises(AppError):self.telegram.call(self.telegram.download({'telegram_bot':'@fixture','telegram_timeout':15},'https://garage.example.test','run3',threading.Event()))
        client.download_media.assert_not_called()

    def test_status_check_never_sends_a_message(self):
        client=self.client(self.response())
        with patch.object(self.telegram,'connect',AsyncMock(return_value=client)):
            self.telegram.call(self.telegram.check('@fixture'))
        client.download_media.assert_not_called()
