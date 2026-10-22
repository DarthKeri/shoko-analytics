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
    stage('Bootstrap: Python ' + sys.version.replace('\n', ' ') + '; ' + platform.platform())
    path = os.environ.get('SHOKO_STARTUP_DETAILS')
    if path and _native_log is None:
        try:
            _native_log = open(path, 'a', encoding='utf-8')
            faulthandler.enable(file=_native_log, all_threads=True)
            atexit.register(_close_native_log)
        except (OSError, RuntimeError):
            _close_native_log()

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
        out('ERROR: diagnostic completion marker was not created.')
        return 1
    out('OK: ' + kind + ' startup completed.')
    return 0

def registry_versions():
    if os.name != 'nt':
        return
    import winreg
    for hive, label in [(winreg.HKEY_LOCAL_MACHINE, 'machine'),
                        (winreg.HKEY_CURRENT_USER, 'user')]:
        for flag, view in [(winreg.KEY_WOW64_32KEY, '32-bit'),
                           (winreg.KEY_WOW64_64KEY, '64-bit')]:
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
                    out(f'.NET Framework ({label}, {view}), release: '
                        + str(winreg.QueryValueEx(key, 'Release')[0]))
            except OSError:
                pass

def check_workspace():
    out('\n--- Existing settings: read-only structure check ---')
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ShokoAnalytics'
    seen = set()
    for _ in range(16):
        key = str(root.resolve()).casefold()
        if key in seen:
            out('ERROR: workspace location redirect loop.'); return False
        seen.add(key)
        out('Workspace folder: ' + str(root))
        redirect = None
        for name in ['workspace.location.json', 'settings.local.json']:
            path = root / name
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object')
                out(name + ': JSON structure OK (contents omitted).')
                if name == 'workspace.location.json':
                    redirect = data.get('root')
                elif not redirect:
                    redirect = data.get('redirect_root')
            except Exception as exc:
                out(name + ': ERROR ' + type(exc).__name__ + ': ' + str(exc))
                return False
        if not redirect:
            out('Workspace exists: ' + str(root.exists()))
            return True
        if not isinstance(redirect, str):
            out('ERROR: workspace redirect is not a path string.'); return False
        root = Path(redirect).expanduser()
    out('ERROR: too many workspace redirects.'); return False

def check_manifest(root):
    out('\n--- Application files and folder layout ---')
    required = ['ShokoAnalytics.exe', 'runtime/python.exe', 'runtime/pythonw.exe',
                'runtime/python313.dll', 'runtime/python313._pth',
                'app/main.py', 'app/bootstrap.py', 'app/startup_diagnostics.py',
                'Проверить запуск.cmd', 'app/web/index.html', 'build-manifest.json']
    missing = [name for name in required if not (root / name).is_file()]
    for name in required:
        out(('MISSING: ' if name in missing else 'OK: ') + name)
    if missing:
        out('ERROR: incomplete extraction or changed folder layout.'); return False
    try:
        manifest = json.loads((root / 'build-manifest.json').read_text(encoding='utf-8'))
        out('Application version: ' + str(manifest.get('version')))
        files = manifest['files']
        if not isinstance(files, dict): raise ValueError('Invalid files manifest')
        bad = []
        for name, expected in files.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError('Manifest path outside application folder')
            if not path.is_file():
                bad.append('MISSING: ' + name); continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected: bad.append('CHANGED: ' + name)
        out(f'Checked {len(files)} packaged files; mismatches: {len(bad)}')
        for message in bad[:80]: out(message)
        if len(bad) > 80: out('Remaining mismatches omitted.')
        return not bad
    except Exception:
        out(exception_text()); return False

def run_child(root, kind, data_dir, timeout):
    out('\n--- Isolated ' + kind + ' startup ---')
    out('This check uses a new empty diagnostic folder, not your settings or tables.')
    if kind == 'gui':
        out('A test window may open and will close automatically.')
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
               '--probe', kind, '--data-dir', str(data_dir)]
    try:
        result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout,
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        out(result.stdout.decode('utf-8', errors='replace'))
        out(f'Exit code: {result.returncode} (0x{result.returncode & 0xFFFFFFFF:08X})')
        return result.returncode == 0
    except subprocess.TimeoutExpired as exc:
        if exc.stdout: out(exc.stdout.decode('utf-8', errors='replace'))
        out('ERROR: diagnostic startup timed out. Close any remaining test window.')
    except Exception:
        out(exception_text())
    return False

def diagnostics_main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', choices=['backend', 'gui'])
    parser.add_argument('--data-dir')
    parser.add_argument('--no-gui', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.probe: return probe(root, args.probe, args.data_dir)
    out('SHOKO ANALYTICS - LOCAL STARTUP DIAGNOSTICS')
    out('Time: ' + datetime.now().astimezone().isoformat())
    out('Windows: ' + platform.platform())
    out('Architecture: ' + platform.machine())
    out('Python: ' + sys.version.replace('\n', ' '))
    out('Application folder: ' + str(root))
    out('No network requests or table updates are performed.')
    out('Existing settings and saved sign-in files are not modified or copied.')
    try: registry_versions()
    except Exception: out(exception_text())
    files_ok = check_manifest(root)
    settings_ok = check_workspace()
    out('\n--- Required module imports ---')
    imports_ok = True
    for name in ['truststore', 'openpyxl', 'requests', 'webview', 'clr']:
        try:
            importlib.import_module(name); out('OK: ' + name)
        except Exception:
            imports_ok = False; out('ERROR: ' + name); out(exception_text())
    work = Path(tempfile.mkdtemp(prefix='Shoko-Diagnostics-'))
    out('\nTemporary diagnostic folder: ' + str(work))
    backend_ok = run_child(root, 'backend', work/'backend', 35)
    gui_ok = None
    if backend_ok and not args.no_gui:
        gui_ok = run_child(root, 'gui', work/'gui', 50)
    out('\n--- SUMMARY ---')
    out(f'Files: {files_ok}; existing settings: {settings_ok}; imports: {imports_ok}; '
        f'backend: {backend_ok}; window: {gui_ok}')
    out('Review this report before sharing. It can contain Windows user names and local paths.')
    out('No report is sent automatically. Send the text report to the application maintainer.')
    return 0 if files_ok and settings_ok and imports_ok and backend_ok and gui_ok is not False else 1

if __name__ == '__main__':
    try:
        sys.exit(diagnostics_main())
    except Exception:
        out(exception_text()); sys.exit(1)
