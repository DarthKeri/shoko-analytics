import hmac
import io
import json
import mimetypes
import os
import secrets
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from .storage import AppError, Store, InstanceLock, atomic_json


class Server:
    def __init__(self, engine, web_root, port=0):
        self.engine, self.web_root = engine, Path(web_root).resolve()
        self.token = secrets.token_urlsafe(32)
        self.window = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def safe_host(self):
                return self.headers.get('Host') == f'127.0.0.1:{owner.http.server_port}'

            def send(self, status, data, mime='application/json; charset=utf-8', attachment=''):
                if isinstance(data, (dict, list)):
                    data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
                if isinstance(data, str):
                    data = data.encode()
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                if attachment:
                    self.send_header('Content-Disposition', 'attachment; filename="' + attachment + '"')
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                return self.safe_host() and hmac.compare_digest(self.headers.get('X-Shoko-Token', ''), owner.token) and self.headers.get('Origin', owner.url) == owner.url

            def do_GET(self):
                if not self.safe_host():
                    return self.send(403, {'error': 'Недопустимый адрес приложения.'})
                path = urlparse(self.path).path
                if path == '/api/state':
                    if not self.authorized():
                        return self.send(403, {'error': 'Доступ запрещён.'})
                    return self.send(200, owner.engine.public_state())
                if path == '/bootstrap.js':
                    if self.headers.get('Sec-Fetch-Site', 'none') not in ('none', 'same-origin'):
                        return self.send(403, {'error': 'Доступ запрещён.'})
                    return self.send(200, 'window.SHOKO_TOKEN=' + json.dumps(owner.token) + ';', 'text/javascript; charset=utf-8')
                file = (owner.web_root / (path.lstrip('/') or 'index.html')).resolve()
                if not file.is_relative_to(owner.web_root) or not file.is_file():
                    return self.send(404, {'error': 'Страница не найдена.'})
                return self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

            def do_POST(self):
                if not self.authorized():
                    return self.send(403, {'error': 'Доступ запрещён.'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if length <= 0 or length > 65_536:
                        return self.send(400, {'error': 'Некорректный запрос.'})
                    body = json.loads(self.rfile.read(length))
                    if urlparse(self.path).path == '/api/export-copy':
                        manifest, _ = owner.engine.store.copy(body.get('id'))
                        buffer = io.BytesIO()
                        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                            for name in ('manifest.json', 'snapshot.json', 'original.xlsx'):
                                file = Path(manifest['path']) / name
                                if file.exists():
                                    archive.write(file, name)
                        return self.send(200, buffer.getvalue(), 'application/zip', manifest['id'] + '.zip')
                    if urlparse(self.path).path != '/api/action':
                        return self.send(404, {'error': 'Неизвестный запрос.'})
                    return self.send(200, owner.action(body))
                except AppError as exc:
                    return self.send(400, {'error': str(exc)})
                except (ValueError, TypeError, KeyError):
                    return self.send(400, {'error': 'Проверьте введённые данные.'})
                except Exception:
                    owner.engine.store.log('ERROR', 'Не удалось выполнить действие интерфейса. Проверьте настройки и доступ к рабочей папке.')
                    return self.send(500, {'error': 'Не удалось выполнить действие. Проверьте настройки и доступ к рабочей папке.'})
        self.http = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def start(self):
        self.started = True
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def action(self, body):
        action = body.get('action')
        engine = self.engine
        if action == 'settings':
            return engine.save_settings(body.get('values', {}))
        if action == 'finish-setup':
            return engine.finish_setup()
        if action == 'check':
            return engine.check(body.get('source', 'all'), body.get('date', ''))
        if action == 'prepare':
            return engine.prepare(body.get('url', ''), body.get('date', ''), bool(body.get('csv_confirmed')))
        if action == 'write':
            return engine.write(body.get('id'), body.get('checksum'))
        if action == 'cancel':
            return engine.cancel()
        if action == 'plan':
            return engine.plan_page(body.get('kind', 'changes'), body.get('sheet', ''), body.get('offset', 0))
        if action == 'create-copy':
            return engine.create_local_copy(body.get('source', ''))
        if action == 'copy':
            manifest, payload = engine.store.copy(body.get('id'))
            result = {'manifest': manifest}
            if manifest['run_id']:
                path = engine.store.root / 'plans' / (manifest['run_id'] + '.json')
                if path.exists():
                    result['changes'] = json.loads(path.read_text(encoding='utf-8'))['changes'][:100]
            return result
        if action == 'telegram':
            return engine.telegram_auth(body.get('step'), **body.get('values', {}))
        if action == 'choose':
            if not self.window:
                raise AppError('Выбор файла доступен в окне Windows-приложения. Здесь можно вставить полный путь.')
            import webview
            if body.get('kind') == 'folder':
                result = self.window.create_file_dialog(webview.FileDialog.FOLDER)
            else:
                types = {'xlsx': ('Excel (*.xlsx)',), 'csv': ('CSV (*.csv)',), 'json': ('Google key (*.json)',)}
                result = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=types.get(body.get('kind'), ('All files (*.*)',)))
            return {'path': result[0] if result else ''}
        if action == 'open-folder':
            if not self.window or os.name != 'nt':
                raise AppError('Открытие папки доступно в Windows-приложении.')
            folder = engine.store.root
            if body.get('id'):
                manifest, _ = engine.store.copy(body['id'])
                folder = Path(manifest['path'])
            os.startfile(folder)
            return {'opened': True}
        if action == 'root':
            with engine.lock:
                if engine.busy():
                    raise AppError('Дождитесь завершения операции.')
                if engine.config()['initialized']:
                    raise AppError('Рабочую папку задают при первой настройке. Для другой папки запустите приложение с параметром --data-dir.')
                new_root = Path(body.get('path', '')).expanduser().resolve()
                if new_root == PROTECTED_ARCHIVE or PROTECTED_ARCHIVE in new_root.parents:
                    raise AppError('Нельзя хранить рабочие данные в исходном архиве.')
                # No files are moved or deleted. Explicitly selected new folder is initialized.
                from .storage import Store
                from .telegram import Telegram
                if new_root == engine.store.root:
                    return engine.public_state()
                new_store = Store(new_root)
                new_lock = InstanceLock(new_root)
                old_store = engine.store
                engine.telegram.close()
                config = engine.config()
                engine.store = new_store
                if getattr(engine, 'root_lock', None):
                    engine.root_lock.close()
                engine.root_lock = new_lock
                redirected = dict(config, redirect_root=str(new_root))
                old_store.save_config(redirected)
                if not engine.store.config_path.exists():
                    engine.store.save_config(config)
                engine.telegram = Telegram(engine.store)
                return engine.public_state()
        raise AppError('Неизвестное действие.')

    def close(self):
        if getattr(self, 'started', False):
            self.http.shutdown()
        self.http.server_close()


from .adapters import PROTECTED_ARCHIVE
