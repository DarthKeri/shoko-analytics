"""Bounded, ordered Sheets batches; every reviewed cell is sent exactly once."""
import json
from .storage import AppError

# Google's recommended payload size, measured before transport compression.
MAX_GOOGLE_WRITE_BYTES = 2_000_000
GOOGLE_WRITE_INTERVAL = 1.2
PREFIX = b'{"requests":['
SUFFIX = b'],"includeSpreadsheetInResponse":false}'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def bounded_requests(request, capacity):
    payload = encoded(request)
    if len(payload) <= capacity:
        yield payload
        return
    update = request.get('updateCells', {})
    rows = update.get('rows', [])
    start = update.get('start')
    if start and len(rows) > 1:
        middle = len(rows) // 2
        for offset, subset in ((0, rows[:middle]), (middle, rows[middle:])):
            position = dict(start, rowIndex=start.get('rowIndex', 0) + offset)
            yield from bounded_requests({'updateCells': dict(update, start=position, rows=subset)}, capacity)
        return
    if start and len(rows) == 1 and len(rows[0].get('values', [])) > 1:
        values = rows[0]['values']
        middle = len(values) // 2
        for offset, subset in ((0, values[:middle]), (middle, values[middle:])):
            position = dict(start, columnIndex=start.get('columnIndex', 0) + offset)
            row = dict(rows[0], values=subset)
            yield from bounded_requests({'updateCells': dict(update, start=position, rows=[row])}, capacity)
        return
    raise AppError('Одна ячейка или операция превышает допустимый размер части записи (2 МБ). '
                   'Запись не начата; проверьте содержимое исходных данных.')


def prepare_batches(requests):
    capacity = MAX_GOOGLE_WRITE_BYTES - len(PREFIX) - len(SUFFIX)
    batches, current, size = [], [], 0
    for request in requests:
        for payload in bounded_requests(request, capacity):
            added = len(payload) + bool(current)
            if current and size + added > capacity:
                batches.append(PREFIX + b','.join(current) + SUFFIX)
                current, size = [], 0
            size += len(payload) + bool(current)
            current.append(payload)
    if current:
        batches.append(PREFIX + b','.join(current) + SUFFIX)
    # Small plans keep the existing single-payload adapter contract.
    return batches[0] if len(batches) == 1 else tuple(batches) if batches else None


def write_batches(prepared):
    return (prepared,) if isinstance(prepared, bytes) else tuple(prepared or ())


def batch_summary(prepared):
    parts = write_batches(prepared)
    return {'parts': len(parts), 'bytes': sum(map(len, parts))}
