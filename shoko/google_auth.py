"""Desktop Google OAuth: external browser, PKCE, loopback state and DPAPI vault."""
import base64
import hmac
import json
import os
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from oauthlib.oauth2 import OAuth2Error
from .storage import AppError, atomic_json, digest
from .google_errors import http_error

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
TOKEN_URI = 'https://oauth2.googleapis.com/token'
AUTH_URIS = {'https://accounts.google.com/o/oauth2/auth', 'https://accounts.google.com/o/oauth2/v2/auth'}


def client_config(path):
    try:
        file = Path(path).expanduser()
        if not path or not file.is_file() or file.suffix.lower() != '.json' or file.stat().st_size > 65_536:
            raise ValueError()
        data = json.loads(file.read_text(encoding='utf-8-sig'))
    except (ValueError, TypeError, OSError):
        raise AppError('Выберите JSON-файл OAuth-клиента Google типа Desktop app (Приложение для компьютера).') from None
    if not isinstance(data, dict):
        raise AppError('OAuth-файл должен содержать объект JSON клиента Desktop app.')
    if data.get('type') == 'service_account':
        raise AppError('Выбран ключ сервисного аккаунта. Для входа через браузер нужен другой JSON: OAuth client ID → Desktop app.')
    installed = data.get('installed')
    if not isinstance(installed, dict) or not isinstance(installed.get('client_id'), str) or not installed['client_id'].endswith('.apps.googleusercontent.com') or not isinstance(installed.get('client_secret'), str) or not installed['client_secret']:
        raise AppError('Файл должен содержать OAuth-клиент типа Desktop app. Ключ Web application не подходит.')
    if installed.get('auth_uri') not in AUTH_URIS or installed.get('token_uri') != TOKEN_URI:
        raise AppError('OAuth-файл содержит неподдерживаемые адреса авторизации. Скачайте его заново из Google Cloud.')
    # Keep only the endpoints and identifiers actually used by the desktop flow.
    return {'installed': {key: installed[key] for key in ('client_id', 'client_secret', 'auth_uri', 'token_uri')}}


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise AppError('Сохранение входа Google поддерживается на Windows (DPAPI).')
    import win32crypt
    try:
        return win32crypt.CryptUnprotectData(data, None, None, None, 1)[1] if decrypt else win32crypt.CryptProtectData(data, 'Shoko Analytics Google', None, None, None, 1)
    except Exception:
        raise AppError('Не удалось открыть или защитить вход Google средствами Windows. Войдите в аккаунт заново.') from None


class OAuthCallback(HTTPServer):
    """Accept one state-checked callback, never log its query or authorization code."""
    allow_reuse_address = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, state):
        self.state, self.result = state, None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, message):
                data = ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                        '<title>Shoko Analytics</title><body style="background:#0d0d0d;color:#eee;font:16px Segoe UI,sans-serif;padding:40px">'
                        '<h2>Shoko Analytics</h2><p>' + message + '</p></body></html>').encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_GET(self):
                parsed = urlparse(self.path)
                if self.headers.get('Host') != f'127.0.0.1:{owner.server_port}' or parsed.path != '/' or len(self.path) > 16_384:
                    return self.reply(404, 'Адрес не найден.')
                query = parse_qs(parsed.query, keep_blank_values=True)
                states = query.get('state', [])
                if len(states) != 1 or not hmac.compare_digest(states[0].encode(), owner.state.encode()):
                    return self.reply(400, 'Ответ не относится к текущему входу. Вернитесь в приложение.')
                code, error = query.get('code', []), query.get('error', [])
                if owner.result is not None or (len(code) != 1 and len(error) != 1) or (code and error):
                    return self.reply(400, 'Некорректный ответ. Вернитесь в приложение.')
                if (code and not code[0]) or (error and not error[0]):
                    return self.reply(400, 'Пустой ответ. Повторите вход из приложения.')
                owner.result = {'code': code[0]} if code else {'error': error[0]}
                self.reply(200, 'Ответ Google получен. Вернитесь в приложение — оно проверит подключение. Эту вкладку можно закрыть.')
        super().__init__(('127.0.0.1', 0), Handler)
        self.timeout = 0.25

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


class SavedSession(AuthorizedSession):
    def __init__(self, credentials, owner, saved):
        super().__init__(credentials, refresh_timeout=45)
        self.owner, self.saved = owner, saved

    def request(self, *args, **kwargs):
        previous = self.credentials.to_json()
        try:
            return super().request(*args, **kwargs)
        finally:
            if self.credentials.to_json() != previous:
                self.saved['credentials'] = json.loads(self.credentials.to_json())
                self.owner.save(self.saved)


class GoogleAuth:
    def __init__(self, store):
        self.store = store
        self.vault = store.root / 'sessions' / 'google.vault'
        self.lock = threading.RLock()

    def save(self, value):
        with self.lock:
            encrypted = protect(json.dumps(value, ensure_ascii=False).encode('utf-8'))
            atomic_json(self.vault, {'format': 1, 'dpapi': base64.b64encode(encrypted).decode('ascii')})

    def load(self, path):
        expected = digest(client_config(path))
        if not self.vault.exists():
            raise AppError('Google-аккаунт не подключён. В настройках Google нажмите «Войти через Google».')
        try:
            with self.lock:
                envelope = json.loads(self.vault.read_text(encoding='utf-8'))
                data = json.loads(protect(base64.b64decode(envelope['dpapi'], validate=True), decrypt=True))
            if data.get('client_fingerprint') != expected:
                raise AppError('Выбран другой OAuth-файл Google. Войдите в аккаунт заново.')
            info = data['credentials']
            if not info.get('refresh_token') or info.get('token_uri') != TOKEN_URI or not set(SCOPES).issubset(info.get('scopes', [])):
                raise AppError('Сохранённый вход Google неполон. Войдите заново и разрешите Drive и таблицы.')
            return data
        except AppError:
            raise
        except Exception:
            raise AppError('Сохранённый вход Google повреждён или недоступен этому пользователю Windows. Войдите заново.') from None

    def status(self, config):
        if config.get('google_auth_mode', 'service_account') != 'oauth':
            return {'connected': False, 'email': '', 'message': 'Выбран сервисный аккаунт'}
        try:
            saved = self.load(config.get('google_oauth_client', ''))
            return {'connected': True, 'email': saved['email'], 'message': 'Вход сохранён'}
        except AppError as exc:
            return {'connected': False, 'email': '', 'message': str(exc)}

    def session(self, config):
        saved = self.load(config.get('google_oauth_client', ''))
        try:
            credentials = Credentials.from_authorized_user_info(saved['credentials'], scopes=SCOPES)
        except Exception:
            raise AppError('Не удалось прочитать сохранённый вход Google. Войдите заново.') from None
        return SavedSession(credentials, self, saved)

    def identity(self, config):
        saved = self.load(config.get('google_oauth_client', ''))
        return digest({'email': saved['email'], 'client': saved['client_fingerprint']})

    def disconnect(self):
        with self.lock:
            self.vault.unlink(missing_ok=True)

    def connect(self, path, cancelled, progress=lambda text: None, timeout=180):
        if cancelled.is_set():
            raise AppError('Вход Google отменён.')
        configuration = client_config(path)
        fingerprint = digest(configuration)
        state = secrets.token_urlsafe(32)
        flow = InstalledAppFlow.from_client_config(configuration, scopes=SCOPES, state=state, autogenerate_code_verifier=True)
        with OAuthCallback(state) as callback:
            flow.redirect_uri = f'http://127.0.0.1:{callback.server_port}/'
            url, returned_state = flow.authorization_url(access_type='offline', prompt='consent select_account')
            if returned_state != state:
                raise AppError('Не удалось подготовить безопасный вход Google. Повторите попытку.')
            progress('Завершите вход и разрешите доступ в открывшемся браузере Google')
            if cancelled.is_set():
                raise AppError('Вход Google отменён.')
            if not webbrowser.open(url, new=1):
                raise AppError('Не удалось открыть браузер для входа Google. Проверьте браузер по умолчанию и повторите.')
            deadline = time.monotonic() + timeout
            while callback.result is None:
                if cancelled.is_set():
                    raise AppError('Вход Google отменён.')
                if time.monotonic() >= deadline:
                    raise AppError('Время входа Google истекло (3 минуты). Нажмите «Войти через Google» ещё раз.')
                callback.handle_request()
            if cancelled.is_set():
                raise AppError('Вход Google отменён.')
            if 'error' in callback.result:
                reason = callback.result['error']
                code = f' ({reason})' if reason in ('access_denied', 'admin_policy_enforced', 'org_internal', 'invalid_scope') else ''
                raise AppError('Google не подтвердил вход или доступ отменён. Проверьте тестовых пользователей OAuth-проекта и ограничения организации.' + code)
            progress('Проверяем Google-аккаунт и сохраняем вход')
            try:
                flow.fetch_token(code=callback.result['code'], timeout=(10, 45))
                credentials = flow.credentials
                granted = credentials.granted_scopes or credentials.scopes or []
                if isinstance(granted, str):
                    granted = granted.split()
                if not credentials.refresh_token or not set(SCOPES).issubset(granted):
                    raise AppError('Google не выдал полный доступ для повторных запусков. Повторите вход и разрешите Drive и таблицы.')
                with AuthorizedSession(credentials, refresh_timeout=45) as session:
                    response = session.get('https://www.googleapis.com/drive/v3/about', params={'fields': 'user(emailAddress)'}, timeout=(10, 45))
                    if response.status_code >= 400:
                        raise AppError(http_error(response))
                    email = response.json().get('user', {}).get('emailAddress', '')
                if not email:
                    raise AppError('Google не вернул аккаунт пользователя. Подключение не сохранено; повторите вход.')
                if cancelled.is_set():
                    raise AppError('Вход Google отменён.')
                if digest(client_config(path)) != fingerprint:
                    raise AppError('OAuth-файл изменился во время входа. Повторите подключение.')
                info = json.loads(credentials.to_json())
                info['scopes'] = list(granted)
                self.save({'credentials': info, 'email': email, 'client_fingerprint': fingerprint})
                return {'connected': True, 'email': email}
            except AppError:
                raise
            except OAuth2Error as exc:
                messages = {'invalid_client': 'Google не принял OAuth-клиент. Проверьте файл Desktop app.',
                            'invalid_grant': 'Код входа Google истёк или больше недействителен. Повторите вход.',
                            'invalid_scope': 'Google не разрешил запрошенные права Drive и таблиц. Проверьте Data Access OAuth-проекта.',
                            'access_denied': 'Google отказал в доступе. Проверьте тестовых пользователей и правила организации.'}
                reason = getattr(exc, 'error', '')
                message = messages.get(reason, 'Google не завершил вход. Проверьте настройки OAuth-клиента и повторите.')
                raise AppError(message + (f' ({reason})' if reason in messages else '')) from None
            except Exception as exc:
                from .adapters import google_request_error
                raise AppError('Вход Google не завершён. ' + google_request_error(exc)) from None
