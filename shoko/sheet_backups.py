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


class SheetOrder:
    """Simulate sequential insertions, including shifts caused by earlier copies."""
    def __init__(self, before):
        metadata = before.get('metadata', {}).get('sheets', [])
        ordered = sorted(enumerate(metadata), key=lambda pair: pair[1]['properties'].get('index', pair[0]))
        self.names = [item['properties']['title'] for _, item in ordered] or list(before['sheets'])

    def insert_copy(self, source, target):
        if source not in self.names or target in self.names:
            raise AppError('Не удалось разместить копию рядом с исходным листом. Подготовьте новый план.')
        index = self.names.index(source) + 1
        self.names.insert(index, target)
        return index

    def rename(self, source, target):
        self.names[self.names.index(source)] = target


def google_backup_requests(plan, used, order=None):
    from .adapters import google_value, merged_follower
    result = []
    order = order or SheetOrder(plan['before'])
    for item in plan.get('sheet_backups', []):
        source = plan['before']['sheets'][item['source']]
        identity = max(used or {0}) + 1
        used.add(identity)
        result.append({'duplicateSheet': {'sourceSheetId': source['id'], 'newSheetId': identity, 'newSheetName': item['target'],
                                          'insertSheetIndex': order.insert_copy(item['source'], item['target'])}})
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


# A high-priority rule makes the marks visible over copied conditional formats.
# It only affects BF copies, retaining all underlying source formatting.
HIGHLIGHT_BACKGROUND = 'FFF2CC'
HIGHLIGHT_FOREGROUND = '1F1F1F'


def _same_value(a,b):
    if a is None:a=''
    if b is None:b=''
    if type(a) in (int,float) and type(b) in (int,float):return a==b
    return type(a)==type(b) and a==b


def highlight_ranges(plan, source_name, result=None):
    """Exact old-grid cells written or recalculated; rectangles never fill holes."""
    source=plan['before']['sheets'][source_name]
    renamed={op['source']:op['target'] for op in plan.get('operations',[]) if op['kind']=='rename'}
    active_name=renamed.get(source_name,source_name)
    direct={}
    for change in plan['changes']:
        if change['sheet']==active_name:
            direct.setdefault(change['row']-1,set()).add(change['col']-1)
    old=source['values']
    def value(matrix,r,c):return matrix[r][c] if r<len(matrix) and c<len(matrix[r]) else ''
    for expected in plan.get('derived',[]):
        r,c=expected['row']-1,expected['col']-1
        if expected['sheet']==active_name and not _same_value(value(old,r,c),expected['value']):
            direct.setdefault(r,set()).add(c)
    new=(result or {}).get('sheets',{}).get(active_name,{}).get('values')
    row_limit=source['rows'];col_limit=source['cols']
    height=min(row_limit,max(len(old),len(new or []),max(direct,default=-1)+1))
    rectangles=[];last={}
    for r in range(height):
        columns={c for c in direct.get(r,()) if 0<=c<col_limit}
        if new is not None:
            width=min(col_limit,max(len(old[r]) if r<len(old) else 0,len(new[r]) if r<len(new) else 0))
            columns.update(c for c in range(width) if not _same_value(value(old,r,c),value(new,r,c)))
        runs=[]
        for c in sorted(columns):
            if runs and runs[-1][1]==c:runs[-1][1]=c+1
            else:runs.append([c,c+1])
        for start,end in runs:
            key=start,end
            previous=last.get(key)
            if previous is not None and rectangles[previous]['endRowIndex']==r:
                rectangles[previous]['endRowIndex']=r+1
            else:
                last[key]=len(rectangles)
                rectangles.append(dict(startRowIndex=r,endRowIndex=r+1,startColumnIndex=start,endColumnIndex=end))
    return rectangles


def _rgb(color):
    return {name:int(color[i:i+2],16)/255 for name,i in (('red',0),('green',2),('blue',4))}


def highlight_rule(ranges,identity):
    return dict(ranges=[dict(area,sheetId=identity) for area in ranges],
                booleanRule=dict(condition=dict(type='CUSTOM_FORMULA',values=[dict(userEnteredValue='=TRUE')]),
                                 format=dict(backgroundColor=_rgb(HIGHLIGHT_BACKGROUND),textFormat=dict(foregroundColor=_rgb(HIGHLIGHT_FOREGROUND)))))


def verify_highlight_rule(actual, expected, name):
    def areas(rule):
        return sorted(tuple(area.get(k,0) for k in ('sheetId','startRowIndex','endRowIndex','startColumnIndex','endColumnIndex')) for area in rule.get('ranges',[]))
    okay=areas(actual)==areas(expected)
    a=actual.get('booleanRule',{});e=expected['booleanRule']
    okay=okay and a.get('condition')==e['condition']
    for key in ('backgroundColor','foregroundColor'):
        style=a.get('format',{});wanted=e['format']
        if key=='foregroundColor':style=style.get('textFormat',{});wanted=wanted['textFormat']
        color=style.get(key,style.get(key+'Style',{}).get('rgbColor',{}))
        okay=okay and all(abs(color.get(k,0)-v)<1e-6 for k,v in wanted[key].items())
    if not okay:
        raise AppError(f'Значения записаны и сверены, но подсветка BF-копии «{name}» не прошла проверку. Резервная копия сохранена.')
