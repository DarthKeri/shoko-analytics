"""Windows entrypoint. --browser is an explicit development/QA mode."""
import argparse
import json
import os
import sys
import threading
from pathlib import Path
# Follow Windows' configured trust store for Google HTTPS, including managed CAs.
# Hostname and certificate verification remain enabled.
if os.name == 'nt':
    import truststore
    truststore.inject_into_ssl()
from shoko import __version__
from shoko.engine import Engine
from shoko.server import Server
from shoko.storage import AppError, InstanceLock, resolve_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--ui-smoke', action='store_true', help='Open native window, check loaded UI and close for QA')
    parser.add_argument('--smoke', action='store_true', help='Validate bundled assets and backend; do not open a window')
    args = parser.parse_args()
    root = args.data_dir or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'ShokoAnalytics'
    root = resolve_root(root)
    from shoko.adapters import PROTECTED_ARCHIVE
    if root.resolve() == PROTECTED_ARCHIVE or PROTECTED_ARCHIVE in root.resolve().parents:
        raise AppError('Исходный архив нельзя использовать как рабочую папку.')
    assets = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'web'
    if not (assets / 'index.html').exists():
        raise AppError('Файлы интерфейса не найдены. Выполните сборку frontend.')
    root.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(root)
    engine = Engine(root)
    server = Server(engine, assets, args.port)
    try:
        if args.smoke:
            assert engine.public_state()['config'] is not None
            (root / 'smoke-ok.txt').write_text(f'Shoko Analytics {__version__}: assets and backend loaded', encoding='utf-8')
            return
        server.start()
        if args.browser:
            print(server.url, flush=True)
            threading.Event().wait()
        else:
            import webview
            if os.name == 'nt':
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Shoko.Analytics.Desktop')
            window = webview.create_window('Shoko Analytics', server.url, width=1120, height=760, min_size=(860, 600), background_color='#0d0d0d', confirm_close=False)
            server.window = window
            def closing():
                phase = engine.state['phase']
                if phase == 'writing':
                    window.create_confirmation_dialog('Запись выполняется', 'Дождитесь завершения записи и сверки. Закрытие сейчас недоступно.')
                    return False
                if phase == 'preparing':
                    if not window.create_confirmation_dialog('Отменить подготовку и закрыть?', 'Журнал сохранится. Изменения в таблицу не записывались.'):
                        return False
                    engine.cancelled.set()
                return True
            window.events.closing += closing
            def ui_smoke():
                import time
                try:
                    for _ in range(60):
                        time.sleep(0.5)
                        heading = window.evaluate_js('document.querySelector("h2")?.textContent || ""')
                        if heading in ('Настройка приложения', 'Обновление данных'):
                            (root / 'ui-smoke.json').write_text(json.dumps({'title': window.evaluate_js('document.title'), 'heading': heading, 'engine': 'edgechromium'}, ensure_ascii=False), encoding='utf-8')
                            return
                finally:
                    window.destroy()
            webview.start(ui_smoke if args.ui_smoke else None, gui='edgechromium', debug=False, private_mode=True, icon=str(assets.parent / 'assets' / 'shoko.ico'))
    finally:
        server.close()
        engine.close()
        lock.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        message = str(exc) if isinstance(exc, AppError) else 'Не удалось запустить Shoko Analytics. Проверьте WebView2 и права на рабочую папку.'
        if os.name == 'nt' and not sys.stdout:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, 'Shoko Analytics', 0x10)
        else:
            print(message, file=sys.stderr)
        sys.exit(1)
