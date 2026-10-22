"""Start the desktop app with diagnostics before third-party imports."""
import os
from pathlib import Path
import runpy
import sys
import traceback


def run():
    try:
        from startup_diagnostics import initialize, stage, exception_text
        initialize()
        stage('Загрузка точки входа приложения')
        sys.argv[0] = str(Path(__file__).with_name('main.py'))
        entry = runpy.run_path(sys.argv[0], run_name='shoko_application')
        entry['main']()
        return 0
    except KeyboardInterrupt:
        return 0
    except SystemExit as exc:
        if exc.code in (None, 0): return 0
        return exc.code if isinstance(exc.code, int) else 1
    except BaseException as exc:
        try:
            from startup_diagnostics import stage, exception_text
            details = exception_text(exc)
            stage('ОШИБКА ЗАПУСКА\n' + details)
            if sys.stderr: sys.stderr.write(details + '\n')
        except BaseException:
            # The launcher also captures stderr if this diagnostic module is missing.
            if sys.stderr: traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(run())
