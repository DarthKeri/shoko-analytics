"""Local developer diagnostics. Never modify source workbooks, backups or Google files."""
import importlib.metadata
import json
import os
import platform
import shutil
import tempfile
import time
from pathlib import Path
from . import __version__
from .storage import AppError, stamp


def health(store, google):
    rows = []
    def item(name, ok, detail):
        rows.append({'name': name, 'ok': ok, 'detail': detail})
    item('Версия приложения', True, __version__)
    manifest = Path(__file__).resolve().parents[2] / 'build-manifest.json'
    try:
        data = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else None
        item('Сборка', True, data['created_utc'] if data else 'Запуск из исходников')
    except (OSError, ValueError, KeyError):
        item('Сборка', False, 'Не удалось прочитать сведения о сборке')
    item('Среда выполнения', True, f'Python {platform.python_version()} · {platform.system()} {platform.release()}')
    try:
        item('Интерфейс', True, 'pywebview ' + importlib.metadata.version('pywebview'))
    except importlib.metadata.PackageNotFoundError:
        item('Интерфейс', False, 'Компонент pywebview не установлен')
    item('Рабочая папка', store.root.is_dir(), str(store.root))
    try:
        with tempfile.TemporaryFile(dir=store.root / 'output') as stream:
            stream.write(b'Shoko diagnostics'); stream.flush()
        item('Запись в рабочую папку', True, 'Проверена во временном файле')
    except OSError:
        item('Запись в рабочую папку', False, 'Нет доступа к записи')
    try:
        data = json.loads(store.config_path.read_text(encoding='utf-8'))
        item('Настройки', isinstance(data, dict), 'settings.local.json читается')
    except (OSError, ValueError):
        item('Настройки', False, 'settings.local.json отсутствует или повреждён')
    status = google.status(store.config)
    if store.config.get('target_mode') != 'google':
        item('Вход Google', True, 'Не требуется для локальной книги')
    elif store.config.get('google_auth_mode') == 'oauth':
        item('Вход Google', bool(status.get('connected')), 'Вход сохранён; доступ к сайту проверяется в «Источниках»' if status.get('connected') else status.get('message', 'Вход не выполнен'))
    else:
        item('Вход Google', bool(Path(store.config.get('credentials') or 'missing').is_file()), 'Выбран сервисный аккаунт')
    usage = shutil.disk_usage(store.root)
    item('Свободное место', usage.free >= 500 * 1024**2, f'{usage.free / 1024**3:.2f} ГБ')
    return {'checked': stamp(), 'ok': all(r['ok'] for r in rows), 'items': rows}


def occupied_space(root, *, max_files=100_000, max_seconds=8):
    root = Path(root).resolve()
    categories = {'inputs': 'Входные файлы', 'plans': 'Планы и результаты', 'backups': 'Резервные копии',
                  'logs': 'Журналы', 'sessions': 'Подключения', 'output': 'Выходные файлы'}
    counts = {key: {'name': title, 'bytes': 0, 'files': 0} for key, title in categories.items()}
    counts['other'] = {'name': 'Другие файлы', 'bytes': 0, 'files': 0}
    pending = [(root, None)]; deadline = time.monotonic() + max_seconds
    errors = skipped = files = 0; partial = False
    while pending:
        if files >= max_files or time.monotonic() > deadline:
            partial = True; break
        directory, category = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if files >= max_files or time.monotonic() > deadline:
                        partial = True; break
                    try:
                        # Do not follow Windows junctions, symlinks or other reparse points.
                        stat = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
                            skipped += 1; continue
                        group = category or (entry.name if entry.name in categories else 'other')
                        if entry.is_dir(follow_symlinks=False):
                            pending.append((Path(entry.path), group))
                        elif entry.is_file(follow_symlinks=False):
                            counts[group]['bytes'] += stat.st_size; counts[group]['files'] += 1; files += 1
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
    usage = shutil.disk_usage(root)
    return {'checked': stamp(), 'items': list(counts.values()), 'bytes': sum(c['bytes'] for c in counts.values()),
            'files': files, 'free': usage.free, 'total': usage.total, 'partial': partial, 'errors': errors, 'skipped': skipped}
