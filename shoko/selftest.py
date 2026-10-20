"""Offline acceptance checks on throwaway synthetic workbooks only."""
import tempfile
import time
from pathlib import Path
from openpyxl import load_workbook
from .storage import AppError, file_hash, stamp
from .calculation import TARGET
from .samples import create_samples


def run_selftest():
    from .engine import Engine
    items = []; started = time.monotonic()
    def wait(engine):
        worker = engine.worker
        if worker: worker.join(30)
        if engine.busy(): raise AppError('Тест не завершился за 30 секунд')
        if engine.state['phase'] == 'error': raise AppError(engine.state['error'])
    def check(name, function):
        try:
            function(); items.append({'name': name, 'ok': True, 'detail': 'Пройдено'})
        except Exception:
            items.append({'name': name, 'ok': False, 'detail': 'Проверка не пройдена'})
            raise
    def require(value):
        if not value: raise AppError('Не совпал ожидаемый результат')
    with tempfile.TemporaryDirectory(prefix='shoko-selftest-') as temporary:
        root = Path(temporary); paths = create_samples(root/'files'); engine = Engine(root/'data')
        try:
            engine.save_settings(paths); original = file_hash(paths['target'])
            def sources():
                engine.check('all', paths['last_date']); wait(engine)
                require(all(item['ok'] for item in engine.checks.values()))
            check('Проверка Excel и CSV', sources)
            def preview():
                engine.prepare('', paths['last_date']); wait(engine)
                require(engine.state['phase'] == 'preview' and engine.plan['after'][TARGET][1][5:8] == [1300,1200,1])
                require(file_hash(paths['target']) == original and not engine.store.copies())
            check('Расчёт и подготовка без записи', preview)
            plan = engine.plan
            def wrong_approval():
                try: engine.write(plan['id'], 'not-reviewed')
                except AppError: return
                raise AppError('Принято неверное подтверждение')
            check('Блокировка неверного подтверждения', wrong_approval)
            def backup_and_write():
                engine.write(plan['id'], plan['checksum']); wait(engine)
                require(engine.state['phase'] == 'done')
                result = engine.public_state()['history'][0]; manifest, _ = engine.store.copy(result['backup'])
                require(file_hash(Path(manifest['path'])/'original.xlsx') == original)
                wb = load_workbook(paths['target'])
                try:
                    require(wb[TARGET]['F2'].value == 1300 and wb['BF_'+TARGET]['F2'].value == 999)
                finally: wb.close()
            check('Полная копия, BF-копия и сверка записи', backup_and_write)
            def unchanged():
                engine.prepare('', paths['last_date']); wait(engine)
                require(not engine.plan['changes'] and not engine.plan.get('sheet_backups') and not engine.plan.get('operations'))
            check('Повторный запуск без изменений', unchanged)
            def missing():
                Path(paths['csv']).unlink(); engine.check('csv', paths['last_date'])
                worker = engine.worker
                if worker: worker.join(30)
                require(not engine.busy() and not engine.checks['csv']['ok'])
            check('Ошибка при отсутствии CSV', missing)
        except Exception:
            pass  # Results are data for the UI, never a claim that a failed suite passed.
        finally:
            engine.close()
    return {'checked': stamp(), 'ok': len(items) == 6 and all(i['ok'] for i in items),
            'items': items, 'seconds': round(time.monotonic()-started, 2)}
