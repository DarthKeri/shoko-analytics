"""Read-only source checks shared by status and preparation."""
from datetime import timedelta
from pathlib import Path
from .storage import AppError
from .adapters import read_excel, read_csv
from .calculation import assemble_prostoi, source_rows, date_columns, number
from .garage import bot_offers, match_garage_rows


def validate_prostoi(rows, processing):
    sources = source_rows(rows)
    positions = date_columns(rows, processing)
    needed = {2, 3, *range(positions[1], positions[1] + 8),
              positions[7] + 6, positions[7] + 7,
              positions[14] + 6, positions[14] + 7}
    for (model, year), row in sources.items():
        for column in sorted(needed):
            if column >= len(row):
                raise AppError(f'{model} / {year}: нет нужного столбца {column + 1} простоев.')
            number(row[column], f'{model} / {year}, столбец {column + 1} простоев')
    return len(sources)


def read_prostoi_source(path, processing):
    sheets = [f'Кол_состав_{processing:%m-%y}']
    if processing.day < 15:
        previous_month = processing.replace(day=1) - timedelta(days=1)
        sheets.append(f'Кол_состав_{previous_month:%m-%y}')
    context = (f'Простои для обработки {processing:%d.%m.%Y} '
               f'(файл «{Path(path).name}», листы: ' + ', '.join(f'«{s}»' for s in sheets) + ')')
    try:
        current = read_excel(path, sheets[0], 373, 345)
        previous = read_excel(path, sheets[1], 373, 345) if len(sheets) > 1 else []
        count = validate_prostoi(assemble_prostoi(current, previous, [], processing), processing)
    except AppError as exc:
        raise AppError(f'{context}: {exc} Проверьте файл простоев и заполнение нужных дней.') from None
    dates = ', '.join((processing - timedelta(days=n)).strftime('%d.%m.%Y') for n in (1, 7, 14))
    detail = f'{context}: блоки моделей и годов, числовые данные и дни {dates} проверены (сочетаний модель/год: {count}).'
    return current, previous, detail


def read_bot_source(path):
    try:
        rows = read_csv(path)
        offers = bot_offers(rows)
        if not offers:
            raise AppError('Нет подходящих предложений: нужны бензин, автомат, тариф Эконом/Комфорт/Комфорт+, '
                           'положительная цена 7/0; Автопорт исключается.')
    except AppError as exc:
        raise AppError(f'CSV из бота «{Path(path).name}»: {exc} Выберите полную выгрузку нужного города.') from None
    return rows


def check_bot_source(path, adapter, rows=None):
    rows = read_bot_source(path) if rows is None else rows
    try:
        name, template = adapter.garage_template()
        matched = match_garage_rows(template, rows)
    except AppError as exc:
        raise AppError(f'CSV из бота «{Path(path).name}»: {exc}') from None
    return (f'CSV «{Path(path).name}»: колонки и данные проверены; строк: {len(rows) - 1}, '
            f'подходящих предложений: {matched["eligible"]}, совпадений с листом «{name}»: {matched["matched"]}.')
