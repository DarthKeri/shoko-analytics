"""DOCX + three RTF business rules. Pure calculations, no service/file writes.

Mappings and fixed row numbers copied from the archived RTFs (2026-10-19).
See docs/DOCX_RTF_PARITY.md for provenance, approved corrections and open issues.
"""
import copy
import math
import re
from .calculation import cell, set_cell
from .storage import AppError

PRICE_MODEL_MAPPING = {'volkswagen polo 2022 г.': ('Volkswagen Polo', 2022),
 'volkswagen polo 2021 г.': ('Volkswagen Polo', 2021),
 'volkswagen polo 2020 г.': ('Volkswagen Polo', 2020),
 'volkswagen polo 2019 г.': ('Volkswagen Polo', 2019),
 'volkswagen polo 2018 г. (каршеринг)': ('Volkswagen Polo K', 2018),
 'kia rio 2021 г.': ('Kia Rio', 2021),
 'kia ceed 2020 г.': ('Kia Ceed', 2020),
 'kia ceed 2018 г.': ('Kia Ceed', 2018),
 'kia cerato 2021 г.': ('Kia Cerato', 2021),
 'kia cerato 2020 г.': ('Kia Cerato', 2020),
 'kia cerato актив 2021 г.': ('Kia Cerato Актив', 2021),
 'kia optima 2020 г.': ('Kia Optima', 2020),
 'kia optima 2019 г.': ('Kia Optima', 2019),
 'kia optima люкс 2019 г.': ('Kia Optima Люкс', 2019),
 'kia optima люкс 2018 г.': ('Kia Optima Люкс', 2018),
 'kia k5 2021 г.': ('Kia K5', 2021),
 'kia k5 комфорт 2022 г.': ('Kia K5 Комфорт', 2022),
 'kia k5 комфорт 2021 г.': ('Kia K5 Комфорт', 2021),
 'kia k5 престиж 2021 г.': ('Kia K5 Престиж', 2021),
 'skoda rapid 2021 г.': ('Skoda Rapid', 2021),
 'skoda rapid 2018 г. (каршеринг)': ('Skoda Rapid K', 2018),
 'skoda octavia 2020 г.': ('Skoda Octavia', 2020),
 'skoda octavia 2019 г.': ('Skoda Octavia', 2019),
 'nissan qashqai 2019 г. эконом': ('Nissan Qashqai', 2019),
 'москвич 3 2023 г.': ('Москвич 3', 2023),
 'toyota camry 2021 г.': ('Toyota Camry', 2021),
 'toyota camry 2020 г.': ('Toyota Camry', 2020),
 'toyota camry 2.5 2021 г.': ('Toyota Camry 2.5', 2021),
 'toyota camry актив+ 2021 г.': ('Toyota Camry Актив+', 2021),
 'toyota camry актив+ 2020 г.': ('Toyota Camry Актив+', 2020),
 'джак j7 2022 г.': ('Jac J7', 2022),
 'hyundai sonata 2020 г.': ('Hyundai Sonata', 2020),
 'geely emgrand 2023г.': ('Geely Emgrand', 2023),
 'geely atlas pro 2024г.': ('Geely Atlas Pro', 2024),
 'haval f7 2024г.': ('Haval F7', 2024),
 'haval f7 elite 2024г.': ('Haval F7 Элит', 2024),
 'haval jolion 2024г.': ('Haval Jolion', 2024),
 'faw bestune t77 2023г.': ('Faw Bestune T77', 2023),
 'chery arrizo 8 2024г.': ('Chery Arrizo 8', 2024),
 'chery tiggo 2022 г.': ('Chery Tiggo 4', 2022),
 'chery tiggo 7 pro 2024 г.': ('Chery Tiggo 7 Pro Max', 2024),
 'kia rio x 2021г. эконом': ('Kia Rio X', 2021)}

# Explicit current-file additions approved by the user on 2026-10-19.
# Keep these separate from the historical RTF and from driver/fleet mappings.
CURRENT_PRICE_ADDITIONS = {
    ('kia optima', 2018): ('Kia Optima', 2018),
    ('skoda octavia каршеринг', 2019): ('Skoda Octavia К', 2019),
    ('volkswagen polo каршеринг', 2019): ('Volkswagen Polo K', 2019),
    ('tenet t77', 2026): ('Tenet T7', 2026),
    ('tenet t7', 2026): ('Tenet T7', 2026),
}

PRICE_SKIP_ROWS = [1, 2, 3, 4, 5, 6, 7, 8, 15, 17, 20, 24, 30, 35, 38, 42, 44, 46, 52, 54, 56, 58, 60, 63, 65, 67, 69, 72]

ACTION_MODEL_MAPPING = {('volkswagen polo эконом', 2020): ('Volkswagen Polo', 2020),
 ('volkswagen polo эконом', 2021): ('Volkswagen Polo', 2021),
 ('volkswagen polo эконом', 2022): ('Volkswagen Polo', 2022),
 ('volkswagen polo эконом', 2019): ('Volkswagen Polo', 2019),
 ('volkswagen polo эконом к', 2019): ('Volkswagen Polo K', 2019),
 ('volkswagen polo эконом к', 2018): ('Volkswagen Polo K', 2018),
 ('kia rio эконом', 2021): ('Kia Rio', 2021),
 ('kia rio x эконом', 2021): ('Kia Rio X', 2021),
 ('skoda rapid эконом', 2021): ('Skoda Rapid', 2021),
 ('skoda rapid эконом к', 2021): ('Skoda Rapid K', 2021),
 ('skoda rapid эконом', 2018): ('Skoda Rapid K', 2018),
 ('skoda rapid эконом к', 2018): ('Skoda Rapid K', 2018),
 ('skoda octavia эконом', 2019): ('Skoda Octavia', 2019),
 ('skoda octavia эконом к', 2019): ('Skoda Octavia K', 2019),
 ('skoda octavia комфорт', 2020): ('Skoda Octavia', 2020),
 ('kia ceed эконом', 2018): ('Kia Ceed', 2018),
 ('kia ceed комфорт', 2020): ('Kia Ceed', 2020),
 ('kia cerato комфорт', 2020): ('Kia Cerato', 2020),
 ('kia cerato комфорт', 2021): ('Kia Cerato', 2021),
 ('kia cerato актив комфорт', 2021): ('Kia Cerato Актив', 2021),
 ('nissan qashqai эконом', 2019): ('Nissan Qashqai', 2019),
 ('chery tiggo 4 комфорт', 2022): ('Chery Tiggo 4', 2022),
 ('москвич 3 комфорт', 2023): ('Москвич 3', 2023),
 ('geely emgrand комфорт', 2023): ('Geely Emgrand', 2023),
 ('haval jolion комфорт', 2024): ('Haval Jolion', 2024),
 ('jac j7 комфорт +', 2022): ('Jac J7', 2022),
 ('kia optima комфорт', 2019): ('Kia Optima', 2019),
 ('kia optima комфорт', 2020): ('Kia Optima', 2020),
 ('kia optima люкс комфорт', 2018): ('Kia Optima Люкс', 2018),
 ('kia optima люкс комфорт', 2019): ('Kia Optima Люкс', 2019),
 ('kia k5 комфорт +', 2021): ('Kia K5', 2021),
 ('kia k5 комфорт комфорт +', 2021): ('Kia K5 Комфорт', 2021),
 ('kia k5 престиж комфорт +', 2021): ('Kia K5 Престиж', 2021),
 ('toyota camry комфорт +', 2021): ('Toyota Camry', 2021),
 ('toyota camry комфорт +', 2020): ('Toyota Camry', 2020),
 ('toyota camry актив комфорт +', 2021): ('Toyota Camry Актив+', 2021),
 ('toyota camry актив комфорт +', 2020): ('Toyota Camry Актив+', 2020),
 ('toyota camry 2.5 комфорт +', 2021): ('Toyota Camry 2.5', 2021),
 ('hyundai sonata комфорт +', 2020): ('Hyundai Sonata', 2020),
 ('kia k5 комфорт комфорт +', 2022): ('Kia K5 Комфорт', 2022),
 ('haval f7 комфорт +', 2024): ('Haval F7', 2024),
 ('haval f7 элит комфорт +', 2024): ('Haval F7 Элит', 2024),
 ('geely atlas pro комфорт +', 2024): ('Geely Atlas Pro', 2024),
 ('chery arrizo 8 комфорт +', 2024): ('Chery Arrizo 8', 2024),
 ('faw bestune t77 комфорт +', 2023): ('Faw Bestune T77', 2023),
 ('chery tiggo7 pro max комфорт +', 2024): ('Chery Tiggo 7 Pro Max', 2024)}

FLEET_CAR_MAPPING = {'Volkswagen Polo Эконом': ('Volkswagen Polo', 'Эконом'),
 'Volkswagen Polo Эконом К': ('Volkswagen Polo K', 'Эконом'),
 'Kia Rio Эконом': ('Kia Rio', 'Эконом'),
 'Kia Rio X Эконом': ('Kia Rio X', 'Эконом'),
 'Kia Ceed Эконом': ('Kia Ceed', 'Эконом'),
 'Kia Ceed Комфорт': ('Kia Ceed', 'Эконом'),
 'Kia Cerato Комфорт': ('Kia Cerato', 'Комфорт'),
 'Kia CERATO Актив Комфорт': ('Kia CERATO Актив', 'Комфорт'),
 'Kia Optima Комфорт': ('Kia Optima', 'Комфорт'),
 'Kia Optima Люкс Комфорт': ('Kia Optima Люкс', 'Комфорт'),
 'Kia K5 Комфорт +': ('Kia K5', 'Комфорт'),
 'Kia K5 Комфорт Комфорт +': ('Kia K5 Комфорт', 'Комфорт +'),
 'Kia K5 Престиж Комфорт +': ('Kia K5 Престиж', 'Комфорт'),
 'Skoda Rapid Эконом': ('Skoda Rapid', 'Эконом'),
 'Skoda RAPID Эконом К': ('Skoda RAPID K', 'Эконом'),
 'Skoda Octavia Эконом': ('Skoda Octavia', 'Эконом'),
 'SKODA OCTAVIA Эконом К': ('Skoda Octavia', 'Эконом'),
 'Skoda OCTAVIA Комфорт К': ('Skoda OCTAVIA', 'Эконом'),
 'Skoda OCTAVIA Комфорт': ('Skoda OCTAVIA', 'Эконом'),
 'Nissan Qashqai Эконом': ('Nissan Qashqai', 'Эконом'),
 'МОСКВИЧ 3 Комфорт': ('МОСКВИЧ 3', 'Комфорт'),
 'Toyota Camry Комфорт +': ('Toyota Camry', 'Комфорт'),
 'Toyota CAMRY Актив Комфорт +': ('Toyota CAMRY Актив+', 'Комфорт'),
 'Toyota Camry 2.5 Комфорт +': ('Toyota Camry 2.5', 'Комфорт'),
 'JAC J7 Комфорт +': ('JAC J7', 'Комфорт'),
 'Hyundai Sonata Комфорт +': ('Hyundai Sonata', 'Комфорт'),
 'Geely EMGRAND Комфорт': ('Geely EMGRAND', 'Комфорт'),
 'Geely Atlas Pro Комфорт +': ('Geely Atlas Pro', 'Комфорт +'),
 'HAVAL F7 Комфорт +': ('HAVAL F7', 'Комфорт +'),
 'HAVAL f7 Элит Комфорт +': ('HAVAL f7 Элит', 'Комфорт +'),
 'HAVAL JOLION Комфорт': ('HAVAL JOLION', 'Комфорт'),
 'FAW BESTUNE T77 Комфорт +': ('FAW BESTUNE T77', 'Комфорт +'),
 'CHERY Arrizo 8 Комфорт +': ('CHERY Arrizo 8', 'Комфорт +'),
 'CHERY Tiggo 4 Комфорт': ('CHERY Tiggo 4', 'Комфорт'),
 'CHERY TIGGO7 PRO MAX Комфорт +': ('CHERY TIGGO 7 PRO MAX', 'Комфорт +')}

FLEET_MODEL_YEARS = {14: [18, 19, 20, 21],
 24: [31, 32],
 34: [39],
 44: [49],
 54: [62],
 64: [70],
 74: [79, 80],
 84: [89],
 94: [100, 101],
 104: [111, 112],
 114: [119],
 124: [128, 129],
 134: [139],
 144: [149],
 154: [162],
 164: [171],
 174: [181],
 184: [191],
 194: [201],
 214: [217],
 224: [229, 230],
 234: [239, 240],
 244: [249],
 254: [258],
 264: [270],
 274: [277],
 284: [286],
 294: [296],
 304: [306],
 314: [316],
 324: [327],
 334: [336],
 344: [348],
 354: [356]}


def normalize_text(value):
    return ' '.join(value.lower().strip().split()) if isinstance(value, str) else ''


def integer(value):
    if value in ('', None):
        return None
    try:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def numeric(value, context):
    if value in ('', None):
        return 0
    if isinstance(value, str) and 'результата формулы' in value:
        raise AppError(context + ': нет сохранённого результата формулы. Пересчитайте файл в Excel.')
    if isinstance(value, bool):
        raise AppError(context + ': вместо числа получено логическое значение.')
    try:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return number
    except (ValueError, TypeError, OverflowError):
        raise AppError(context + ': значение не является числом. Исправьте исходный файл.') from None


def model_key(model, year):
    name = normalize_text(str(model))
    name = re.sub(r'\s+[кk]$', ' k', name)
    return name, integer(year)


def target_rows(rows):
    result = {}
    for index, row in enumerate(rows[1:], 1):
        model, year = str(cell(row, 1)).strip(), integer(cell(row, 2))
        if not model or year is None:
            continue
        key = model_key(model, year)
        if key in result:
            raise AppError(f'Предложение цен: неоднозначная модель «{model}», {year}: строки {result[key]+1} и {index+1}.')
        result[key] = index
    return result


def price_layout(rows):
    """Identify the current split-column order without guessing price columns."""
    header = rows[7] if len(rows) > 7 else []
    fields = [normalize_text(cell(header, col)) for col in range(5)]
    split = fields[0] == 'автомобиль' or fields[1] == 'год выпуска' or fields[2] == 'доп.признак'
    if split:
        if not (fields[:3] == ['автомобиль', 'год выпуска', 'доп.признак']
                and fields[3].startswith('действующий водитель')
                and fields[4].startswith('новый водитель')):
            raise AppError('Приказ цен: проверьте заголовки A8:E8: автомобиль, год выпуска, доп.признак, действующий водитель, новый водитель.')
        return 'split'
    return 'rtf'


def split_price_mapping():
    # Keep the approved RTF identities; only move year/variant out of its text.
    result = {}
    for text, target in PRICE_MODEL_MAPPING.items():
        match = re.fullmatch(r'(.+?)\s+(\d{4})\s*г\.\s*(.*)', text)
        model, year, suffix = match.groups()
        if suffix == 'эконом':
            suffix = ''  # This class was part of the old source label only.
        name = normalize_text(model + ' ' + suffix.strip('()'))
        result[(name, int(year))] = target
    result.update(CURRENT_PRICE_ADDITIONS)
    return result


def price_table(rows):
    layout = price_layout(rows)
    result = {}; unmapped = []
    aliases = split_price_mapping() if layout == 'split' else {}
    stop = len(rows) if layout == 'split' else min(73, len(rows))
    for index in range(8, stop):
        row = rows[index]
        if layout == 'split':
            model = normalize_text(cell(row, 0))
            raw_year = cell(row, 1)
            if not model or raw_year in ('', None):
                continue  # Text outside the vehicle table has no year.
            year = numeric(raw_year, f'Приказ цен, B{index+1}')
            if year != int(year) or not 1900 <= year <= 2100:
                raise AppError(f'Приказ цен, B{index+1}: нужен четырёхзначный год выпуска.')
            variant = normalize_text(str(cell(row, 2) or ''))
            key = aliases.get((normalize_text(model + ' ' + variant), int(year)))
            if key is None:
                unmapped.append(f'A{index+1}: {model} {int(year)}' + (f' ({variant})' if variant else ''))
                continue
            columns = (4, 3)  # New driver E -> F; current driver D -> G.
            if key in result:
                raise AppError(f'Приказ цен, строка {index+1}: повтор модели/года. Уточните дублирующиеся цены.')
        else:
            if index + 1 in PRICE_SKIP_ROWS:
                continue
            key = PRICE_MODEL_MAPPING.get(normalize_text(cell(row, 0)))
            if key is None:
                continue
            columns = (2, 1)
        result[key] = [int(numeric(cell(row, column), f'Приказ цен, строка {index+1}')) for column in columns]
    if not result:
        if layout == 'split':
            raise AppError('Приказ цен: в A/B/C первого листа нет моделей/годов из согласованного справочника.')
        raise AppError('Приказ цен: в строках 9–73 первого листа нет моделей из справочника RTF.')
    return {'layout': layout, 'values': result, 'unmapped': unmapped}


def price_values(rows):
    return price_table(rows)['values']


def price_notes(rows):
    unmapped = price_table(rows)['unmapped']
    return ['Приказ цен: нет в согласованном справочнике, цены этих моделей не меняются: ' + '; '.join(unmapped) + '.'] if unmapped else []


def apply_prices(source, target):
    after = copy.deepcopy(target); mapping = target_rows(target); matched = 0
    for key, values in price_values(source).items():
        key = model_key(*key)
        if key in mapping:
            matched += 1
            for column, value in zip((5, 6), values):
                set_cell(after, mapping[key], column, value)
    if not matched:
        raise AppError('Приказ цен: ни одна модель/год не совпала с «Предложением цен». Запись заблокирована.')
    return after, matched


def action_counts(rows):
    if not rows or max(map(len, rows), default=0) < 41:
        raise AppError('Акционные водители: нужны колонки H, AM (модель с классом) и AO (год ПТС).')
    headers = [normalize_text(str(value)) for value in rows[0]]
    for column, title in ((7, 'скидка/наценка'), (40, 'год птс')):
        if column >= len(headers) or headers[column] != title:
            raise AppError('В начислениях отсутствует колонка «' + ('Скидка/наценка» в H.' if column == 7 else 'Год ПТС» в AO.'))
        if headers.count(title) != 1:
            raise AppError('Обязательные заголовки начислений должны встречаться по одному разу.')
    counts = {}; known = 0
    for line, row in enumerate(rows[1:], 2):
        if any(isinstance(cell(row, c), str) and 'результата формулы' in cell(row, c) for c in (7, 38, 40)):
            raise AppError(f'Начисления, строка {line}: нет сохранённого результата формулы в H/AM/AO. Пересчитайте файл в Excel.')
        key = normalize_text(cell(row, 38)), integer(cell(row, 40))
        if key not in ACTION_MODEL_MAPPING and key != ('tenet t7 комфорт +', 2026):
            continue
        known += 1
        if cell(row, 7) in ('#N/A', '#REF!', '#VALUE!', '#DIV/0!', '#NUM!', '#NAME?', '#NULL!', '#ERROR!'):
            raise AppError(f'Начисления, строка {line}: ошибка формулы в H. Исправьте исходный файл.')
        if cell(row, 7) not in ('', None):
            target = ACTION_MODEL_MAPPING.get(key, ('Tenet T7', 2026))
            counts[target] = counts.get(target, 0) + 1
    if not known:
        raise AppError('Акционные водители: AM/AO: нет строк с моделями/годами из справочника RTF.')
    return counts, known


def apply_actions(source, target):
    after = copy.deepcopy(target); counts, known = action_counts(source)
    if not counts:
        # Original exits without changing H when there is no nonempty H at all.
        return after, 0, ['В исходных начислениях нет непустого H у моделей RTF. По оригинальному скрипту H остаётся без изменений.']
    counts = {model_key(*key):value for key,value in counts.items()}
    mapping = target_rows(target)
    if not set(mapping).intersection(counts):
        raise AppError('Акционные водители: ни одна посчитанная модель/год не совпала с целевой таблицей.')
    for key, row in mapping.items():
        set_cell(after, row, 7, counts.get(key, 0))
    return after, sum(counts.values()), []


def normalized_name(value):
    return ' '.join(word.capitalize() for word in str(value).split())


def fleet_day(rows, day):
    start = 11 + (day - 1) * 9
    if len(rows) < 356 or max(map(len, rows), default=0) < start + 9:
        raise AppError(f'Кол_состав: для дня {day} нужны строки до 356 и блок из 9 колонок с колонки {start+1}.')
    def values(row):
        result = {}
        for name, offset in (('total', 0), ('rent', 1), ('idle', 2), ('buyout', 3), ('buyout_idle', 4), ('nv', 6)):
            result[name] = int(numeric(cell(rows[row-1], start+offset), f'Кол_состав, строка {row}, день {day}'))
        raw = cell(rows[row-1], start+7)
        result['percent_after'] = None if raw in ('', None) else numeric(raw, f'Кол_состав, строка {row}, день {day}, процент')
        return result
    result = {}
    for model_row, years in FLEET_MODEL_YEARS.items():
        raw = cell(rows[model_row-1], 1)
        if raw in ('', None):
            raise AppError(f'Кол_состав: в B{model_row} нет модели; структура отличается от RTF.')
        model = FLEET_CAR_MAPPING.get(raw, (raw, ''))[0]
        for year_row in years:
            actual = 190 if model_row == 184 else year_row
            year = integer(cell(rows[actual-1], 1))
            key = normalized_name(model), year
            data = values(actual)
            if (model_row, year_row) in ((24, 31), (174, 181)):
                other = values(21 if model_row == 24 else 171)
                data = {name: data[name] + other[name] for name in ('total', 'rent', 'idle', 'buyout', 'buyout_idle', 'nv')}
                first, second = values(actual)['percent_after'], other['percent_after']
                data['percent_after'] = first + second if first and second else None
                if model_row == 24:
                    key = 'Volkswagen Polo', 2019
            result[key] = data
    return result


def percent(value):
    # RTF sends a one-decimal percent through USER_ENTERED. Store its numeric value.
    return round(float(f'{(value or 0) * 100:.1f}') / 100, 3)


def source_name(value):
    return re.sub(r'\s*\+\s*', '+', normalize_text(value))


def approved_models():
    keys = set(PRICE_MODEL_MAPPING.values()) | set(CURRENT_PRICE_ADDITIONS.values()) | set(ACTION_MODEL_MAPPING.values())
    return {model_key(*key) for key in keys}


def fleet_by_model(rows, day):
    """Resolve source blocks and their years, independently of worksheet row numbers."""
    if not 1 <= day <= 31:
        raise AppError('Кол_состав: неверный день месяца.')
    start = 11 + (day - 1) * 9
    header = rows[1] if len(rows) > 1 else []
    titles = ['всего','аренда','простой аренда','выкуп','выкуп простой','%','простой нв','% нв']
    if [normalize_text(str(cell(header,start+i))) for i in range(8)] != titles:
        raise AppError(f'Кол_состав: за {day}-е число отсутствует или изменён блок колонок всего/аренда/простой/выкуп/НВ.')
    source_map = {source_name(k): model_key(v[0], 0)[0] for k,v in FLEET_CAR_MAPPING.items()}
    source_map[source_name('SKODA OCTAVIA Эконом К')] = model_key('Skoda Octavia K',0)[0]
    source_map[source_name('Skoda OCTAVIA Комфорт К')] = model_key('Skoda Octavia K',0)[0]
    source_map[source_name('Tenet T7 Комфорт +')] = model_key('Tenet T7',0)[0]
    preferred = {}
    for (name,year),target in ACTION_MODEL_MAPPING.items():
        preferred.setdefault(model_key(*target),set()).add(source_name(name))
    preferred[model_key('Tenet T7',2026)] = {source_name('Tenet T7 Комфорт +')}
    approved = approved_models()
    result = {}; block = None; model = None
    for index,row in enumerate(rows):
        text = cell(row,1)
        if isinstance(text,str) and integer(text) is None and text.strip():
            block = source_name(text); model = source_map.get(block)
            continue
        year = integer(text)
        if model is None or year is None or not 1900 <= year <= 2100:
            continue
        key = model, year
        if key not in approved or (key in preferred and block not in preferred[key]):
            continue
        if key in result:
            raise AppError(f'Кол_состав: неоднозначная модель «{model}», {year}; строки {result[key]["source_row"]} и {index+1}.')
        data = {}
        for name,offset in (('total',0),('rent',1),('idle',2),('buyout',3),('buyout_idle',4),('nv',6),('percent_after',7)):
            raw = cell(row,start+offset)
            if raw in ('',None):
                raise AppError(f'Кол_состав: нет данных за {day}-е число: «{model}», {year}, строка {index+1}, поле {name}.')
            value = numeric(raw,f'Кол_состав, {day}-е число, «{model}», {year}, строка {index+1}')
            if value < 0 or (name != 'percent_after' and value != int(value)):
                raise AppError(f'Кол_состав: неверное количество/процент за {day}-е число, «{model}», {year}.')
            data[name] = value if name == 'percent_after' else int(value)
        data['source_row'] = index + 1
        result[key] = data
    if not result:
        raise AppError('Кол_состав: не найдены модели и годы из согласованного справочника.')
    return result


def apply_fleet(current, week, fortnight, target, dates=None):
    after = copy.deepcopy(target); mapping = target_rows(target); matched = 0
    source = []
    for matrix in (current,week,fortnight):
        normalized = {}
        for key,value in matrix.items():
            canonical = model_key(*key)
            if canonical in normalized:
                raise AppError(f'Кол_состав: неоднозначная модель «{key[0]}», {key[1]}.')
            normalized[canonical] = value
        source.append(normalized)
    current,week,fortnight = source
    known_names = {key[0] for key in approved_models()}
    required = {key for key in mapping if key[0] in known_names}
    for number,matrix in enumerate(source):
        for key in sorted(required):
            if key not in matrix:
                moment = dates[number].strftime('%d.%m.%Y') if dates else ('отчётный день','неделю назад','две недели назад')[number]
                raise AppError(f'Кол_состав: нет данных за {moment}: «{key[0]}», {key[1]}. Обновление заблокировано.')
    for key in sorted(required):
        data=current[key];matched+=1;row=mapping[key]
        denominator = data['idle'] + data['rent']
        output = {15:data['total'],16:data['rent'],17:data['buyout'],18:data['buyout_idle'],
                  20:data['idle'],21:percent(data['idle']/denominator if denominator > 0 else 0),22:data['nv'],
                  24:week[key]['nv'],25:percent(week[key]['percent_after']),
                  27:fortnight[key]['nv'],28:percent(fortnight[key]['percent_after'])}
        for column,value in output.items():set_cell(after,row,column,value)
    if not matched:raise AppError('Кол_состав: ни одна модель/год не совпала с целевой таблицей.')
    return after,matched
