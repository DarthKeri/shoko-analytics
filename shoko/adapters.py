import copy
import csv
import gzip
import json
import math
import os
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from .calculation import PROSTOI, DRIVERS, TARGET, GARAGE
from .storage import AppError, digest, file_hash

SHEETS = [PROSTOI, DRIVERS, TARGET, GARAGE]
LIVE_ID = '1HzoNqo_7x1RE_4ADegdW46r7qKhUl9xrKi6j1DXEiTQ'
PROTECTED_ARCHIVE = Path(r'C:\Users\krivov\Desktop\shoko\ScriptExcel')
MAX_CELLS = 2_000_000
MAX_GOOGLE_WRITE_BYTES = 12_000_000
GOOGLE_WRITE_TIMEOUT = (45, 180)


def google_exception_chain(exc):
    pending, seen = [exc], set()
    while pending and len(seen) < 20:
        value = pending.pop(0)
        if not isinstance(value, BaseException) or id(value) in seen:
            continue
        seen.add(id(value))
        yield value
        pending.extend([value.__cause__, value.__context__, getattr(value, 'reason', None)])
        pending.extend(arg for arg in value.args if isinstance(arg, BaseException))


def google_transport_code(exc):
    # Exception text may contain request bodies, URLs or credentials. Only emit
    # bounded Python type names and integer OS error codes, never repr/str(exc).
    parts = []
    for value in google_exception_chain(exc):
        name = type(value).__name__
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,63}', name):
            name = 'Error'
        codes = [f'{key}={getattr(value, key)}' for key in ('errno', 'winerror')
                 if type(getattr(value, key, None)) is int]
        part = name + ('(' + ','.join(codes) + ')' if codes else '')
        if part not in parts:
            parts.append(part)
    return ' / '.join(parts)


def google_http_request(session, method, url, timeout=(10, 45), report=lambda text: None, **kwargs):
    """Retry only transient GET failures. Never replay any mutating request."""
    from requests.exceptions import ConnectionError, Timeout, SSLError
    attempts = 3 if method.upper() == 'GET' else 1
    for attempt in range(attempts):
        try:
            return session.request(method, url, timeout=timeout, **kwargs)
        except Exception as exc:
            chain = list(google_exception_chain(exc))
            transient = (any(isinstance(item, (ConnectionError, Timeout, TimeoutError)) for item in chain)
                         and not any(isinstance(item, SSLError) for item in chain))
            if attempt + 1 == attempts or not transient:
                raise
            delay = 2 ** attempt
            report(f'Google: временная ошибка чтения. Повтор {attempt + 2} из {attempts} через {delay} с. '
                   f'Код: {google_transport_code(exc)}.')
            time.sleep(delay)


def scalar(value):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise AppError('В таблице найдено бесконечное или недопустимое число.')
    return value


def readable(path, suffix):
    if not path:
        raise AppError('Путь к файлу не указан. Выберите файл в настройках.')
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise AppError(f'Файл не найден: {path.name}. Выберите его расположение в настройках.')
    if path.suffix.lower() not in suffix:
        raise AppError(f'Неверный тип файла: {path.name}. Ожидается {", ".join(suffix)}.')
    try:
        with path.open('rb') as stream:
            stream.read(1)
    except PermissionError:
        raise AppError(f'Нет доступа к файлу {path.name}. Проверьте права и подключение к папке.') from None
    return path


def read_excel(path, sheet, row_limit=100_000, col_limit=500):
    path = readable(path, {'.xlsx'})
    try:
        with path.open('rb') as stream:
            values = load_workbook(stream, read_only=True, data_only=True)
            if sheet not in values.sheetnames:
                values.close()
                raise AppError(f'{path.name}: не найден лист «{sheet}».')
            ws = values[sheet]
            if row_limit == 100_000 and (ws.max_row > row_limit or ws.max_column > col_limit):
                values.close()
                raise AppError('Начисления превышают предел MVP: 100 000 строк / 50 столбцов.')
            rows, cols = min(ws.max_row, row_limit), min(ws.max_column, col_limit)
            if rows * cols > MAX_CELLS:
                values.close()
                raise AppError('Лист слишком большой для MVP (более 2 млн ячеек).')
            matrix = [[scalar(v) for v in row] for row in ws.iter_rows(max_row=rows, max_col=cols, values_only=True)]
            values.close()
        # A formula without an Excel cached result must never silently become zero.
        with path.open('rb') as stream:
            formulas = load_workbook(stream, read_only=True, data_only=False)
            for r, row in enumerate(formulas[sheet].iter_rows(max_row=rows, max_col=cols)):
                for c, value in enumerate(row):
                    if value.data_type == 'f' and matrix[r][c] == '':
                        matrix[r][c] = '#Нет сохранённого результата формулы'
            formulas.close()
        return matrix
    except AppError:
        raise
    except PermissionError:
        raise AppError(f'Нет доступа к {path.name}.') from None
    except Exception:
        raise AppError(f'Не удалось прочитать {path.name}. Проверьте формат XLSX и сохраните файл в Excel.') from None


def read_csv(path):
    path = readable(path, {'.csv'})
    if path.stat().st_size > 30 * 1024 * 1024:
        raise AppError('CSV превышает 30 МБ.')
    try:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream, delimiter=';', strict=True))
    except (UnicodeError, csv.Error):
        raise AppError('CSV должен быть в UTF-8 с разделителем «;». Перевыгрузите файл из бота.') from None
    if len(rows) < 2 or len(rows[0]) < 2 or not any(rows[0]):
        raise AppError('CSV пуст или не содержит заголовков. Требуется файл выгрузки бота.')
    width = len(rows[0])
    if len(rows) * width > MAX_CELLS:
        raise AppError('CSV слишком большой для MVP.')
    if len(set(rows[0])) != width or any(not h.strip() for h in rows[0]):
        raise AppError('В CSV пустые или повторяющиеся заголовки.')
    if any(len(row) != width for row in rows):
        raise AppError('В CSV строки разной длины. Проверьте разделитель и формат выгрузки.')
    return rows


def spreadsheet_id(value):
    match = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]+)', value or '')
    result = match.group(1) if match else (value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,}', result):
        raise AppError('Укажите ссылку или ID тестовой Google-таблицы.')
    return result


def target_key(config):
    return spreadsheet_id(config['target']) if config['target_mode'] == 'google' else str(Path(config['target']).expanduser().resolve())


def guard_target(config):
    if not config.get('target'):
        raise AppError('Не выбрана копия целевой таблицы.')
    key = target_key(config)
    if key == LIVE_ID:
        raise AppError('Это боевая legacy-таблица. Запись в неё запрещена.')
    if key not in config.get('allowed_targets', []):
        raise AppError('Цель не разрешена для тестов. В настройках подтвердите, что это копия.')
    if config['target_mode'] == 'local':
        path = Path(key)
        if path == PROTECTED_ARCHIVE or PROTECTED_ARCHIVE in path.parents:
            raise AppError('Исходный архив ScriptExcel защищён. Создайте копию в рабочей папке.')
        for source in ('prostoi', 'drivers', 'csv'):
            if config.get(source) and path == Path(config[source]).resolve():
                raise AppError('Целевая книга совпадает с исходным файлом. Выберите отдельную копию.')
    return key


def equivalent(first, second):
    if first is None:
        first = ''
    if second is None:
        second = ''
    # Google and Excel may return 0 as either int or float. They are the same
    # spreadsheet number; bool and text must remain distinct from numbers.
    if type(first) in (int, float) and type(second) in (int, float):
        return first == second
    return type(first) == type(second) and first == second


def changes_for(before, after):
    changes = []
    for name, new_rows in after.items():
        old = before['sheets'].get(name, {}).get('values', [])
        raw = before['sheets'].get(name, {}).get('raw', old)
        for r in range(max(len(old), len(new_rows))):
            old_row = old[r] if r < len(old) else []
            new_row = new_rows[r] if r < len(new_rows) else []
            for c in range(max(len(old_row), len(new_row))):
                value = new_row[c] if c < len(new_row) else ''
                prior = old_row[c] if c < len(old_row) else ''
                if not equivalent(prior, value):
                    original = raw[r][c] if r < len(raw) and c < len(raw[r]) else prior
                    changes.append(dict(sheet=name, row=r + 1, col=c + 1, cell=f'{get_column_letter(c + 1)}{r + 1}', before=original, after=value))
    if len(changes) > 500_000:
        raise AppError('Слишком много изменений для MVP. Выберите меньшую таблицу.')
    return changes


def merged_follower(merge, row, col):
    """Coordinates are zero-based; the top-left cell retains its value."""
    top, left = merge.get('startRowIndex', 0), merge.get('startColumnIndex', 0)
    return (top <= row < merge['endRowIndex'] and left <= col < merge['endColumnIndex']
            and (row, col) != (top, left))


def normalize_merged_output(before, after):
    """Remove only empty/zero placeholders before review, never meaningful data.

    Google stores a merged range's value at its top-left cell. The legacy
    rectangular arrays can contain synthetic zeros at the other coordinates.
    Preserve the layout and warn explicitly; block any meaningful conflict.
    """
    normalized, warnings = dict(after), []
    for name, rows in after.items():
        merges = before['sheets'].get(name, {}).get('merges', [])
        if not merges:
            continue
        count, copied = 0, set()
        for merge in merges:
            for r in range(merge.get('startRowIndex', 0), min(merge['endRowIndex'], len(rows))):
                for c in range(merge.get('startColumnIndex', 0), min(merge['endColumnIndex'], len(rows[r]))):
                    if not merged_follower(merge, r, c) or rows[r][c] == '':
                        continue
                    value = rows[r][c]
                    zero = (type(value) in (int, float) and value == 0) or (type(value) is str and value in ('0', '0.0%'))
                    if not zero:
                        address = f'{get_column_letter(c + 1)}{r + 1}'
                        raise AppError(f'Лист «{name}», {address}: непустые данные попадают внутрь объединённой области. '
                                       'Проверьте структуру тестовой таблицы и исходные файлы. Запись не начата.')
                    if normalized[name] is rows:
                        normalized[name] = list(rows)
                    if r not in copied:
                        normalized[name][r] = list(rows[r])
                        copied.add(r)
                    normalized[name][r][c] = ''
                    count += 1
        if count:
            warnings.append(f'«{name}»: {count} служебных нулей внутри объединённых ячеек исключены из плана; '
                            'значения верхних левых ячеек и объединения сохранены.')
    return normalized, warnings


class LocalTarget:
    def __init__(self, config):
        self.path = readable(guard_target(config), {'.xlsx'})

    def snapshot(self):
        before_hash = file_hash(self.path)
        values = load_workbook(self.path, data_only=True)
        raw = load_workbook(self.path, data_only=False)
        try:
            sheets = {}
            for name in SHEETS:
                if name not in values.sheetnames:
                    continue
                ws = values[name]
                if ws.max_row * ws.max_column > MAX_CELLS:
                    raise AppError(f'Лист «{name}» превышает лимит MVP.')
                sheets[name] = dict(values=[[scalar(v) for v in row] for row in ws.values],
                                    raw=[[scalar(v) for v in row] for row in raw[name].values],
                                    rows=ws.max_row, cols=ws.max_column)
            if TARGET not in sheets:
                raise AppError(f'В копии отсутствует лист «{TARGET}».')
        finally:
            values.close()
            raw.close()
        if file_hash(self.path) != before_hash:
            raise AppError('Целевая книга изменилась во время чтения. Повторите обновление.')
        return dict(mode='local', target=str(self.path), fingerprint=before_hash, sheets=sheets)

    def prepare_write(self, plan):
        return None

    def write(self, plan, prepared=None):
        workbook = load_workbook(self.path)
        temp = self.path.with_name('.' + self.path.stem + '.' + uuid.uuid4().hex + '.xlsx')
        try:
            for name in plan['after']:
                if name not in workbook.sheetnames:
                    workbook.create_sheet(name)
            for change in plan['changes']:
                c = workbook[change['sheet']].cell(change['row'], change['col'])
                c.value = change['after'] if change['after'] != '' else None
                if isinstance(change['after'], str) and change['after']:
                    c.data_type = 's'  # spreadsheet formulas from CSV are treated as text
            workbook.save(temp)
            if file_hash(self.path) != plan['before']['fingerprint']:
                raise AppError('Книга изменилась после просмотра. Создайте новый план.')
            os.replace(temp, self.path)
        except PermissionError:
            raise AppError('Не удалось записать книгу. Закройте её в Excel и повторите подготовку.') from None
        finally:
            workbook.close()
            temp.unlink(missing_ok=True)

    def verify(self, plan):
        workbook = load_workbook(self.path, data_only=False)
        try:
            for change in plan['changes']:
                value = scalar(workbook[change['sheet']].cell(change['row'], change['col']).value)
                if value != change['after']:
                    raise AppError(f'Ошибка сверки: {change["sheet"]}, {change["cell"]}. Копия сохранена.')
        finally:
            workbook.close()


def clean_fingerprint(value):
    if isinstance(value, dict):
        return {k: clean_fingerprint(v) for k, v in value.items() if k not in ('effectiveValue', 'formattedValue', 'effectiveFormat', 'hyperlink')}
    if isinstance(value, list):
        return [clean_fingerprint(v) for v in value]
    return value


def google_value(value):
    if value == '':
        return {}
    if isinstance(value, bool):
        return {'boolValue': value}
    if isinstance(value, (int, float)):
        return {'numberValue': value}
    return {'stringValue': str(value)}  # RAW text, never USER_ENTERED formula injection


def google_request_error(exc):
    """Classify failures without exposing keys, request JWTs or response payloads."""
    from requests.exceptions import SSLError, Timeout, ConnectionError
    from google.auth.exceptions import RefreshError
    chain = list(google_exception_chain(exc))
    if any(isinstance(item, SSLError) for item in chain):
        return 'Google: не удалось проверить HTTPS-сертификат. Проверьте доверенные сертификаты Windows, антивирус или прокси.'
    if any(isinstance(item, (Timeout, TimeoutError)) for item in chain):
        return 'Google не ответил вовремя. Подключение: до 10 секунд, ожидание данных: до 45 секунд на запрос. Повторите проверку.'
    if any(isinstance(item, ConnectionError) for item in chain):
        return 'Не удалось установить соединение с Google. Проверьте интернет, DNS и прокси.'
    if any(isinstance(item, RefreshError) for item in chain):
        return 'Google не принял авторизацию. Войдите в Google заново или проверьте ключ сервисного аккаунта и дату/время Windows.'
    return 'Не удалось выполнить запрос к Google. Проверьте авторизацию и настройки подключения.'


class GoogleTarget:
    def __init__(self, config, google_auth=None):
        self.id = guard_target(config)
        logger = getattr(getattr(google_auth, 'store', None), 'log', None)
        self.report = (lambda message: logger('WARNING', message)) if logger else (lambda message: None)
        if config.get('google_auth_mode', 'service_account') == 'oauth':
            if google_auth is None:
                raise AppError('Вход Google недоступен. Перезапустите приложение и подключите аккаунт в настройках.')
            self.session = google_auth.session(config)
            self.base = 'https://sheets.googleapis.com/v4/spreadsheets/' + self.id
            return
        path = readable(config.get('credentials'), {'.json'})
        from google.oauth2.service_account import Credentials
        from google.auth.transport.requests import AuthorizedSession
        try:
            credentials = Credentials.from_service_account_file(str(path), scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
            self.session = AuthorizedSession(credentials)
        except Exception:
            raise AppError('credentials.json не является ключом сервисного аккаунта Google.') from None
        self.base = 'https://sheets.googleapis.com/v4/spreadsheets/' + self.id

    def request(self, method, suffix='', **kwargs):
        timeout = kwargs.pop('timeout', (10, 45))
        try:
            response = google_http_request(self.session, method, self.base + suffix, timeout=timeout,
                                           report=getattr(self, 'report', lambda text: None), **kwargs)
        except Exception as exc:
            message = google_request_error(exc)
            if method == 'POST' and suffix == ':batchUpdate':
                message = ('Google не подтвердил результат записи. Соединение или передача: '
                           f'до {timeout[0]} секунд, ожидание ответа: до {timeout[1]} секунд. '
                           'Данные могли быть записаны. Проверьте таблицу и резервную копию; '
                           'автоматический повтор не выполняется.')
            message += ' Код: ' + google_transport_code(exc) + '.'
            raise AppError(message) from None
        if response.status_code >= 400:
            from .google_errors import http_error
            raise AppError(http_error(response))
        return response.json()

    def check(self):
        # Status only verifies access and the required sheet, without downloading cells.
        meta = self.request('GET', params={'includeGridData': 'false', 'fields': 'spreadsheetId,sheets(properties,merges)'})
        titles = {s['properties']['title'] for s in meta.get('sheets', [])}
        if TARGET not in titles:
            raise AppError(f'В Google-таблице нет листа «{TARGET}».')
        return meta

    def snapshot(self):
        meta = self.check()
        titles = {s['properties']['title'] for s in meta.get('sheets', [])}
        names = [name for name in SHEETS if name in titles]
        merges = {s['properties']['title']: s.get('merges', []) for s in meta.get('sheets', [])}
        # Cell formatting can dwarf the actual values by gigabytes. Read only the
        # values needed for calculation and formulas needed for exact comparison.
        fields = 'sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)),data(startRow,startColumn,rowData(values(userEnteredValue,effectiveValue))))'
        data = self.request('GET', params={'ranges': ["'" + name + "'" for name in names], 'fields': fields})
        sheets = {}
        count = 0
        for sheet in data.get('sheets', []):
            values, raw = [], []
            for block in sheet.get('data', []):
                for r, row in enumerate(block.get('rowData', []), block.get('startRow', 0)):
                    while len(values) <= r:
                        values.append([])
                        raw.append([])
                    for c, item in enumerate(row.get('values', []), block.get('startColumn', 0)):
                        count += 1
                        if count > MAX_CELLS:
                            raise AppError('Google-таблица слишком большая для MVP.')
                        entered = item.get('userEnteredValue', {})
                        effective = item.get('effectiveValue', {})
                        if 'errorValue' in effective:
                            val = '#Ошибка формулы'
                        else:
                            val = next(iter(effective.values()), next(iter(entered.values()), ''))
                        for matrix, value in ((values, val), (raw, next(iter(entered.values()), ''))):
                            matrix[r].extend([''] * max(0, c + 1 - len(matrix[r])))
                            matrix[r][c] = value
            props = sheet['properties']
            for matrix in (values, raw):
                for row in matrix:
                    while row and row[-1] == '':
                        row.pop()
                while matrix and not matrix[-1]:
                    matrix.pop()
            sheets[props['title']] = dict(values=values, raw=raw, rows=props['gridProperties']['rowCount'], cols=props['gridProperties']['columnCount'], id=props['sheetId'], merges=merges.get(props['title'], []))
        # Keep structure of ALL tabs for collision-free IDs, but no duplicate grid.
        fingerprint = digest({'metadata': meta, 'sheets': {name: {k: v for k, v in item.items() if k != 'values'} for name, item in sheets.items()}})
        return dict(mode='google', target=self.id, fingerprint=fingerprint, sheets=sheets, metadata=meta)

    def prepare_write(self, plan):
        requests = []
        sheet_ids = {name: item['id'] for name, item in plan['before']['sheets'].items()}
        used = set(sheet_ids.values()) | {s['properties']['sheetId'] for s in plan['before'].get('metadata', {}).get('sheets', [])}
        for name, rows in plan['after'].items():
            needed_rows = max(1, len(rows))
            needed_cols = max(1, max((len(row) for row in rows), default=1))
            if name not in sheet_ids:
                identity = max(used or {0}) + 1
                used.add(identity)
                sheet_ids[name] = identity
                requests.append({'addSheet': {'properties': {'sheetId': identity, 'title': name, 'gridProperties': {'rowCount': needed_rows, 'columnCount': needed_cols}}}})
            else:
                old = plan['before']['sheets'][name]
                if needed_rows > old['rows'] or needed_cols > old['cols']:
                    requests.append({'updateSheetProperties': {'properties': {'sheetId': old['id'], 'gridProperties': {'rowCount': max(old['rows'], needed_rows), 'columnCount': max(old['cols'], needed_cols)}}, 'fields': 'gridProperties.rowCount,gridProperties.columnCount'}})
        # Exact rectangles of adjacent changed cells. Never fill holes with a
        # neighbouring value: untouched cells and their formulas must survive.
        grouped = {}
        for change in plan['changes']:
            grouped.setdefault((change['sheet'], change['row']), []).append(change)
        blocks = {}
        for (name, row), changes in sorted(grouped.items()):
            # Never silently filter an already-reviewed plan at write time.
            merges = [m for m in plan['before']['sheets'].get(name, {}).get('merges', [])
                      if m.get('startRowIndex', 0) <= row - 1 < m['endRowIndex']]
            if any(merged_follower(m, row - 1, change['col'] - 1) for m in merges for change in changes):
                raise AppError(f'Лист «{name}»: план содержит запись внутрь объединённой ячейки. '
                               'Подготовьте новый план. Запись не начата.')
            changes.sort(key=lambda x: x['col'])
            groups = []
            for item in changes:
                if not groups or item['col'] != groups[-1][-1]['col'] + 1:
                    groups.append([])
                groups[-1].append(item)
            for group in groups:
                key = (name, group[0]['col'], len(group))
                values = {'values': [{'userEnteredValue': google_value(c['after'])} for c in group]}
                previous = blocks.get(key)
                if previous and previous['end'] == row - 1:
                    previous['request']['updateCells']['rows'].append(values)
                    previous['end'] = row
                else:
                    request = {'updateCells': {'start': {'sheetId': sheet_ids[name], 'rowIndex': row - 1, 'columnIndex': group[0]['col'] - 1}, 'rows': [values], 'fields': 'userEnteredValue'}}
                    requests.append(request)
                    blocks[key] = {'end': row, 'request': request}
        body = {'requests': requests, 'includeSpreadsheetInResponse': False}
        payload = json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(payload) > MAX_GOOGLE_WRITE_BYTES:
            raise AppError(f'Объём изменений {len(payload) / 1_000_000:.2f} МБ превышает предел одной атомарной записи (12 МБ). Запись не начата; уменьшите набор данных.')
        return payload if requests else None

    def write(self, plan, prepared=None):
        payload = prepared if prepared is not None else self.prepare_write(plan)
        if payload:
            # Same atomic batch and exact reviewed cells. Gzip reduces network
            # upload volume; this is not a second request or a partial write.
            headers = {'Content-Type': 'application/json; charset=utf-8'}
            body = payload
            if len(payload) >= 64_000:
                compressed = gzip.compress(payload, compresslevel=6, mtime=0)
                if len(compressed) < len(payload):
                    body = compressed
                    headers['Content-Encoding'] = 'gzip'
            report = getattr(self, 'report', lambda text: None)
            report(f'Отправляем {len(plan["changes"])} подтверждённых изменений: '
                   f'{len(body) / 1_000_000:.2f} МБ по сети из {len(payload) / 1_000_000:.2f} МБ JSON. '
                   'Ожидание ответа Google — до 180 секунд.')
            self.request('POST', ':batchUpdate', data=body, headers=headers, timeout=GOOGLE_WRITE_TIMEOUT)

    def verify(self, plan):
        result = self.snapshot()
        for name in plan['after']:
            if name not in result['sheets']:
                raise AppError(f'Ошибка сверки Google: отсутствует лист «{name}».')
            expected = plan['before']['sheets'].get(name, {}).get('merges', [])
            if result['sheets'][name].get('merges', []) != expected:
                raise AppError(f'Ошибка сверки Google: изменились объединения ячеек листа «{name}».')
        for change in plan['changes']:
            rows = result['sheets'][change['sheet']]['raw']
            value = rows[change['row'] - 1][change['col'] - 1] if change['row'] <= len(rows) and change['col'] <= len(rows[change['row'] - 1]) else ''
            if value != change['after']:
                raise AppError(f'Ошибка сверки Google: {change["sheet"]}, {change["cell"]}.')


def target(config, google_auth=None):
    return GoogleTarget(config, google_auth) if config['target_mode'] == 'google' else LocalTarget(config)
