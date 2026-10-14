import copy
import csv
import math
import os
import re
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

    def write(self, plan):
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
    chain, seen, value = [], set(), exc
    while value is not None and id(value) not in seen:
        seen.add(id(value)); chain.append(value)
        value = value.__cause__ or value.__context__
    if any(isinstance(item, SSLError) for item in chain):
        return 'Google: не удалось проверить HTTPS-сертификат. Проверьте доверенные сертификаты Windows, антивирус или прокси.'
    if any(isinstance(item, Timeout) for item in chain):
        return 'Google не ответил вовремя. Подключение: до 10 секунд, ожидание данных: до 45 секунд на запрос. Повторите проверку.'
    if any(isinstance(item, ConnectionError) for item in chain):
        return 'Не удалось установить соединение с Google. Проверьте интернет, DNS и прокси.'
    if any(isinstance(item, RefreshError) for item in chain):
        return 'Google не принял авторизацию. Войдите в Google заново или проверьте ключ сервисного аккаунта и дату/время Windows.'
    return 'Не удалось выполнить запрос к Google. Проверьте авторизацию и настройки подключения.'


class GoogleTarget:
    def __init__(self, config, google_auth=None):
        self.id = guard_target(config)
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
        try:
            response = self.session.request(method, self.base + suffix, timeout=(10, 45), **kwargs)
        except Exception as exc:
            raise AppError(google_request_error(exc)) from None
        if response.status_code >= 400:
            from .google_errors import http_error
            raise AppError(http_error(response))
        return response.json()

    def check(self):
        # Status only verifies access and the required sheet, without downloading cells.
        meta = self.request('GET', params={'includeGridData': 'false', 'fields': 'spreadsheetId,sheets(properties)'})
        titles = {s['properties']['title'] for s in meta.get('sheets', [])}
        if TARGET not in titles:
            raise AppError(f'В Google-таблице нет листа «{TARGET}».')
        return meta

    def snapshot(self):
        meta = self.check()
        titles = {s['properties']['title'] for s in meta.get('sheets', [])}
        names = [name for name in SHEETS if name in titles]
        data = self.request('GET', params={'includeGridData': 'true', 'ranges': ["'" + name + "'" for name in names]})
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
            sheets[props['title']] = dict(values=values, raw=raw, rows=props['gridProperties']['rowCount'], cols=props['gridProperties']['columnCount'], id=props['sheetId'])
        return dict(mode='google', target=self.id, fingerprint=digest(clean_fingerprint(data)), sheets=sheets, metadata=data)

    def write(self, plan):
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
        # Contiguous row patches avoid hundreds of thousands of individual API requests.
        grouped = {}
        for change in plan['changes']:
            grouped.setdefault((change['sheet'], change['row']), []).append(change)
        for (name, row), changes in grouped.items():
            changes.sort(key=lambda x: x['col'])
            groups = []
            for item in changes:
                if not groups or item['col'] != groups[-1][-1]['col'] + 1:
                    groups.append([])
                groups[-1].append(item)
            for group in groups:
                requests.append({'updateCells': {'start': {'sheetId': sheet_ids[name], 'rowIndex': row - 1, 'columnIndex': group[0]['col'] - 1}, 'rows': [{'values': [{'userEnteredValue': google_value(c['after'])} for c in group]}], 'fields': 'userEnteredValue'}})
        body = {'requests': requests, 'includeSpreadsheetInResponse': False}
        if len(__import__('json').dumps(body).encode()) > 12_000_000:
            raise AppError('План превышает лимит одной записи MVP. Разделение записи пока не поддерживается.')
        if requests:
            self.request('POST', ':batchUpdate', json=body)

    def verify(self, plan):
        result = self.snapshot()
        for change in plan['changes']:
            rows = result['sheets'][change['sheet']]['raw']
            value = rows[change['row'] - 1][change['col'] - 1] if change['row'] <= len(rows) and change['col'] <= len(rows[change['row'] - 1]) else ''
            if value != change['after']:
                raise AppError(f'Ошибка сверки Google: {change["sheet"]}, {change["cell"]}. Копия сохранена.')


def target(config, google_auth=None):
    return GoogleTarget(config, google_auth) if config['target_mode'] == 'google' else LocalTarget(config)
