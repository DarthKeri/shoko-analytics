import copy
import json
import os
import shutil
import threading
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse
from .storage import AppError, Store, atomic_json, digest, file_hash, stamp
from .adapters import readable, read_excel, read_csv, target, guard_target, target_key, changes_for, PROTECTED_ARCHIVE
from .calculation import assemble_prostoi, calculate, PROSTOI, DRIVERS, TARGET, GARAGE
from .telegram import Telegram

STEPS = ['Проверка файлов и доступа', 'Чтение исходных данных', 'Получение CSV', 'Подготовка изменений']
CONFIG_KEYS = {'prostoi', 'drivers', 'csv', 'target_mode', 'target', 'credentials', 'developer', 'telegram_mode', 'telegram_bot', 'telegram_timeout', 'last_url', 'last_date'}


def resolved(value, processing_date):
    return value.format(year=processing_date.year, yy=str(processing_date.year)[-2:], month=f'{processing_date.month:02d}')


class Engine:
    def __init__(self, root):
        self.store = Store(root)
        self.telegram = Telegram(self.store)
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.state = dict(phase='idle', step=-1, started='', error='', plan_id='', operation='')
        self.plan = None
        self.checks = {}
        self.worker = None
        self.store.log('INFO', 'Приложение запущено. Запись выполняется только после просмотра и подтверждения.')
        # Interrupted write is never declared successful after restart.
        for path in (self.store.root / 'plans').glob('*.result.json'):
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('status') == 'writing':
                data['status'] = 'interrupted'
                data['message'] = 'Приложение закрыто во время записи. Проверьте цель и резервную копию.'
                atomic_json(path, data)

    def config(self):
        return copy.deepcopy(self.store.config)

    def busy(self):
        return self.state['phase'] in ('preparing', 'writing', 'checking', 'connecting')

    def save_settings(self, values):
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения операции перед изменением настроек.')
            candidate = dict(self.config(), **{k: v for k, v in values.items() if k in CONFIG_KEYS})
            if candidate['target_mode'] not in ('local', 'google') or candidate['telegram_mode'] not in ('manual', 'auto'):
                raise AppError('Неверный тип подключения.')
            try:
                candidate['telegram_timeout'] = max(15, min(150, int(candidate['telegram_timeout'])))
            except (TypeError, ValueError):
                raise AppError('Время ожидания бота должно быть числом.') from None
            if 'test_copy_confirmed' in values:
                candidate['allowed_targets'] = [target_key(candidate)] if values['test_copy_confirmed'] else []
            # Saving incomplete first-run settings is allowed. Writes require guard_target.
            if candidate['target'] and candidate['allowed_targets']:
                guard_target(candidate)
            candidate['initialized'] = self.config()['initialized']
            self.store.save_config(candidate)
            self.plan = None
            self.state.update(phase='idle', plan_id='', error='')
            self.checks = {}
            self.store.log('INFO', 'Настройки сохранены. Предыдущая версия сохранена перед изменением.')
            return self.public_state()

    def finish_setup(self):
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения проверки.')
            config = self.config()
            processing = date.fromisoformat(config['last_date'] or date.today().isoformat())
            inputs = self.inputs(config, processing)
            for item in ('prostoi', 'drivers', 'target', 'csv'):
                self.check_one(item, inputs, processing)
                self.checks[item] = {'ok': True, 'message': 'Доступен', 'checked': stamp(), 'date': processing.isoformat()}
            config['initialized'] = True
            self.store.save_config(config)
            return {'finished': True}

    def public_state(self):
        with self.lock:
            histories = []
            for path in (self.store.root / 'plans').glob('*.result.json'):
                histories.append(json.loads(path.read_text(encoding='utf-8')))
            plan = None
            if self.plan:
                plan = {k: self.plan[k] for k in ('id', 'date', 'url', 'warnings', 'created', 'checksum')}
                plan['count'] = len(self.plan['changes'])
                plan['cleared'] = sum(c['after'] == '' for c in self.plan['changes'])
                plan['sheets'] = [{'name': k, 'rows': len(v), 'columns': max(map(len, v), default=0), 'changes': sum(c['sheet'] == k for c in self.plan['changes'])} for k, v in self.plan['after'].items()]
            try:
                target_allowed = target_key(self.config()) in self.config()['allowed_targets']
            except AppError:
                target_allowed = False
            return dict(target_allowed=target_allowed, config=self.config(), root=str(self.store.root), state=copy.deepcopy(self.state), checks=copy.deepcopy(self.checks), plan=plan,
                        history=sorted(histories, key=lambda h: h['created'], reverse=True)[:100], copies=self.store.copies()[:100], logs=self.store.logs())

    def plan_page(self, kind='changes', sheet='', offset=0):
        with self.lock:
            if not self.plan:
                raise AppError('План ещё не подготовлен.')
            offset = max(0, int(offset))
            if kind == 'changes':
                data = self.plan['changes']
            elif sheet in self.plan['after']:
                data = copy.deepcopy(self.plan['after'][sheet])
                changed = {(c['row'] - 1, c['col'] - 1) for c in self.plan['changes'] if c['sheet'] == sheet}
                for r, row in enumerate(self.plan['before']['sheets'].get(sheet, {}).get('raw', [])):
                    for c, value in enumerate(row):
                        if isinstance(value, str) and value.startswith('=') and (r, c) not in changed and r < len(data) and c < len(data[r]):
                            data[r][c] = value
            else:
                raise AppError('Неизвестный лист плана.')
            return {'data': data[offset:offset + 100], 'total': len(data), 'offset': offset}

    def set_phase(self, **values):
        with self.lock:
            self.state.update(values)

    def launch(self, operation, function):
        with self.lock:
            if self.busy():
                raise AppError('Уже выполняется операция. Дождитесь её завершения.')
            self.cancelled.clear()
            phase = {'prepare': 'preparing', 'write': 'writing', 'check': 'checking', 'telegram': 'connecting'}[operation]
            self.state.update(phase=phase, operation=operation, started=stamp(), error='', step=-1)
            def work():
                try:
                    function()
                except Exception as exc:
                    message = str(exc) if isinstance(exc, AppError) else 'Операция не завершена. Проверьте файлы и настройки подключения.'
                    if self.cancelled.is_set() and operation == 'prepare':
                        self.set_phase(phase='cancelled', error='Подготовка отменена.')
                        self.store.log('INFO', 'Подготовка отменена. Запись не выполнялась.')
                    else:
                        self.set_phase(phase='error', error=message)
                        self.store.log('ERROR', message)
                finally:
                    with self.lock:
                        if self.worker is threading.current_thread():
                            self.worker = None
            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()
            return {'started': True}

    def check_cancel(self):
        if self.cancelled.is_set():
            raise AppError('Подготовка отменена.')

    def inputs(self, config, processing_date):
        result = dict(config)
        result['prostoi'] = resolved(config['prostoi'], processing_date)
        result['drivers'] = resolved(config['drivers'], processing_date - timedelta(days=1))
        return result

    def check_one(self, name, config, processing_date):
        if name == 'prostoi':
            read_excel(config['prostoi'], f'Кол_состав_{processing_date:%m-%y}', 373, 345)
            if processing_date.day < 15:
                previous = processing_date.replace(day=1) - timedelta(days=1)
                read_excel(config['prostoi'], f'Кол_состав_{previous:%m-%y}', 373, 345)
        elif name == 'drivers':
            read_excel(config['drivers'], f'{(processing_date - timedelta(days=1)).day:02d}', 100_000, 50)
        elif name == 'credentials':
            readable(config['credentials'], {'.json'})
            try:
                data = json.loads(Path(config['credentials']).read_text(encoding='utf-8'))
            except Exception:
                raise AppError('credentials.json не читается или содержит неверный JSON.') from None
            if data.get('type') != 'service_account' or not data.get('private_key') or not data.get('client_email'):
                raise AppError('credentials.json должен содержать ключ сервисного аккаунта Google.')
        elif name == 'target':
            if config['target_mode'] == 'google':
                self.check_one('credentials', config, processing_date)
            target(config).snapshot()
        elif name == 'csv':
            if config['telegram_mode'] == 'auto':
                self.telegram.call(self.telegram.check(config['telegram_bot']))
            else:
                read_csv(config['csv'])
        else:
            raise AppError('Неизвестный источник.')

    def check(self, name='all', processing_date=''):
        processing = date.fromisoformat(processing_date or date.today().isoformat())
        config = self.inputs(self.config(), processing)
        names = ['prostoi', 'drivers', 'target', 'csv'] if name == 'all' else [name]
        def work():
            errors = []
            for item in names:
                try:
                    self.check_one(item, config, processing)
                    value = {'ok': True, 'message': 'Доступен', 'checked': stamp(), 'date': processing.isoformat()}
                    self.store.log('INFO', f'Проверка источника {item}: доступен. Запись не выполнялась.')
                except Exception as exc:
                    message = str(exc) if isinstance(exc, AppError) else 'Источник недоступен. Проверьте настройки.'
                    value = {'ok': False, 'message': message, 'checked': stamp(), 'date': processing.isoformat()}
                    errors.append(message)
                    self.store.log('ERROR', message)
                with self.lock:
                    self.checks[item] = value
            self.set_phase(phase='idle' if not errors else 'error', error='\n'.join(errors))
        return self.launch('check', work)

    def prepare(self, url, processing_date, csv_confirmed=False):
        processing = date.fromisoformat(processing_date)
        config = self.inputs(self.config(), processing)
        parsed = urlparse(url)
        if parsed.scheme not in ('https', 'http') or not parsed.netloc or parsed.username or parsed.password:
            raise AppError('Вставьте корректную ссылку на Гараж.')
        if config['telegram_mode'] == 'manual' and not csv_confirmed:
            raise AppError('Подтвердите, что выбранный CSV получен для этой ссылки. Это связывает файл с текущим запросом.')
        guard_target(config)
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения операции.')
            latest = self.config()
            latest.update(last_url=url, last_date=processing_date)
            if latest != self.config():
                self.store.save_config(latest)
        def work():
            self.plan = None
            self.set_phase(plan_id='', step=0)
            identity = uuid.uuid4().hex
            for key in ('prostoi', 'drivers'):
                readable(config[key], {'.xlsx'})
            adapter = target(config)
            self.check_cancel()
            source_hashes = {key: file_hash(config[key]) for key in ('prostoi', 'drivers')}
            self.set_phase(step=1)
            current = read_excel(config['prostoi'], f'Кол_состав_{processing:%m-%y}', 373, 345)
            previous = []
            if processing.day < 15:
                last_month = processing.replace(day=1) - timedelta(days=1)
                previous = read_excel(config['prostoi'], f'Кол_состав_{last_month:%m-%y}', 373, 345)
            drivers = read_excel(config['drivers'], f'{(processing - timedelta(days=1)).day:02d}', 100_000, 50)
            self.check_cancel()
            self.set_phase(step=2)
            if config['telegram_mode'] == 'auto':
                config['csv'] = self.telegram.call(self.telegram.download(config, url, identity, self.cancelled))
            csv_path = readable(config['csv'], {'.csv'})
            source_hashes['csv'] = file_hash(csv_path)
            garage = read_csv(csv_path)
            self.check_cancel()
            self.set_phase(step=3)
            before = adapter.snapshot()
            prostoi = assemble_prostoi(current, previous, before['sheets'].get(PROSTOI, {}).get('values', []), processing)
            prices, warnings = calculate(prostoi, drivers, before['sheets'][TARGET]['values'], processing)
            after = {PROSTOI: prostoi, DRIVERS: drivers, TARGET: prices, GARAGE: garage}
            changes = changes_for(before, after)
            self.check_cancel()
            sources = {key: {'path': config[key], 'sha256': hash_value} for key, hash_value in source_hashes.items()}
            for item in sources.values():
                if file_hash(item['path']) != item['sha256']:
                    raise AppError('Исходный файл изменился во время подготовки. Повторите обновление.')
            plan = dict(id=identity, date=processing_date, url=url, created=stamp(), rule='primary-legacy-v1',
                        before=before, after=after, changes=changes, sources=sources, config_digest=digest(self.config()), warnings=warnings)
            plan['checksum'] = digest(plan)
            atomic_json(self.store.root / 'plans' / (identity + '.json'), plan)
            self.check_cancel()
            with self.lock:
                self.plan = plan
            self.set_phase(phase='preview', plan_id=identity, error='', step=4)
            self.store.log('INFO', f'Подготовлен план: {len(changes)} изменений. Запись ожидает подтверждения.')
        return self.launch('prepare', work)

    def cancel(self):
        with self.lock:
            if self.state['phase'] != 'preparing':
                raise AppError('Сейчас нет подготовки, которую можно отменить.')
            self.cancelled.set()
            return {'cancelled': True}

    def write(self, identity, checksum):
        with self.lock:
            if not self.plan or self.plan['id'] != identity or self.plan['checksum'] != checksum or self.state['phase'] != 'preview':
                raise AppError('Подтверждение не соответствует текущему плану. Подготовьте обновление заново.')
            approved = copy.deepcopy(self.plan)
        def work():
            guard_target(self.config())
            if digest(self.config()) != approved['config_digest']:
                raise AppError('Настройки изменились после просмотра. Подготовьте новый план.')
            saved = json.loads((self.store.root / 'plans' / (identity + '.json')).read_text(encoding='utf-8'))
            content = dict(saved)
            expected = content.pop('checksum')
            if digest(content) != expected or expected != checksum:
                raise AppError('Сохранённый план повреждён. Запись заблокирована.')
            for item in approved['sources'].values():
                if file_hash(item['path']) != item['sha256']:
                    raise AppError('Исходный файл изменился после просмотра. Подготовьте новый план.')
            self.set_phase(step=0)
            adapter = target(self.config())
            current = adapter.snapshot()
            if current['fingerprint'] != approved['before']['fingerprint']:
                raise AppError('Целевая таблица изменилась после просмотра. Подготовьте новый план.')
            self.set_phase(step=1)
            backup = self.store.backup_file(current['target'], identity) if current['mode'] == 'local' else self.store.backup_json(current, 'google', current['target'], identity)
            self.store.copy(backup['id'])
            self.store.log('INFO', 'Резервная копия сохранена и проверена: ' + backup['id'])
            # Recheck after the potentially slow backup, immediately before writing.
            if adapter.snapshot()['fingerprint'] != current['fingerprint']:
                raise AppError('Целевая таблица изменилась во время создания копии. Подготовьте новый план.')
            result = dict(id=identity, created=stamp(), date=approved['date'], status='writing', changes=len(approved['changes']), backup=backup['id'], message='Запись и сверка')
            result_path = self.store.root / 'plans' / (identity + '.result.json')
            atomic_json(result_path, result)
            try:
                self.set_phase(step=2)
                adapter.write(approved)
                self.set_phase(step=3)
                adapter.verify(approved)
                result.update(status='done', message='Обновление завершено, результат проверен.')
                atomic_json(result_path, result)
                with self.lock:
                    self.plan = None  # approved plan is single-use
                self.set_phase(phase='done', step=4, error='', plan_id='')
                self.store.log('INFO', 'Обновление завершено. Записанные значения сверены чтением.')
            except Exception as exc:
                result.update(status='uncertain', message='Запись или сверка не завершилась. Проверьте цель; резервная копия сохранена.')
                atomic_json(result_path, result)
                raise exc
        return self.launch('write', work)

    def create_local_copy(self, source):
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения операции.')
            source = readable(source, {'.xlsx'})
            # Source is read-only. Destination is always a new file in application data.
            path = self.store.root / 'output' / ('Рабочая_копия_' + uuid.uuid4().hex[:8] + '.xlsx')
            self.store.backup_json({'previously_exists': False}, 'new-file', str(path))
            shutil.copy2(source, path)
            if file_hash(source) != file_hash(path):
                raise AppError('Не удалось проверить созданную копию.')
            return {'path': str(path)}

    def telegram_auth(self, step, **values):
        def work():
            if step == 'code':
                result = self.telegram.call(self.telegram.send_code(values.get('api_id', ''), values.get('api_hash', ''), values.get('phone', '')))
            elif step == 'sign_in':
                result = self.telegram.call(self.telegram.sign_in(values.get('code', ''), values.get('password', '')))
            else:
                raise AppError('Неизвестный шаг подключения Telegram.')
            self.set_phase(phase='idle', telegram_auth=result)
            self.store.log('INFO', 'Шаг подключения Telegram завершён. Секреты в журнал не записаны.')
        return self.launch('telegram', work)

    def close(self):
        self.cancelled.set()
        if self.worker:
            self.worker.join(timeout=50)
        self.telegram.close()
        if getattr(self, 'root_lock', None):
            self.root_lock.close()
