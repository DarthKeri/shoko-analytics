"""Durable private state, verified backups and cross-process exclusion."""
import hashlib
import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class AppError(Exception):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def defaults():
    return dict(initialized=False, prostoi='', drivers='', csv='', target_mode='local', target='',
                credentials='', google_auth_mode='oauth', google_oauth_client='', google_backup_folder='', allowed_targets=[], developer=False, telegram_mode='manual',
                telegram_bot='', telegram_api_id='', telegram_timeout=120, last_url='', last_date='')


def resolve_root(root):
    """Follow a workspace locator; migrate old locators that duplicated settings."""
    root, seen, legacy = Path(root).resolve(), set(), []
    while True:
        if root in seen:
            raise AppError('Цикл перенаправлений рабочей папки.')
        seen.add(root)
        locator, settings = root / 'workspace.location.json', root / 'settings.local.json'
        try:
            if locator.exists():
                redirect = json.loads(locator.read_text(encoding='utf-8'))['root']
            elif settings.exists():
                previous = json.loads(settings.read_text(encoding='utf-8'))
                redirect = previous.get('redirect_root')
                if redirect:
                    legacy.append((root, previous))
            else:
                redirect = None
        except (ValueError, KeyError, OSError):
            raise AppError('Не удалось прочитать расположение данных или settings.local.json.') from None
        if not redirect:
            break
        root = Path(redirect).expanduser().resolve()
    if legacy:
        if not (root / 'settings.local.json').exists():
            atomic_json(root / 'settings.local.json', {k: v for k, v in legacy[-1][1].items() if k != 'redirect_root'})
        for old_root, _ in legacy:
            atomic_json(old_root / 'workspace.location.json', {'root': str(root)})
            (old_root / 'settings.local.json').unlink()
    return root


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for name in ('inputs', 'plans', 'backups', 'logs', 'sessions', 'output'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.mutex = threading.RLock()
        for path in (self.root / 'backups').glob('*/manifest.json'):
            previous_copy = json.loads(path.read_text(encoding='utf-8'))
            if previous_copy.get('kind') == 'google-drive' and previous_copy.get('status') == 'creating':
                previous_copy.update(status='failed', error='Приложение закрыто во время копирования. Проверьте папку Google Drive; запись не начиналась.')
                atomic_json(path, previous_copy)
        self.config_path = self.root / 'settings.local.json'
        self.config = defaults()
        if self.config_path.exists():
            try:
                self.config.update(json.loads(self.config_path.read_text(encoding='utf-8')))
            except (ValueError, OSError) as exc:
                raise AppError('Настройки повреждены. Сохраните файл settings.local.json и проверьте его.') from exc
            # Older builds saved fields but never completed the all-or-nothing wizard.
            if not self.config['initialized'] and any(self.config.get(k) for k in ('prostoi', 'drivers', 'csv', 'target', 'credentials', 'telegram_bot')):
                self.config['initialized'] = True
                atomic_json(self.config_path, self.config)

    def backup_json(self, value, kind, target, run_id=''):
        identity = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        folder = self.root / 'backups' / identity
        folder.mkdir()
        payload = folder / 'snapshot.json'
        atomic_json(payload, value)
        if digest(json.loads(payload.read_text(encoding='utf-8'))) != digest(value):
            raise AppError('Не удалось проверить резервную копию. Запись заблокирована.')
        manifest = dict(id=identity, created=stamp(), kind=kind, target=target, run_id=run_id,
                        snapshot_hash=file_hash(payload), path=str(folder))
        atomic_json(folder / 'manifest.json', manifest)
        return manifest

    def backup_file(self, source, run_id='', kind='local'):
        source = Path(source)
        before = file_hash(source)
        manifest = self.backup_json({'file': source.name, 'sha256': before}, kind, str(source), run_id)
        destination = Path(manifest['path']) / 'original.xlsx'
        shutil.copy2(source, destination)
        if file_hash(destination) != before or file_hash(source) != before:
            raise AppError('Файл изменился во время копирования. Запись заблокирована.')
        manifest['file_hash'] = before
        atomic_json(Path(manifest['path']) / 'manifest.json', manifest)
        return manifest

    def save_config(self, values):
        with self.mutex:
            candidate = dict(self.config, **values)
            if candidate == self.config and self.config_path.exists():
                return
            atomic_json(self.config_path, candidate)
            self.config = candidate

    def log(self, level, message):
        # Only sanitized, user-facing messages are passed here. No credential payloads.
        line = f'[{stamp()}] [{level}] {message}\n'
        with self.mutex:
            with (self.root / 'logs' / 'application.log').open('a', encoding='utf-8') as stream:
                stream.write(line)

    def logs(self):
        path = self.root / 'logs' / 'application.log'
        if not path.exists():
            return ''
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 100_000))
            return stream.read().decode('utf-8', errors='replace')

    def copies(self):
        manifests = [json.loads(p.read_text(encoding='utf-8')) for p in (self.root / 'backups').glob('*/manifest.json')]
        return sorted([m for m in manifests if m['kind'] != 'settings'], key=lambda x: x['created'], reverse=True)

    def copy(self, identity):
        if not isinstance(identity, str) or not all(c.isalnum() or c == '-' for c in identity):
            raise AppError('Неверный идентификатор копии.')
        folder = self.root / 'backups' / identity
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if file_hash(folder / 'snapshot.json') != manifest['snapshot_hash']:
            raise AppError('Резервная копия повреждена.')
        if manifest.get('file_hash') and file_hash(folder / 'original.xlsx') != manifest['file_hash']:
            raise AppError('Файловая копия повреждена.')
        return manifest, json.loads((folder / 'snapshot.json').read_text(encoding='utf-8'))


class InstanceLock:
    def __init__(self, root):
        self.stream = (Path(root) / 'instance.lock').open('a+b')
        self.stream.seek(0)
        self.stream.write(b'0')
        self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise AppError('Приложение с этой рабочей папкой уже запущено.') from exc

    def close(self):
        self.stream.close()
