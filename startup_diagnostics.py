"""Local startup diagnostics. No uploads, configuration edits or Google writes."""
import argparse
import contextlib
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import runpy
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime


# Standard-library-only startup support. Do not import application adapters here.
import atexit
import faulthandler
import re

_native_log = None
_MAX_DETAILS = 131072
_SECRET_LINE = re.compile(
    r'''(?im)["']?\b(client[_-]?secret|access[_-]?token|refresh[_-]?token|id[_-]?token|password|authorization|cookie|api[_-]?key|code[_-]?verifier)["']?\s*[:=][^\r\n]*''')


def sanitize(text):
    text = str(text)
    home = os.environ.get('USERPROFILE', '')
    if home:
        text = re.sub(re.escape(home), lambda _: '%USERPROFILE%', text, flags=re.I)
    text = _SECRET_LINE.sub(lambda m: m.group(1) + '=[REDACTED]', text)
    text = re.sub(r'''(?i)\bBearer\s+[^\s"'<>]+''', 'Bearer [REDACTED]', text)
    text = re.sub(r'\b(?:ya29\.|1//|GOCSPX-|gh[pousr]_)[A-Za-z0-9._~-]+', '[REDACTED]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'''(?i)(https?://[^\s?"'<>]+)\?[^\s"'<>]*''', lambda m: m.group(1) + '?[REDACTED]', text)
    return text


def exception_text(error=None):
    error = error or sys.exc_info()[1]
    return sanitize(''.join(traceback.TracebackException.from_exception(error, capture_locals=False).format()))


def stage(message):
    path = os.environ.get('SHOKO_STARTUP_DETAILS')
    if not path:
        return
    try:
        target = Path(path)
        if target.exists() and target.stat().st_size >= _MAX_DETAILS:
            return
        with target.open('a', encoding='utf-8') as stream:
            stream.write(sanitize(datetime.now().astimezone().isoformat() + ' | ' + str(message))[:32768] + '\n')
    except OSError:
        pass


def _close_native_log():
    global _native_log
    if _native_log is not None:
        try:
            faulthandler.disable()
            _native_log.close()
        except Exception:
            pass
        _native_log = None


def initialize():
    global _native_log
    stage('Запуск встроенного Python ' + sys.version.replace('\n', ' ') + '; ' + platform.platform())
    path = os.environ.get('SHOKO_STARTUP_DETAILS')
    if path and _native_log is None:
        try:
            _native_log = open(path, 'a', encoding='utf-8')
            faulthandler.enable(file=_native_log, all_threads=True)
            atexit.register(_close_native_log)
        except (OSError, RuntimeError):
            _close_native_log()

def result_text(value):
    return 'успешно' if value is True else 'ошибка' if value is False else 'не выполнялась'


def out(message=''):
    print(sanitize(message), flush=True)

def probe(root, kind, data_dir):
    os.chdir(root)
    sys.argv = [str(root / 'app/main.py'), '--data-dir', data_dir,
                '--smoke' if kind == 'backend' else '--ui-smoke']
    try:
        entry = runpy.run_path(str(root / 'app/main.py'), run_name='shoko_diagnostic')
        entry['main']()
    except BaseException:
        out(exception_text())
        return 1
    expected = Path(data_dir) / ('smoke-ok.txt' if kind == 'backend' else 'ui-smoke.json')
    if not expected.exists():
        out('ОШИБКА: проверка не подтвердила успешное завершение запуска.')
        return 1
    out('УСПЕШНО: ' + {'backend':'ядро приложения','gui':'окно приложения'}[kind] + ' запущено.')
    return 0

def registry_versions():
    if os.name != 'nt':
        return
    import winreg
    for hive, label in [(winreg.HKEY_LOCAL_MACHINE, 'компьютер'),
                        (winreg.HKEY_CURRENT_USER, 'пользователь')]:
        for flag, view in [(winreg.KEY_WOW64_32KEY, '32-разрядный реестр'),
                           (winreg.KEY_WOW64_64KEY, '64-разрядный реестр')]:
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\EdgeUpdate\Clients',
                                    0, winreg.KEY_READ | flag) as clients:
                    for i in range(winreg.QueryInfoKey(clients)[0]):
                        try:
                            with winreg.OpenKey(clients, winreg.EnumKey(clients, i)) as product:
                                name = str(winreg.QueryValueEx(product, 'name')[0])
                                if 'webview' in name.lower():
                                    version = str(winreg.QueryValueEx(product, 'pv')[0])
                                    out(f'WebView2 ({label}, {view}): {version}')
                        except OSError:
                            continue
            except OSError:
                pass
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full',
                                    0, winreg.KEY_READ | flag) as key:
                    out(f'.NET Framework ({label}, {view}), код выпуска: '
                        + str(winreg.QueryValueEx(key, 'Release')[0]))
            except OSError:
                pass

def check_workspace():
    out('\n--- Проверка структуры существующих настроек без изменения ---')
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ShokoAnalytics'
    seen = set()
    for _ in range(16):
        key = str(root.resolve()).casefold()
        if key in seen:
            out('ОШИБКА: обнаружен цикл перенаправлений рабочей папки.'); return False
        seen.add(key)
        out('Рабочая папка: ' + str(root))
        redirect = None
        for name in ['workspace.location.json', 'settings.local.json']:
            path = root / name
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('Ожидался JSON-объект настроек')
                out(name + ': структура JSON корректна; значения в отчёт не включены.')
                if name == 'workspace.location.json':
                    redirect = data.get('root')
                elif not redirect:
                    redirect = data.get('redirect_root')
            except Exception as exc:
                out(name + ': ОШИБКА ' + type(exc).__name__ + ': ' + str(exc))
                return False
        if not redirect:
            out('Рабочая папка существует: ' + ('да' if root.exists() else 'нет'))
            return True
        if not isinstance(redirect, str):
            out('ОШИБКА: адрес рабочей папки должен быть строкой пути.'); return False
        root = Path(redirect).expanduser()
    out('ОШИБКА: слишком много перенаправлений рабочей папки.'); return False

def check_manifest(root):
    out('\n--- Комплектность файлов и структура папок приложения ---')
    required = ['ShokoAnalytics.exe', 'runtime/python.exe', 'runtime/pythonw.exe',
                'runtime/python313.dll', 'runtime/python313._pth',
                'app/main.py', 'app/bootstrap.py', 'app/startup_diagnostics.py',
                'Проверить запуск.cmd', 'app/web/index.html', 'build-manifest.json']
    missing = [name for name in required if not (root / name).is_file()]
    for name in required:
        out(('НЕТ ФАЙЛА: ' if name in missing else 'УСПЕШНО: ') + name)
    if missing:
        out('ОШИБКА: архив распакован не полностью или нарушена структура папок.'); return False
    try:
        manifest = json.loads((root / 'build-manifest.json').read_text(encoding='utf-8'))
        out('Версия приложения: ' + str(manifest.get('version')))
        files = manifest['files']
        if not isinstance(files, dict): raise ValueError('Некорректный список файлов в манифесте')
        bad = []
        for name, expected in files.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError('Путь из манифеста выходит за пределы папки приложения')
            if not path.is_file():
                bad.append('НЕТ ФАЙЛА: ' + name); continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected: bad.append('ИЗМЕНЁН ФАЙЛ: ' + name)
        out(f'Проверено файлов поставки: {len(files)}; отсутствуют или изменены: {len(bad)}')
        for message in bad[:80]: out(message)
        if len(bad) > 80: out('Остальные несовпадения не показаны: список ограничен первыми 80.')
        return not bad
    except Exception:
        out(exception_text()); return False

def run_child(root, kind, data_dir, timeout):
    out('\n--- Проверка запуска: ' + {'backend':'ядро приложения','gui':'окно приложения'}[kind] + ' ---')
    out('Используется новая пустая диагностическая папка. Ваши настройки и таблицы не изменяются.')
    if kind == 'gui':
        out('Может открыться тестовое окно; после проверки оно закроется автоматически.')
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
               '--probe', kind, '--data-dir', str(data_dir)]
    try:
        result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout,
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        out(result.stdout.decode('utf-8', errors='replace'))
        out(f'Код завершения: {result.returncode} (0x{result.returncode & 0xFFFFFFFF:08X})')
        return result.returncode == 0
    except subprocess.TimeoutExpired as exc:
        if exc.stdout: out(exc.stdout.decode('utf-8', errors='replace'))
        out('ОШИБКА: превышено время ожидания запуска. Закройте оставшееся тестовое окно, если оно есть.')
    except Exception:
        out(exception_text())
    return False

def diagnostics_main():
    # Isolated Python ignores PYTHONIOENCODING; use one encoding for the report and probes.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', choices=['backend', 'gui'])
    parser.add_argument('--data-dir')
    parser.add_argument('--no-gui', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.probe: return probe(root, args.probe, args.data_dir)
    out('SHOKO ANALYTICS — ДИАГНОСТИКА ЗАПУСКА')
    out('Время: ' + datetime.now().astimezone().isoformat())
    out('Windows: ' + platform.platform())
    out('Архитектура системы: ' + platform.machine())
    out('Python: ' + sys.version.replace('\n', ' '))
    out('Папка приложения: ' + str(root))
    out('Проверка не обращается к Google и не обновляет таблицы.')
    out('Настройки и сохранённые входы не изменяются и не копируются в отчёт.')
    try: registry_versions()
    except Exception: out(exception_text())
    files_ok = check_manifest(root)
    settings_ok = check_workspace()
    out('\n--- Проверка загрузки обязательных компонентов ---')
    imports_ok = True
    for name in ['truststore', 'openpyxl', 'requests', 'webview', 'clr']:
        try:
            importlib.import_module(name); out('УСПЕШНО: ' + name)
        except Exception:
            imports_ok = False; out('ОШИБКА: ' + name); out(exception_text())
    work = Path(tempfile.mkdtemp(prefix='Shoko-Diagnostics-'))
    out('\nВременная диагностическая папка: ' + str(work))
    backend_ok = run_child(root, 'backend', work/'backend', 35)
    gui_ok = None
    if backend_ok and not args.no_gui:
        gui_ok = run_child(root, 'gui', work/'gui', 50)
    out('\n--- ИТОГ ПРОВЕРКИ ---')
    out('Файлы: ' + result_text(files_ok) + '\nСтруктура настроек: ' + result_text(settings_ok) + '\nКомпоненты: ' + result_text(imports_ok) + '\nЯдро приложения: ' + result_text(backend_ok) + '\nОкно приложения: ' + result_text(gui_ok))
    out('Перед передачей просмотрите отчёт. В нём могут встречаться локальные пути.')
    out('Отчёт никуда не отправляется автоматически. Передайте этот текстовый файл ответственному за приложение.')
    return 0 if files_ok and settings_ok and imports_ok and backend_ok and gui_ok is not False else 1

if __name__ == '__main__':
    try:
        sys.exit(diagnostics_main())
    except Exception:
        out(exception_text()); sys.exit(1)
