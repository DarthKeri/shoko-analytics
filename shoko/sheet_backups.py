"""Reviewed per-sheet safety copies; never rename the active sheet or replace an old copy."""
import copy
from datetime import datetime, timezone, timedelta
from .storage import AppError, digest


def planned_backups(before, changes, operations, moment=None, derived=()):
    if not changes and not operations:
        return []
    renamed = {op['target']: op['source'] for op in operations if op['kind'] == 'rename'}
    touched = {renamed.get(c['sheet'], c['sheet']) for c in changes}
    touched.update(op['source'] for op in operations if op['kind'] == 'rename')
    for expected in derived:
        source = before['sheets'].get(expected['sheet'], {}).get('values', [])
        r, c = expected['row'] - 1, expected['col'] - 1
        old = source[r][c] if r < len(source) and c < len(source[r]) else ''
        if old != expected['value']:
            touched.add(expected['sheet'])
    names = {s['properties']['title'].casefold() for s in before.get('metadata', {}).get('sheets', [])}
    names.update(n.casefold() for n in before['sheets'])
    names.update(op['target'].casefold() for op in operations)
    limit = 31 if before['mode'] == 'local' else 100
    moment = moment or datetime.now(timezone.utc)
    suffix = moment.astimezone(timezone(timedelta(hours=3))).strftime('_%Y%m%d_%H%M%S')
    result = []
    for source in sorted(touched):
        if source not in before['sheets']:
            continue  # A new sheet has no previous version to save.
        base = 'BF_' + source
        name = base[:limit]
        if name.casefold() in names:
            name = base[:limit-len(suffix)] + suffix
            counter = 2
            while name.casefold() in names:
                tail = suffix + '_' + str(counter)
                name = base[:limit-len(tail)] + tail
                counter += 1
        names.add(name.casefold())
        result.append({'source': source, 'target': name})
    return result


def backup_matrix(source, mode):
    matrix = copy.deepcopy(source.get('raw', source['values']))
    if mode == 'google':
        # Freeze old formula results so changes in other live sheets cannot alter
        # the safety copy. Original formulas also survive in the full Drive copy.
        cells = source.get('formula_cells')
        if cells is None:  # Older plan/synthetic snapshot compatibility.
            cells = [(r, c) for r, row in enumerate(matrix) for c, v in enumerate(row)
                     if isinstance(v, str) and v.startswith('=')]
        for r, c in cells:
            matrix[r][c] = source['values'][r][c] if r < len(source['values']) and c < len(source['values'][r]) else ''
    return matrix


def compact_matrix(matrix):
    result = []
    for row in matrix:
        row = [int(v) if type(v) is float and v.is_integer() else v for v in row]
        while row and row[-1] == '':
            row.pop()
        result.append(row)
    while result and not result[-1]:
        result.pop()
    return result


def verify_matrix(actual, expected, name):
    if digest(compact_matrix(actual)) != digest(compact_matrix(expected)):
        raise AppError(f'Ошибка сверки BF-копии «{name}»: содержимое не совпадает с состоянием до записи. Полная резервная копия сохранена.')


def google_backup_requests(plan, used):
    from .adapters import google_value, merged_follower
    result = []
    for item in plan.get('sheet_backups', []):
        source = plan['before']['sheets'][item['source']]
        identity = max(used or {0}) + 1
        used.add(identity)
        result.append({'duplicateSheet': {'sourceSheetId': source['id'], 'newSheetId': identity, 'newSheetName': item['target']}})
        cells = source.get('formula_cells')
        if cells is None:
            cells = [(r, c) for r, row in enumerate(source.get('raw', [])) for c, v in enumerate(row)
                     if isinstance(v, str) and v.startswith('=')]
        groups = {}
        for r, c in cells:
            if any(merged_follower(m, r, c) for m in source.get('merges', [])):
                raise AppError('Формула BF-копии находится внутри объединённой области. Запись не начата.')
            groups.setdefault(r, []).append(c)
        for r, columns in sorted(groups.items()):
            runs = []
            for c in sorted(columns):
                if not runs or c != runs[-1][-1] + 1:
                    runs.append([])
                runs[-1].append(c)
            for run in runs:
                row = source['values'][r] if r < len(source['values']) else []
                result.append({'updateCells': {'start': {'sheetId': identity, 'rowIndex': r, 'columnIndex': run[0]},
                              'rows': [{'values': [{'userEnteredValue': google_value(row[c] if c < len(row) else '')} for c in run]}],
                              'fields': 'userEnteredValue'}})
    return result
