"""Bind current-driver Garage prices to the approved model/year source."""
import math
from .calculation import TARGET, cell, set_cell
from .docx_rules import model_key, target_rows
from .storage import AppError


def _normalized(expression):
    # Same whitespace/case and terminal Cyrillic/Latin K rules as model_key.
    spaced = f'TRIM(REGEXREPLACE(SUBSTITUTE({expression};CHAR(160);" ");"\\s+";" "))'
    return f'REGEXREPLACE(LOWER({spaced});" [кk]$";" k")'


def target_value_formula(column):
    """Look up both keys in Sheets itself; no source or destination row numbers."""
    if column not in ("F", "G", "P", "Q", "U", "W", "X"):
        raise ValueError("Unsupported target column")
    sheet = "'" + TARGET + "'!"
    models = _normalized(sheet + 'B:B')
    current = _normalized('INDEX(B:B;ROW())')
    return (f'=LET(found;FILTER(ROW({sheet}{column}:{column});ARRAYFORMULA({models})={current};'
            f'ARRAYFORMULA(IFERROR(VALUE({sheet}C:C);0))=VALUE(INDEX(C:C;ROW())));'
            f'IF(ROWS(found)<>1;NA();IF(ISBLANK(INDEX({sheet}{column}:{column};INDEX(found;1)));'
            f'"";INDEX({sheet}{column}:{column};INDEX(found;1)))))')


def driver_price_formula():
    return target_value_formula('G')


def bind_driver_prices(source, target, work_name):
    """Mutate only virtual BP formulas, returning independently checked results."""
    mapping = target_rows(target)
    expected = []
    raw = source.setdefault('raw', [row.copy() for row in source['values']])
    positions = source.setdefault('formula_cells', [[r,c] for r,row in enumerate(raw)
        for c,v in enumerate(row) if isinstance(v,str) and v.startswith('=')])
    expression = driver_price_formula()
    for r, row in enumerate(source['values'][1:], 1):
        model, year = cell(row,1), cell(row,2)
        if not model and year in ('',None):
            continue
        key = model_key(model,year)
        address = f'«{work_name}», BP{r+1}'
        if key[1] is None or key not in mapping:
            raise AppError(f'{address}: в «{TARGET}» не найдена модель «{model}», {year}. Обновление заблокировано.')
        price = cell(target[mapping[key]],6)
        if price not in ('',None) and (type(price) not in (int,float) or not math.isfinite(price) or price < 0):
            raise AppError(f'{address}: «{TARGET}», G{mapping[key]+1} — цена должна быть числом или пустой ячейкой.')
        set_cell(raw,r,67,expression)
        if [r,67] not in positions:
            positions.append([r,67])
        expected.append(dict(sheet=work_name,row=r+1,col=68,value='' if price is None else price))
    return expected
