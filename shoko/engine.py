import copy
import json
import os
import shutil
import threading
import uuid
from datetime import date, timedelta
from pathlib import Path
from .storage import AppError, Store, atomic_json, digest, file_hash, stamp
from .adapters import readable, read_excel, read_driver_excel, read_csv, target, guard_target, target_key, changes_for, normalize_merged_output, PROTECTED_ARCHIVE
from .calculation import assemble_prostoi, calculate, PROSTOI, DRIVERS, TARGET, GARAGE
from .drive_backup import DriveBackup
from .google_auth import GoogleAuth, client_config
from .plans import save_plan, load_plan
from .sheet_backups import planned_backups
from .garage import prepare_garage, comparison_before, formula_text
from .validation import read_prostoi_source, read_bot_source, check_bot_source
from .activity import ActivityLedger

STEPS = ['Проверка файлов и доступа', 'Чтение исходных данных', 'Проверка CSV', 'Подготовка изменений']
CONFIG_KEYS = {'prostoi', 'drivers', 'csv', 'target_mode', 'target', 'credentials', 'google_backup_folder', 'google_auth_mode', 'google_oauth_client', 'developer', 'appearance', 'last_date'}


def resolved(value, processing_date):
    try:
        return value.format(year=processing_date.year, yy=str(processing_date.year)[-2:], month=f'{processing_date.month:02d}')
    except (KeyError, ValueError, IndexError):
        raise AppError('Неверный шаблон пути. Допустимы {year}, {yy} и {month}.') from None


class Engine:
    def __init__(self, root, record_launch=True):
        self.store = Store(root)
        self.activity = ActivityLedger(self.store.root)
        self.record_launch = record_launch
        if record_launch and not self.activity.record_launch():
            self.store.log('WARNING', self.activity.error)
        self.google = GoogleAuth(self.store)
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.state = dict(phase='idle', step=-1, started='', error='', plan_id='', operation='')
        self.plan = None
        self.checks = {}
        self.developer_results = {}
        self.worker = None
        if record_launch:
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

    def plan_config_digest(self):
        # Display preferences do not change the reviewed data or invalidate approval.
        return digest({k: v for k, v in self.config().items() if k != 'chart_type'})

    def set_chart_type(self, value):
        if value not in ('line', 'bars'):
            raise AppError('Выберите вид графика: Линии или Столбцы.')
        with self.lock:
            self.store.save_config({'chart_type': value})
        return {'chart_type': value}

    def busy(self):
        return self.state['phase'] in ('preparing', 'writing', 'checking', 'connecting')

    def reset_settings(self, confirmed=False):
        with self.lock:
            if confirmed is not True:
                raise AppError('Подтвердите удаление всех настроек.')
            if self.busy() or (self.worker and self.worker.is_alive()):
                raise AppError('Дождитесь завершения текущей операции перед удалением настроек.')
            # Invalidate approval even if filesystem permissions prevent a complete reset.
            self.plan = None
            self.checks = {}
            self.developer_results = {}
            self.state = dict(phase='idle', step=-1, started='', error='', plan_id='', operation='')
            try:
                self.google.disconnect()
            except OSError:
                raise AppError('Не удалось удалить сохранённый вход Google. Проверьте доступ к рабочей папке и повторите сброс. Настройки не удалены.') from None
            try:
                self.store.reset_config()
            except OSError:
                raise AppError('Вход Google отключён, но не удалось сбросить настройки. Проверьте доступ к settings.local.json и повторите сброс.') from None
            try:
                self.store.log('INFO', 'Все настройки сброшены, сохранённый вход Google удалён. Таблицы, резервные копии и журналы сохранены.')
            except OSError:
                pass  # A read-only journal must not disguise a completed reset as a failure.
            return self.public_state()

    def save_settings(self, values):
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения операции перед изменением настроек.')
            previous = self.config()
            candidate = dict(previous, **{k: v for k, v in values.items() if k in CONFIG_KEYS})
            if candidate['target_mode'] not in ('local', 'google'):
                raise AppError('Неверный тип подключения.')
            if candidate['google_auth_mode'] not in ('oauth', 'service_account'):
                raise AppError('Неверный способ входа Google.')
            if candidate['appearance'] not in ('classic', 'apple'):
                raise AppError('Неизвестный вид интерфейса. Выберите Стандартный или Apple HIG.')
            if 'test_copy_confirmed' in values:
                candidate['allowed_targets'] = [target_key(candidate)] if values['test_copy_confirmed'] and candidate['target'] else []
            # Saving incomplete first-run settings is allowed. Writes require guard_target.
            if candidate['target'] and candidate['allowed_targets']:
                guard_target(candidate)
            candidate['initialized'] = True
            if candidate == previous:
                return self.public_state()
            self.store.save_config(candidate)
            self.plan = None
            self.state.update(phase='idle', plan_id='', error='')
            dependencies = {'prostoi': ('prostoi', 'last_date'), 'drivers': ('drivers', 'last_date'),
                            'target': ('target', 'target_mode', 'credentials', 'allowed_targets'),
                            'credentials': ('credentials',), 'csv': ('csv',)}
            dependencies['csv'] += ('target', 'target_mode', 'credentials', 'allowed_targets', 'google_auth_mode', 'google_oauth_client')
            dependencies['backup'] = ('google_backup_folder', 'target', 'credentials', 'target_mode', 'allowed_targets')
            for key in ('credentials', 'target', 'backup'):
                dependencies[key] += ('google_auth_mode', 'google_oauth_client')
            self.checks = {k: v for k, v in self.checks.items() if not any(previous.get(key) != candidate.get(key) for key in dependencies[k])}
            self.store.log('INFO', 'Настройки сохранены в settings.local.json.')
            return self.public_state()

    def finish_setup(self):
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения проверки.')
            config = self.config()
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
                plan['operations'] = self.plan.get('operations', [])
                plan['garage'] = self.plan.get('garage')
                plan['sheet_backups'] = self.plan.get('sheet_backups', [])
            try:
                target_allowed = target_key(self.config()) in self.config()['allowed_targets']
            except AppError:
                target_allowed = False
            return dict(target_allowed=target_allowed, config=self.config(), root=str(self.store.root), state=copy.deepcopy(self.state), checks=copy.deepcopy(self.checks), plan=plan, activity=self.activity.summary(histories),
                        history=sorted(histories, key=lambda h: h['created'], reverse=True)[:100], copies=self.store.copies()[:100], logs=self.store.logs(), developer=copy.deepcopy(self.developer_results), google_errors=self.store.google_errors(), google_auth=self.google.status(self.config()))

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
            if kind != 'changes':
                data = [[formula_text(value) for value in row] for row in data]
            return {'data': data[offset:offset + 100], 'total': len(data), 'offset': offset}

    def set_phase(self, **values):
        with self.lock:
            if 'step' in values and values['step'] != self.state.get('step'):
                values.setdefault('detail', '')
            self.state.update(values)

    def launch(self, operation, function):
        with self.lock:
            if self.busy():
                raise AppError('Уже выполняется операция. Дождитесь её завершения.')
            self.cancelled.clear()
            phase = {'prepare': 'preparing', 'write': 'writing', 'check': 'checking', 'diagnostics': 'checking', 'google': 'connecting'}[operation]
            self.state.update(phase=phase, operation=operation, started=stamp(), error='', step=-1, detail='', current_source='')
            def work():
                try:
                    function()
                except Exception as exc:
                    message = str(exc) if isinstance(exc, AppError) else 'Операция не завершена. Проверьте файлы и настройки подключения.'
                    if self.cancelled.is_set() and operation == 'prepare':
                        self.set_phase(phase='cancelled', error='Подготовка отменена.')
                        self.store.log('INFO', 'Подготовка отменена. Запись не выполнялась.')
                    elif self.cancelled.is_set() and operation == 'google':
                        self.set_phase(phase='idle', error='', detail='Вход Google отменён.')
                        self.store.log('INFO', 'Вход Google отменён. Таблица не изменялась.')
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
            return read_prostoi_source(config['prostoi'], processing_date)[2]
        elif name == 'drivers':
            source_date = processing_date - timedelta(days=1)
            read_driver_excel(config['drivers'], source_date)
            return (f'Начисления за {source_date:%d.%m.%Y}: лист «{source_date.day:02d}», '
                    'обязательные колонки и данные проверены.')
        elif name == 'credentials':
            if config.get('google_auth_mode') == 'oauth':
                client_config(config.get('google_oauth_client', ''))
                return
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
                target(config, self.google).check()
            else:
                target(config, self.google).snapshot()
        elif name == 'backup':
            if config['target_mode'] != 'google':
                raise AppError('Для локального Excel копия создаётся в рабочей папке приложения.')
            DriveBackup(target(config, self.google), config).check()
        elif name == 'csv':
            # Validate the local export before opening any Google connection.
            rows = read_bot_source(config['csv'])
            return check_bot_source(config['csv'], target(config, self.google), rows)
        else:
            raise AppError('Неизвестный источник.')

    def check(self, name='all', processing_date=''):
        processing = date.fromisoformat(processing_date or date.today().isoformat())
        config = self.inputs(self.config(), processing)
        names = ['prostoi', 'drivers', 'target', 'csv'] if name == 'all' else [name]
        if name == 'all' and config['target_mode'] == 'google':
            names.append('backup')
        def work():
            errors = []
            for item in names:
                self.set_phase(current_source=item)
                try:
                    detail = self.check_one(item, config, processing)
                    message = detail if isinstance(detail, str) and detail else 'Доступен'
                    value = {'ok': True, 'message': message, 'checked': stamp(), 'date': processing.isoformat()}
                    self.store.log('INFO', f'Проверка источника {item}: {message} Запись не выполнялась.')
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
        required = [('prostoi', 'Средние цены и простои'), ('drivers', 'Начисления'), ('target', 'Целевая копия')]
        if config['target_mode'] == 'google':
            if config['google_auth_mode'] == 'service_account':
                required += [('credentials', 'Ключ Google')]
            required += [('google_backup_folder', 'Папка копий Google Drive')]
        required += [('csv', 'CSV Гаража')]
        missing = [label for key, label in required if not config[key].strip()]
        if missing:
            raise AppError('Заполните настройки: ' + ', '.join(missing) + '. Обновление не запущено.')
        if config['target_mode'] == 'google' and config['google_auth_mode'] == 'oauth':
            client_config(config.get('google_oauth_client', ''))
        url = ''
        guard_target(config)
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения операции.')
            latest = self.config()
            latest.update(last_date=processing_date)
            if latest != self.config():
                self.store.save_config(latest)
        def work():
            self.plan = None
            self.set_phase(plan_id='', step=0)
            identity = uuid.uuid4().hex
            for key in ('prostoi', 'drivers'):
                readable(config[key], {'.xlsx'})
            adapter = target(config, self.google)
            if config['target_mode'] == 'google':
                DriveBackup(adapter, config).check()
            self.check_cancel()
            source_hashes = {key: file_hash(config[key]) for key in ('prostoi', 'drivers')}
            self.set_phase(step=1)
            current, previous, _ = read_prostoi_source(config['prostoi'], processing)
            drivers = read_driver_excel(config['drivers'], processing - timedelta(days=1))
            self.check_cancel()
            self.set_phase(step=2)
            csv_path = readable(config['csv'], {'.csv'})
            source_hashes['csv'] = file_hash(csv_path)
            garage = read_bot_source(csv_path)
            self.check_cancel()
            self.set_phase(step=3)
            before = adapter.snapshot()
            prostoi = assemble_prostoi(current, previous, before['sheets'].get(PROSTOI, {}).get('values', []), processing)
            prices, warnings = calculate(prostoi, drivers, before['sheets'][TARGET]['values'], processing)
            after = {PROSTOI: prostoi, DRIVERS: drivers, TARGET: prices, GARAGE: garage}
            garage_plan = prepare_garage(before, after, garage, processing)
            if garage_plan['garage']['archived_errors']:
                warnings.append(f'В старом Гараже {garage_plan["garage"]["archived_errors"]} ошибок формул: их вид сохранится текстом в архиве. Исходные формулы останутся в полной резервной копии.')
            baseline = comparison_before(dict(before=before, **garage_plan))
            after, merge_warnings = normalize_merged_output(baseline, after)
            warnings.extend(merge_warnings)
            changes = changes_for(baseline, after)
            self.check_cancel()
            sources = {key: {'path': config[key], 'sha256': hash_value} for key, hash_value in source_hashes.items()}
            for item in sources.values():
                if file_hash(item['path']) != item['sha256']:
                    raise AppError('Исходный файл изменился во время подготовки. Повторите обновление.')
            plan = dict(id=identity, date=processing_date, url=url, created=stamp(), rule='primary-legacy-v1+garage-v1',
                        before=before, after=after, changes=changes, sources=sources, config_digest=self.plan_config_digest(), warnings=warnings, **garage_plan)
            plan['sheet_backups'] = planned_backups(before, changes, garage_plan['operations'], derived=garage_plan.get('derived', []))
            if config['target_mode'] == 'google' and config['google_auth_mode'] == 'oauth':
                plan['google_identity'] = self.google.identity(config)
            adapter.prepare_write(plan)  # Validate size before preview, approval or backup.
            plan['checksum'] = digest(plan)
            saved_path = save_plan(self.store.root, plan)
            self.check_cancel()
            with self.lock:
                self.plan = plan
            self.set_phase(phase='preview', plan_id=identity, error='', step=4)
            self.store.log('INFO', f'Подготовлен план: {len(changes)} изменений. Запись ожидает подтверждения.')
            self.store.log('INFO', f'Размер сохранённого плана: {saved_path.stat().st_size / 1_000_000:.2f} МБ.')
            info = garage_plan['garage']
            self.store.log('INFO', f'Гараж: {info["matched"]} строк с предложениями, {info["cleared"]} без предложений, {info["ignored"]} исключений. Запись ожидает подтверждения.')
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
            if approved.get('garage') and not approved['changes'] and not approved.get('operations'):
                raise AppError('Изменений нет. Запись и новая резервная копия не нужны.')
        def work():
            guard_target(self.config())
            if self.plan_config_digest() != approved['config_digest']:
                raise AppError('Настройки изменились после просмотра. Подготовьте новый план.')
            if self.config()['target_mode'] == 'google' and self.config()['google_auth_mode'] == 'oauth' and approved.get('google_identity') != self.google.identity(self.config()):
                raise AppError('Google-аккаунт изменился после просмотра. Подготовьте новый план.')
            saved = load_plan(self.store.root, identity)
            content = dict(saved)
            expected = content.pop('checksum')
            if digest(content) != expected or expected != checksum:
                raise AppError('Сохранённый план повреждён. Запись заблокирована.')
            for item in approved['sources'].values():
                if file_hash(item['path']) != item['sha256']:
                    raise AppError('Исходный файл изменился после просмотра. Подготовьте новый план.')
            self.set_phase(step=0)
            adapter = target(self.config(), self.google)
            current = adapter.snapshot()
            if current['fingerprint'] != approved['before']['fingerprint']:
                raise AppError('Целевая таблица изменилась после просмотра. Подготовьте новый план.')
            prepared = adapter.prepare_write(approved)  # Never create a backup for an unsendable plan.
            self.set_phase(step=1)
            cloud = DriveBackup(adapter, self.config(), lambda text: self.set_phase(detail=text)) if current['mode'] == 'google' else None
            backup = cloud.create(self.store, current, identity) if cloud else self.store.backup_file(current['target'], identity)
            self.store.copy(backup['id'])
            self.store.log('INFO', 'Резервная копия сохранена и проверена: ' + backup['id'])
            # Recheck after the potentially slow backup, immediately before writing.
            self.store.log('INFO', 'После копирования повторно проверяем актуальность таблицы. Запись ещё не начата.')
            if adapter.snapshot()['fingerprint'] != current['fingerprint']:
                raise AppError('Целевая таблица изменилась во время создания копии. Подготовьте новый план.')
            if cloud and cloud.metadata(cloud.source)['version'] != backup['source_version']:
                raise AppError('Google-таблица изменилась после создания копии. Подготовьте новый план.')
            self.set_phase(detail='')
            result = dict(id=identity, created=stamp(), date=approved['date'], status='writing', changes=len(approved['changes']), backup=backup['id'], sheet_backups=approved.get('sheet_backups', []), message='Запись и сверка')
            result_path = self.store.root / 'plans' / (identity + '.result.json')
            atomic_json(result_path, result)
            stage = 'Запись изменений'
            try:
                self.set_phase(step=2)
                def report(message):
                    self.set_phase(detail=message)
                    self.store.log('INFO', message)
                adapter.report = report
                self.store.log('INFO', 'Начинаем запись подтверждённого плана. Резервная копия проверена.')
                adapter.write(approved, prepared=prepared)
                stage = 'Сверка результата'
                self.store.log('INFO', 'Запрос записи завершён. Читаем таблицу для сверки.')
                self.set_phase(step=3, detail='')
                adapter.verify(approved)
                result.update(status='done', completed=stamp(), message='Обновление завершено, результат проверен.')
                atomic_json(result_path, result)
                with self.lock:
                    self.plan = None  # approved plan is single-use
                self.set_phase(phase='done', step=4, error='', plan_id='')
                self.store.log('INFO', f'BF-копии листов созданы и проверены: {len(approved.get("sheet_backups", []))}.')
                self.store.log('INFO', 'Обновление завершено. Записанные значения сверены чтением.')
            except Exception as exc:
                reason = str(exc) if isinstance(exc, AppError) else 'Операция не завершена.'
                message = f'{stage}: {reason} Резервная копия сохранена. Подготовьте новый план после проверки таблицы.'
                result.update(status='uncertain', message=message)
                atomic_json(result_path, result)
                raise AppError(message) from None
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

    def developer_action(self, kind):
        if kind not in ('health', 'space', 'selftest'):
            raise AppError('Неизвестная диагностика.')
        with self.lock:
            if not self.config()['developer']:
                raise AppError('Включите страницу разработчика в настройках.')
            previous = self.state['phase']
            def work():
                from .diagnostics import health, occupied_space
                from .selftest import run_selftest
                self.set_phase(detail={'health': 'Проверяем приложение', 'space': 'Подсчитываем занятое место', 'selftest': 'Проверяем встроенные примеры'}[kind])
                result = health(self.store, self.google) if kind == 'health' else occupied_space(self.store.root) if kind == 'space' else run_selftest()
                with self.lock:
                    self.developer_results[kind] = result
                self.store.log('INFO', {'health': 'Диагностика приложения завершена', 'space': 'Подсчёт места завершён', 'selftest': 'Тест встроенных примеров: ' + ('пройден' if result.get('ok') else 'есть ошибки')}[kind] + '. Пользовательские таблицы не изменялись.')
                self.set_phase(phase='preview' if self.plan and previous == 'preview' else 'idle', detail='', error='')
            return self.launch('diagnostics', work)

    def google_library(self, kind, query='', page_token=''):
        from .google_library import list_items
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения текущей операции.')
            config = self.config()
        return list_items(self.google, config, kind, query, page_token)

    def google_auth_action(self, step):
        with self.lock:
            if self.busy():
                raise AppError('Дождитесь завершения текущей операции.')
            config = self.config()
            if config['google_auth_mode'] != 'oauth':
                raise AppError('Нажмите «Войти через Google» в настройках, чтобы подключить аккаунт.')
            if step not in ('connect', 'disconnect', 'profile'):
                raise AppError('Неизвестное действие входа Google.')
            if step == 'connect':
                client_config(config.get('google_oauth_client', ''))
            self.plan = None
            self.checks = {k: v for k, v in self.checks.items() if k not in ('target', 'credentials', 'backup', 'csv')}
            self.state.update(plan_id='', error='', auth_step=step)
            if step == 'disconnect':
                self.google.disconnect()
                self.set_phase(phase='idle', detail='Вход Google удалён с этого компьютера.')
                self.store.log('INFO', 'Сохранённый вход Google удалён с этого компьютера.')
                return {'disconnected': True}
            if step == 'profile':
                def refresh_profile():
                    self.google.refresh_profile(config)
                    self.set_phase(phase='idle', error='', detail='Аккаунт Google обновлён.')
                return self.launch('google', refresh_profile)
            def work():
                self.google.connect(config['google_oauth_client'], self.cancelled, lambda text: self.set_phase(detail=text))
                self.set_phase(phase='idle', error='', detail='Google-аккаунт подключён.')
                self.store.log('INFO', 'Google-аккаунт подключён. Токены защищены Windows; запись в таблицу не выполнялась.')
            return self.launch('google', work)

    def cancel_google(self):
        with self.lock:
            if self.state['phase'] != 'connecting' or self.state['operation'] != 'google':
                raise AppError('Сейчас нет входа Google, который можно отменить.')
            self.cancelled.set()
            return {'cancelled': True}

    def close(self):
        self.cancelled.set()
        if self.worker:
            self.worker.join(timeout=50)
        if getattr(self, 'root_lock', None):
            self.root_lock.close()
