"""User-account Telegram access. Credentials and StringSession are DPAPI-protected."""
import asyncio
import base64
import json
import os
import threading
import time
from pathlib import Path
from .storage import AppError, atomic_json


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise AppError('Сохранение Telegram-доступа поддерживается только на Windows (DPAPI).')
    import win32crypt
    try:
        if decrypt:
            return win32crypt.CryptUnprotectData(data, None, None, None, 1)[1]
        return win32crypt.CryptProtectData(data, 'Shoko Analytics Telegram', None, None, None, 1)
    except Exception:
        raise AppError('Не удалось защитить Telegram-доступ средствами Windows.') from None



class Telegram:
    def __init__(self, store):
        self.store = store
        self.vault = store.root / 'sessions' / 'telegram.vault'
        self.loop = asyncio.SelectorEventLoop() if os.name == 'nt' else asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.client = None
        self.pending = None

    def call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=180)

    def save(self, data):
        atomic_json(self.vault, {'dpapi': base64.b64encode(protect(json.dumps(data).encode())).decode()})

    def load(self):
        if not self.vault.exists():
            raise AppError('Telegram не подключён. Подключите аккаунт или выберите CSV вручную.')
        try:
            return json.loads(protect(base64.b64decode(json.loads(self.vault.read_text())['dpapi']), decrypt=True))
        except AppError:
            raise
        except Exception:
            raise AppError('Не удалось открыть сохранённый Telegram-доступ. Подключите аккаунт заново.') from None

    async def connect(self):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        if self.client and self.client.is_connected():
            return self.client
        data = self.load()
        self.client = TelegramClient(StringSession(data['session']), int(data['api_id']), data['api_hash'], timeout=15, connection_retries=1)
        await asyncio.wait_for(self.client.connect(), 25)
        if not await asyncio.wait_for(self.client.is_user_authorized(), 25):
            raise AppError('Telegram-сессия истекла. Подключите аккаунт заново.')
        return self.client

    async def send_code(self, api_id, api_hash, phone):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        if self.client:
            await self.client.disconnect()
        if not str(api_id).isdigit() or len(api_hash) != 32 or not phone.startswith('+'):
            raise AppError('Проверьте API ID, API Hash и номер телефона в международном формате.')
        self.pending = {'api_id': int(api_id), 'api_hash': api_hash, 'phone': phone}
        self.client = TelegramClient(StringSession(), int(api_id), api_hash, timeout=15, connection_retries=1)
        await asyncio.wait_for(self.client.connect(), 25)
        result = await asyncio.wait_for(self.client.send_code_request(phone), 30)
        self.pending['phone_code_hash'] = result.phone_code_hash
        return {'password_required': False}

    async def sign_in(self, code='', password=''):
        from telethon.errors import SessionPasswordNeededError
        if not self.pending or not self.client:
            raise AppError('Сначала запросите код Telegram.')
        try:
            if password:
                await asyncio.wait_for(self.client.sign_in(password=password), 30)
            else:
                await asyncio.wait_for(self.client.sign_in(phone=self.pending['phone'], code=code, phone_code_hash=self.pending['phone_code_hash']), 30)
        except SessionPasswordNeededError:
            return {'password_required': True}
        data = {k: self.pending[k] for k in ('api_id', 'api_hash')}
        data['session'] = self.client.session.save()
        self.save(data)
        self.pending = None
        return {'connected': True}

    async def check(self, bot):
        client = await self.connect()
        entity = await asyncio.wait_for(client.get_entity(bot), 25)
        if not getattr(entity, 'bot', False):
            raise AppError('Указанный Telegram-пользователь не является ботом.')
        return {'message': 'Аккаунт подключён, бот доступен. Запрос не отправлялся.'}

    async def download(self, config, url, run_id, cancelled):
        client = await self.connect()
        entity = await asyncio.wait_for(client.get_entity(config['telegram_bot']), 25)
        if not getattr(entity, 'bot', False):
            raise AppError('Получатель Telegram не является ботом.')
        deadline = time.monotonic() + int(config.get('telegram_timeout', 120))
        async with client.conversation(entity, timeout=5, total_timeout=int(config.get('telegram_timeout', 120)), exclusive=True) as conv:
            sent = await conv.send_message(url)
            while time.monotonic() < deadline:
                if cancelled.is_set():
                    raise AppError('Подготовка отменена.')
                try:
                    response = await conv.get_response(timeout=min(5, max(0.1, deadline - time.monotonic())))
                except asyncio.TimeoutError:
                    continue
                if not response.file:
                    continue
                if not (response.file.name or '').lower().endswith('.csv'):
                    raise AppError('Бот прислал файл другого формата. Выберите CSV вручную.')
                # Never silently use a delayed export from a different request.
                if response.reply_to_msg_id != sent.id and url not in (response.raw_text or ''):
                    raise AppError('Бот прислал CSV без связи с отправленной ссылкой. Для MVP выберите этот файл вручную и подтвердите ссылку.')
                if (response.file.size or 0) > 30_000_000:
                    raise AppError('CSV бота превышает лимит 30 МБ.')
                folder = self.store.root / 'inputs' / run_id
                folder.mkdir(exist_ok=True)
                path = folder / 'garage.csv'
                await asyncio.wait_for(client.download_media(response, file=str(path)), 60)
                if cancelled.is_set():
                    raise AppError('Подготовка отменена.')
                atomic_json(folder / 'telegram-source.json', {'bot_id': entity.id, 'request_message': sent.id, 'response_message': response.id, 'url': url})
                return str(path)
        raise AppError('Бот не прислал CSV за отведённое время. Повторите или выберите файл вручную.')

    def close(self):
        if self.client and self.loop.is_running():
            try:
                self.call(self.client.disconnect())
            except Exception:
                pass
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if not self.loop.is_running() and not self.loop.is_closed():
            self.loop.close()
