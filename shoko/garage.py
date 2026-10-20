"""Reviewed Garage lifecycle. Pure calculations: no network or workbook writes."""
import copy
import math
import re
from datetime import datetime
from openpyxl.utils import get_column_letter, column_index_from_string
from .storage import AppError
from .calculation import TARGET, cell, set_cell

DATED = re.compile(r'^Гараж (\d{2}\.\d{2}\.\d{4})$')
LINK = re.compile(r"^='(Гараж \d{2}\.\d{2}\.\d{4})'!\$?([A-Z]+)\$?(\d+)$", re.I)
GUARDED_LINK = re.compile(r'''^=IF\(COUNT\('(Гараж \d{2}\.\d{2}\.\d{4})'!P(\d+):BC\2\)=0;(?:""|"-");'\1'!([A-Z]+)\2\)$''', re.I)
EMPTY_LINK = re.compile(r"""^=IF\(\s*('Гараж \d{2}\.\d{2}\.\d{4}'!\$?[A-Z]+\$?\d+)\s*=\s*""\s*[;,]\s*"([^"]{0,100})"\s*[;,]\s*\1\s*\)$""", re.I)
ERROR_LINK = re.compile(r"""^=IFERROR\(\s*('Гараж \d{2}\.\d{2}\.\d{4}'!\$?[A-Z]+\$?\d+)\s*[;,]\s*"([^"]{0,100})"\s*\)$""", re.I)
ERROR_VALUES = {'#DIV/0!', '#N/A', '#REF!', '#VALUE!', '#NUM!', '#NAME?', '#NULL!', '#ERROR!'}
SUMMARY = {10: 'N', 11: 'BE', 12: 'BF', 13: 'L', 14: 'BH', 15: 'BI'}
REQUIRED = ('Марка и модель', 'Тариф', 'Год выпуска', 'Цена аренды 7 / 0', 'Тип топлива', 'КПП', 'Таксопарк')
BRANDS = {'volkswagen', 'skoda', 'kia', 'nissan', 'toyota', 'hyundai', 'chery', 'cherry', 'geely', 'haval', 'jac', 'faw'}


def latest_garage(names):
    dated = []
    for name in names:
        match = DATED.fullmatch(name)
        if match:
            try:
                dated.append((datetime.strptime(match[1], '%d.%m.%Y').date(), name))
            except ValueError:
                raise AppError(f'Неверная дата в названии листа «{name}».') from None
    return max(dated)[1] if dated else None


def managed_names(names, extra=()):
    from .calculation import PROSTOI, DRIVERS, GARAGE
    latest = latest_garage(names)
    wanted = {PROSTOI, DRIVERS, TARGET, GARAGE, *extra}
    if latest:
        wanted.add(latest)
    return [name for name in names if name in wanted]


def formula(value):
    return {'formula': value}


def formula_text(value):
    return value['formula'] if isinstance(value, dict) and set(value) == {'formula'} else value


def normalize_class(value):
    return re.sub(r'\s+', '', re.sub(r'\(\s*[кk]\s*\)', '', str(value), flags=re.I)).casefold()


def normalize_model(value):
    value = ' '.join(str(value).casefold().split())
    value = re.sub(r'\s+[кk]$', '', value).strip()
    if value.startswith('kia k5 комфорт'):
        value = 'kia k5'
    parts = value.split(' ', 1)
    if parts[0] in BRANDS and len(parts) == 2:
        value = parts[1]
    return value


def car_key(row):
    klass, model, year = (str(cell(row, i)).strip() for i in range(3))
    return (normalize_class(klass), normalize_model(model), year) if klass and model and re.fullmatch(r'\d{4}', year) else None


def ignored(key):
    return key in {('эконом', 'ceed', '2018'), ('эконом', 'ceed sw', '2020'), ('комфорт+', 'bestune t77', '2023')}


def classes_match(car, bot):
    if car[0] == bot[0]:
        return True
    special = (car[1] == 'arrizo 8' and car[2] == '2024') or (car[1] == 'j7' and car[2] == '2022') or (car[1] == 'optima' and car[2] in ('2019', '2020'))
    return special and car[0] in ('комфорт', 'комфорт+') and bot[0] in ('комфорт', 'комфорт+')


def bot_offers(rows):
    if len(rows) < 2:
        raise AppError('CSV Гаража пуст. Обновление заблокировано.')
    headers = [str(v).strip() for v in rows[0]]
    missing = [h for h in REQUIRED if h not in headers]
    if missing:
        raise AppError('В CSV Гаража нет колонок: ' + ', '.join(missing))
    if any(headers.count(h) != 1 for h in REQUIRED):
        raise AppError('В CSV Гаража повторяются обязательные заголовки. Выберите корректную выгрузку.')
    columns = {h: headers.index(h) for h in REQUIRED}
    result = []
    for n, row in enumerate(rows[1:], 2):
        v = {h: str(cell(row, c)).strip() for h, c in columns.items()}
        if v['Тип топлива'].casefold() != 'бензин' or 'автомат' not in v['КПП'].casefold() or v['Таксопарк'].casefold() == 'автопорт':
            continue
        klass = normalize_class(v['Тариф'])
        if klass not in ('эконом', 'комфорт', 'комфорт+') or not v['Цена аренды 7 / 0']:
            continue
        # Accept currency and grouping spaces, never turn "1 500,50" into 150050.
        price = re.sub(r'\s+', '', v['Цена аренды 7 / 0']).replace('₽', '').replace('руб.', '').replace('руб', '').replace(',', '.')
        if not re.fullmatch(r'\d+(?:\.\d{1,2})?', price) or float(price) <= 0:
            raise AppError(f'CSV Гаража, строка {n}: неверная цена аренды 7/0.')
        if not re.fullmatch(r'\d{4}', v['Год выпуска']):
            raise AppError(f'CSV Гаража, строка {n}: неверный год выпуска.')
        result.append(((klass, normalize_model(v['Марка и модель']), v['Год выпуска']), float(price)))
    return result



def match_garage_rows(template, csv_rows):
    headers = template[0] if template else []
    comp = [i for i, h in enumerate(headers) if re.fullmatch(r'Конкурент\s+([1-9]|[1-3]\d|40)', str(h).strip(), re.I)]
    if comp != list(range(15, 55)):
        raise AppError('В шаблоне Гаража нужны колонки «Конкурент 1…40» в P:BC. Проверьте структуру.')
    offers = bot_offers(csv_rows)
    cars, prices, matched, cleared, skipped = {}, {}, 0, 0, 0
    for r, row in enumerate(template[1:], 1):
        key = car_key(row)
        if not key:
            continue
        if key in cars:
            raise AppError(f'Неоднозначные строки Гаража: {key[1]} / {key[2]}.')
        cars[key] = r
        values = sorted(p for b, p in offers if b[1:] == key[1:] and classes_match(key, b)) if not ignored(key) else []
        prices[r] = values
        if ignored(key):
            skipped += 1
        elif values:
            matched += 1
        else:
            cleared += 1
    if not matched:
        raise AppError(f'Гараж: ни одного совпадения. В CSV {len(csv_rows) - 1} записей, после фильтров {len(offers)}, строк шаблона {len(cars)}. Обновление всей таблицы заблокировано; старые цены не изменены.')
    return dict(cars=cars, prices=prices, eligible=len(offers), matched=matched, cleared=cleared, ignored=skipped)


def comparison_before(plan):
    """Virtual baseline for names that are renamed/duplicated in the same batch."""
    before = dict(plan['before'], sheets=dict(plan['before']['sheets']))
    for op in plan.get('operations', []):
        source = plan['before']['sheets'][op['source']]
        before['sheets'][op['target']] = source
    return before


def _unique_archive(name, names):
    base = 'Гараж ' + DATED.fullmatch(name)[1].replace('.', '/') + ' (архив)'
    if base not in names:
        return base
    for n in range(2, 1000):
        candidate = base[:-1] + f' {n})'
        if candidate not in names:
            return candidate
    raise AppError('Не удалось выбрать свободное имя архива Гаража.')


def _number(value, address):
    if value in ('', None):
        return None
    if type(value) in (int, float) and math.isfinite(value):
        return value
    raise AppError(f'Гараж, {address}: для проверки формулы ожидалось число.')


def summary_value(rows, row, col, visiting=None, native=False, expression=None):
    """Evaluate only the existing summary formula grammar, never execute code."""
    address = f'{get_column_letter(col)}{row + 1}'
    value = cell(rows[row], col - 1)
    text = formula_text(value)
    if expression is not None:
        text, value = expression, formula(expression)
    if not isinstance(value, dict):
        if native and isinstance(text, str) and text in ERROR_VALUES:
            return text
        return _number(text, address)
    if native:
        guarded_market = re.fullmatch(r'=IF\(COUNT\(P(\d+):([A-Z]+)\1\)=0;"-";(.*)\)', text, re.I|re.S)
        if guarded_market and int(guarded_market[1])==row+1:
            end=column_index_from_string(guarded_market[2])
            if not 16<=end<=55:raise AppError('Неожиданный диапазон проверки рынка.')
            if not any(type(cell(rows[row],c)) in (int,float) for c in range(15,end)):
                return '-'
            return summary_value(rows,row,col,visiting,native=True,expression='='+guarded_market[3])
    if native:
        guarded = re.fullmatch(r'=IFERROR\((.+)[;,]"(-|)"\)', text.upper().replace(' ', '').replace('$', ''))
        if guarded:
            computed = summary_value(rows, row, col, visiting, native=True, expression='=' + guarded[1])
            return guarded[2] if isinstance(computed, str) and computed in ERROR_VALUES else computed
    visiting = set() if visiting is None else set(visiting)
    if col in visiting:
        raise AppError(f'Циклическая формула Гаража: {address}.')
    visiting.add(col)
    text = text.upper().replace(' ', '').replace('$', '')
    match = re.fullmatch(r'=(MIN|MAX|AVERAGE|COUNTA)\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)', text)
    if match and int(match[3]) == row + 1 == int(match[5]):
        start, end = column_index_from_string(match[2]), column_index_from_string(match[4])
        if not (16 <= start <= end <= 55):
            raise AppError(f'Неожиданный диапазон формулы Гаража: {address}.')
        values = [cell(rows[row], c - 1) for c in range(start, end + 1)]
        if match[1] == 'COUNTA':
            return sum(v not in ('', None) for v in values)
        numbers = [_number(v, address) for v in values if v not in ('', None)]
        if not numbers:
            return (0 if match[1] in ('MIN', 'MAX') else '#DIV/0!') if native else None
        return {'MIN': min, 'MAX': max, 'AVERAGE': lambda v: math.fsum(v) / len(v)}[match[1]](numbers)
    match = re.fullmatch(r'=AVERAGE\(([A-Z]+)(\d+)[;,]([A-Z]+)(\d+)\)', text)
    if match and int(match[2]) == row + 1 == int(match[4]):
        values = [summary_value(rows, row, column_index_from_string(match[i]), visiting, native=native) for i in (1, 3)]
        if native and any(isinstance(v, str) and v in ERROR_VALUES for v in values):
            return next(v for v in values if isinstance(v, str) and v in ERROR_VALUES)
        numbers = [v for v in values if v is not None]
        return math.fsum(numbers) / len(numbers) if numbers else ('#DIV/0!' if native else None)
    raise AppError(f'Неподдерживаемая формула Гаража: {address}. Обновление заблокировано.')


def prepare_garage(before, after, csv_rows, processing, update_links=True, archive_source=None):
    names = [s['properties']['title'] for s in before.get('metadata', {}).get('sheets', [])] or list(before['sheets'])
    source_name = latest_garage(names)
    if not source_name or source_name not in before['sheets']:
        raise AppError('Не найден датированный лист «Гараж ДД.ММ.ГГГГ». Нужен существующий шаблон Гаража.')
    source_date = datetime.strptime(DATED.fullmatch(source_name)[1], '%d.%m.%Y').date()
    if processing < source_date:
        raise AppError('Дата обработки раньше рабочего листа Гаража. Выберите копию таблицы за нужный период.')
    work_name = f'Гараж {processing:%d.%m.%Y}'
    source = before['sheets'][source_name]
    rows = copy.deepcopy(source['values'])
    raw = source.get('raw', rows)
    typed_formulas = {tuple(position) for position in source['formula_cells']} if 'formula_cells' in source else None
    for r, row in enumerate(raw):
        for c, value in enumerate(row):
            if isinstance(value, str) and value.startswith('=') and (typed_formulas is None or (r, c) in typed_formulas):
                set_cell(rows, r, c, formula(value.replace(f"'{source_name}'!", f"'{work_name}'!")))
    matches = match_garage_rows(source['values'], csv_rows)
    cars = matches['cars']
    matched, cleared, skipped = (matches[k] for k in ('matched', 'cleared', 'ignored'))
    comp = list(range(15, 55))
    for r, prices in matches['prices'].items():
        for i, c in enumerate(comp):
            set_cell(rows, r, c, prices[i] if i < len(prices) else '')
    operations = []
    if work_name != source_name:
        archive = _unique_archive(source_name, names)
        frozen = copy.deepcopy((archive_source or source)['values'])
        if before['mode'] == 'local' and any(isinstance(v, str) and v.startswith('=') and cell(frozen[r] if r < len(frozen) else [], c) == '' for r, row in enumerate(raw) for c, v in enumerate(row)):
            raise AppError('В локальном Гараже нет сохранённых результатов формул для архива. Пересчитайте копию в Excel или используйте Google-таблицу.')
        after[archive] = frozen
        operations = [dict(kind='archive', source=source_name, target=archive), dict(kind='rename', source=source_name, target=work_name)]
    after[work_name] = rows
    links, expected = 0, []
    empty_links = 0
    skipped_links, preserved = [], []
    target_raw = before['sheets'][TARGET].get('raw', before['sheets'][TARGET]['values'])
    target_formula_cells = before['sheets'][TARGET].get('formula_cells')
    target_formula_cells = set(map(tuple, target_formula_cells)) if target_formula_cells is not None else None
    for r, old_row in enumerate(before['sheets'][TARGET]['values'][1:], 1):
        key = car_key(old_row)
        if not key:
            continue
        mapped = cars.get(key)
        for col, source_col in SUMMARY.items():
            old = cell(target_raw[r] if r < len(target_raw) else [], col - 1)
            address = f'{get_column_letter(col)}{r + 1}'
            if not update_links:
                # We do not own J:O. Verify preserved entries, not guessed results.
                is_formula = isinstance(old, str) and old.startswith('=') and (target_formula_cells is None or (r, col - 1) in target_formula_cells)
                value = old.replace(f"'{source_name}'!", f"'{work_name}'!") if is_formula else old
                preserved.append(dict(sheet=TARGET, row=r + 1, col=col, value=value))
            def skip_dash(reason):
                skipped_links.append(dict(cell=address, reason=reason))
            if not update_links and isinstance(old, str) and (old.strip() == '-' or re.fullmatch(r'=\s*"-"\s*', old)):
                skip_dash('прочерк')
                continue
            link = LINK.fullmatch(old) if isinstance(old, str) else None
            guarded = GUARDED_LINK.fullmatch(old) if isinstance(old, str) else None
            empty_wrapper = EMPTY_LINK.fullmatch(old) if isinstance(old, str) else None
            error_wrapper = ERROR_LINK.fullmatch(old) if isinstance(old, str) else None
            dash_logic = not update_links and (
                (isinstance(old, str) and old.startswith('=') and re.search(r'"\s*-\s*"', old))
                or cell(old_row, col - 1) == '-')
            if not update_links and (empty_wrapper or error_wrapper):
                link = LINK.fullmatch('=' + (empty_wrapper or error_wrapper)[1])
            if guarded:
                link = LINK.fullmatch(f"='{guarded[1]}'!{guarded[3]}{guarded[2]}")
            if link:
                allowed_columns = {10: {'N'}, 11: {'BE', 'J'}, 12: {'BF'}, 13: {'L', 'BG'}, 14: {'BH'}, 15: {'BI'}}
                if link[1] != source_name or link[2].upper() not in allowed_columns[col]:
                    if dash_logic:
                        skip_dash('собственная формула с прочерком; другая ссылка или показатель')
                        continue
                    raise AppError(f'Предложение цен, {get_column_letter(col)}{r + 1}: ссылка на другой Гараж или показатель. Проверьте шаблон.')
                source_col = link[2].upper()
                source_row = int(link[3]) - 1
                # Preserve explicitly mapped variant rows only when model/year agree.
                linked_key = car_key(source['values'][source_row]) if 0 < source_row < len(source['values']) else None
                if not linked_key or linked_key[1:] != key[1:]:
                    if dash_logic:
                        skip_dash('собственная формула с прочерком; другое сопоставление модели')
                        continue
                    raise AppError(f'Предложение цен, {get_column_letter(col)}{r + 1}: модель/год не совпадают со ссылкой Гаража.')
            elif not update_links and old in ('', None):
                empty_links += 1
                continue  # Original importer leaves empty J:O cells untouched.
            elif not update_links:
                if dash_logic:
                    skip_dash('собственная формула, показывающая прочерк')
                    continue
                raise AppError(f'Предложение цен, {get_column_letter(col)}{r + 1}: неподдерживаемая формула или значение в J:O. Проверьте формулу связи с листом «{source_name}» внутри этой таблицы. Адрес сайта вводить не нужно; приложение не переписывает J:O.')
            elif mapped is not None:
                source_row = mapped
            else:
                continue
            count = sum(cell(rows[source_row], c) not in ('', None) for c in comp)
            if not update_links:
                try:
                    value = ('-' if ';"-";' in old else None) if guarded and not count else summary_value(rows, source_row, column_index_from_string(source_col), native=True)
                except AppError:
                    if not dash_logic:
                        raise
                    skip_dash('собственная формула с прочерком; результат не вычисляется приложением')
                    continue
                if empty_wrapper and value in ('', None):
                    value = empty_wrapper[2]
                if error_wrapper and isinstance(value, str) and value in ERROR_VALUES:
                    value = error_wrapper[2]
            else:
                value = summary_value(rows, source_row, column_index_from_string(source_col)) if count else (0 if col == 10 else None)
            # IFERROR makes no-offer rows blank, while count remains numeric zero.
            reference = f"'{work_name}'!{source_col}{source_row + 1}"
            expression = '=' + reference if col == 10 else f'=IF(COUNT(\'{work_name}\'!P{source_row + 1}:BC{source_row + 1})=0;"";{reference})'
            if update_links:
                set_cell(after[TARGET], r, col - 1, formula(expression))
            expected.append(dict(sheet=TARGET, row=r + 1, col=col, value='' if value is None else value))
            links += 1
    if not links and update_links:
        raise AppError('Не удалось связать Гараж с J:O листа «Предложение цен». Обновление заблокировано.')
    archived_errors = sum(isinstance(v, str) and v.startswith(('#DIV/0!', '#N/A', '#REF!', '#VALUE!', '#NUM!', '#NAME?', '#NULL!', '#ERROR!')) for row in source['values'] for v in row) if operations else 0
    return dict(operations=operations, garage=dict(sheet=work_name, csv_rows=len(csv_rows) - 1, eligible=matches['eligible'], matched=matched, cleared=cleared, ignored=skipped, links=links, empty_links=empty_links, skipped_links=skipped_links, archived_errors=archived_errors), derived=expected, preserved=preserved)
