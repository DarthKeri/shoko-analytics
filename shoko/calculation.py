"""Pure port of update_google_sheets.py (primary legacy only). No I/O or writes."""
import calendar
import copy
import math
from datetime import date, timedelta
from .storage import AppError
from .mappings import PRICE_MODELS, DRIVER_MODELS

PROSTOI = 'Простои'
DRIVERS = 'Новый старый водитель'
TARGET = 'Предложение цен'
GARAGE = 'Выгрузка_Бот_Гараж'
SUM_COLUMNS = [16, 17, 18, 19, 21, 23, 25, 28]


def cell(row, index):
    return row[index] if index < len(row) else ''


def number(value, context):
    if value in ('', None, '-'):
        return 0
    if isinstance(value, bool):
        raise AppError(f'{context}: вместо числа указан логический признак.')
    try:
        if isinstance(value, str):
            value = value.strip().replace(',', '.').replace('\u00a0', '')
            if value.endswith('%'):
                value = float(value[:-1]) / 100
        result = float(value)
        if not math.isfinite(result):
            raise ValueError()
        return int(result) if result.is_integer() else result
    except (ValueError, TypeError):
        raise AppError(f'{context}: значение «{str(value)[:40]}» не является числом.') from None


def year_value(value):
    try:
        numeric = float(value)
        return int(numeric) if numeric.is_integer() and 1900 <= numeric <= 2100 else None
    except (ValueError, TypeError):
        return None


def set_cell(matrix, r, c, value):
    while len(matrix) <= r:
        matrix.append([])
    matrix[r].extend([''] * max(0, c + 1 - len(matrix[r])))
    matrix[r][c] = value


def format_source(value, column, percent_columns, data_row=False):
    if value in (None, ''):
        return ''
    try:
        if column in percent_columns:
            num = number(value, f'Простои, столбец {column}')
            return f'{num * 100 if 0 <= num <= 1 else num:.1f}%'
        if column in {5, 6, 7, 8, 9, 11}:
            return str(round(number(value, f'Простои, столбец {column}')))
    except AppError:
        # Column titles/model labels are text, not failed numeric inputs.
        if data_row:
            raise
    return value


def assemble_prostoi(current, previous, existing, processing_date):
    result = [] if processing_date.day >= 15 else copy.deepcopy(existing)
    segments = []
    def paste(rows, start, begin, end):
        segments.append((rows, start, begin, end))
        for r, row in enumerate(rows[:373]):
            for i, value in enumerate(row[begin:end]):
                set_cell(result, r, start + i, value)
    if processing_date.day >= 15:
        paste(current, 0, 0, 292)
    else:
        if not previous:
            raise AppError('Для даты до 15-го числа нужен лист предыдущего месяца в книге простоев.')
        prev = processing_date.replace(day=1) - timedelta(days=1)
        days = calendar.monthrange(prev.year, prev.month)[1]
        begin, end = {31: (156, 291), 30: (147, 282), 29: (138, 273), 28: (129, 264)}[days]
        paste(previous, 12, begin, end)
        paste(current, 0, 0, 12)
        paste(current, 147, 12, {31: 291, 30: 282, 29: 264, 28: 264}[days])
    # Determine the column types from the header we are about to publish, after
    # applying the same month slices as the data. The old target header changes
    # after the first run and must not change how identical inputs are interpreted.
    percent_columns = {i + 1 for row in result[1:3] for i, val in enumerate(row)
                       if isinstance(val, str) and val.strip() in ('%', '% НВ')}
    for rows, start, begin, end in segments:
        for r, row in enumerate(rows[:373]):
            for i, value in enumerate(row[begin:end]):
                set_cell(result, r, start + i, format_source(value, start + i + 1, percent_columns, year_value(cell(row, 1)) is not None))
    return result


def source_rows(matrix):
    result = {}
    for r, row in enumerate(matrix):
        model = str(cell(row, 1)).strip()
        if not model or year_value(model):
            continue
        for child in matrix[r + 1:r + 12]:
            year = year_value(cell(child, 1))
            if year and 2017 <= year <= 2026:
                key = (model, year)
                if key in result and result[key] != child:
                    raise AppError(f'Дубли в простоях: {model}, {year}.')
                result[key] = child
    if not result:
        raise AppError('В листе простоев не найдены блоки «модель → год» в столбце B.')
    return result


def date_columns(matrix, processing_date):
    positions = {}
    for offset in (1, 7, 14):
        day = (processing_date - timedelta(days=offset)).day
        matches = []
        for i, value in enumerate(matrix[0]):
            try:
                if float(value) == day:
                    matches.append(i)
            except (ValueError, TypeError):
                pass
        if not matches:
            raise AppError(f'В простоях отсутствует день {day} для даты {(processing_date - timedelta(days=offset)).isoformat()}.')
        positions[offset] = matches[-1]  # primary legacy searches by day, last occurrence wins
    return positions


def driver_columns(rows):
    headers = [str(v).strip() for v in rows[0]] if rows else []
    discounts = [i for i, v in enumerate(headers) if v == 'Скидка/наценка']
    years = [i for i, v in enumerate(headers) if v in ('Год ПТС', 'год ПТС', 'год')]
    missing = []
    if not discounts:
        missing.append('«Скидка/наценка»')
    if not years:
        missing.append('«Год ПТС»')
    # The primary legacy mapping reads the car model from W, with the Toyota
    # override in AM. Do not silently accept a shifted or unrelated export.
    if str(cell(headers, 22)).casefold() not in ('марка', 'автомобиль'):
        missing.append('«марка» / «Автомобиль» в столбце W')
    if missing:
        raise AppError('Отсутствуют обязательные колонки: ' + ', '.join(missing) + '.')
    if len(discounts) != 1 or len(years) != 1:
        raise AppError('Заголовки «Скидка/наценка» и «Год ПТС» должны встречаться по одному разу.')
    if len(rows) < 2:
        raise AppError('Есть заголовки, но нет строк начислений.')
    return discounts[0], years[0]


def driver_counts(rows):
    discount_col, year_col = driver_columns(rows)
    result, unknown = {}, set()
    for row in rows[1:]:
        am = str(cell(row, 38))
        auto = am if 'Toyota' in am else str(cell(row, 22))
        model = auto.split('№')[0].strip()
        year = year_value(cell(row, year_col))
        if not model:
            continue
        discounted = str(cell(row, discount_col)).strip() not in ('', '-', 'None')
        if not year:
            if discounted:
                raise AppError(f'Не указан год ПТС у акционного водителя ({model}).')
            continue
        target_model = DRIVER_MODELS.get(model)
        if target_model is None:
            normalized = ' '.join(model.split()).casefold()
            candidates = {v for k, v in DRIVER_MODELS.items() if ' '.join(k.split()).casefold() == normalized}
            if len(candidates) == 1:
                target_model = candidates.pop()
        if not target_model:
            if discounted:
                unknown.add(model)
            continue
        if ' '.join(model.split()).casefold() == 'volkswagen polo' and year == 2018:
            target_model = 'Polo К'
        if ' '.join(model.split()).casefold() == 'kia ceed sw' and year == 2020:
            target_model = 'Kia Ceed'
        key = (target_model, year)
        result.setdefault(key, 0)
        result[key] += int(discounted)
    if unknown:
        raise AppError('Нужно сопоставление моделей акционных водителей: ' + ', '.join(sorted(unknown)[:12]))
    return result


def calculate(prostoi, drivers, target, processing_date):
    if len(target) < 2:
        raise AppError('Целевой лист «Предложение цен» пуст. Нужен шаблон с классом, моделью и годом в A:C.')
    sources = source_rows(prostoi)
    positions = date_columns(prostoi, processing_date)
    counts = driver_counts(drivers)
    after = copy.deepcopy(target)
    warnings, matched, keys, specials = [], 0, set(), {}
    offsets = {16: 0, 17: 1, 18: 2, 19: 3, 21: 4, 22: 5, 23: 6, 24: 7}
    for r, row in enumerate(target[1:], 1):
        klass, model, raw_year = [str(cell(row, i)).strip() for i in range(3)]
        year = year_value(raw_year)
        if not klass or not model:
            continue
        if not year:
            if raw_year:
                raise AppError(f'Предложение цен, строка {r + 1}: неверный год «{raw_year}».')
            continue
        key = (klass, model, year)
        if key in keys:
            raise AppError(f'Дубли в целевой таблице: {klass}, {model}, {year}.')
        keys.add(key)
        if year == 2019 and klass == 'Эконом' and model in ('Polo', 'Polo К', 'Skoda Octavia', 'Skoda Octavia К'):
            specials[model] = r
        src_model = PRICE_MODELS.get(f'{klass}|{model}', PRICE_MODELS.get(model))
        src = sources.get((src_model, year))
        if src is None:
            warnings.append(f'{model} / {year}: по legacy очищаются F:G и P:AC — соответствие не найдено.')
            for column in [6, 7] + list(range(16, 30)):
                set_cell(after, r, column - 1, '')
        else:
            matched += 1
            special = year == 2019 and klass == 'Эконом' and model in ('Polo', 'Skoda Octavia')
            for column, source_index in [(6, 3), (7, 2)] + [(c, positions[1] + o) for c, o in offsets.items()] + [(25, positions[7] + 6), (26, positions[7] + 7), (28, positions[14] + 6), (29, positions[14] + 7)]:
                if special and column in (22, 24, 26, 29):
                    continue
                if source_index >= len(src):
                    raise AppError(f'{model} / {year}: нет значения в столбце {source_index + 1} простоев.')
                set_cell(after, r, column - 1, number(src[source_index], f'{model} / {year}, столбец {column}'))
        search = model
        if model == 'Skoda Rapid К' and year == 2018:
            search = 'Skoda Rapid'
        if model in ('Kia Ceed SW', 'Kia Ceed') and year == 2020:
            search = 'Kia Ceed'
        set_cell(after, r, 7, counts.get((search, year), 0))
    if not matched:
        raise AppError('Ни одна модель целевого листа не сопоставилась с простоями. Запись заблокирована.')
    for basic, special in [('Polo', 'Polo К'), ('Skoda Octavia', 'Skoda Octavia К')]:
        if basic in specials and special in specials:
            first, second = specials[basic], specials[special]
            for column in SUM_COLUMNS:
                value = number(cell(after[first], column - 1), basic) + number(cell(after[second], column - 1), special)
                set_cell(after, first, column - 1, value)
                set_cell(after, second, column - 1, '')
            for column in (6, 7):
                set_cell(after, second, column - 1, '')
    return after, warnings
