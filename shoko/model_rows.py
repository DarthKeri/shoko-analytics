"""Reviewed creation of two agreed model rows; existing row numbers stay stable."""
import copy
from .calculation import TARGET, GARAGE, cell, set_cell
from .docx_rules import model_key, target_rows, price_values, ACTION_MODEL_MAPPING, normalize_text
from .garage import latest_garage, car_key, formula, bot_offers
from .storage import AppError

POLO = model_key('Volkswagen Polo K',2019)
TENET = model_key('Tenet T7',2026)
OCTAVIA = model_key('Skoda Octavia K',2019)
MODELS = {POLO:('Эконом','Volkswagen Polo K',2019), TENET:('Комфорт +','Tenet T7',2026)}


def requested_models(sources, garage_csv=None):
    keys=set()
    if 'prices' in sources:keys.update(model_key(*k) for k in price_values(sources['prices']))
    if 'fleet' in sources:keys.update(model_key(*k) for k in sources['fleet'][0])
    if 'actions' in sources:
        for row in sources['actions'][1:]:
            key=normalize_text(cell(row,38)), cell(row,40)
            if key in ACTION_MODEL_MAPPING:keys.add(model_key(*ACTION_MODEL_MAPPING[key]))
            if key==('tenet t7 комфорт +',2026):keys.add(TENET)
    if garage_csv and any(key==('комфорт+','tenet t7','2026') for key,price in bot_offers(garage_csv)):
        keys.add(TENET)
    return keys & set(MODELS)


def ensure_target_rows(before, rows, wanted):
    result=copy.deepcopy(rows);existing=target_rows(rows);created=[];formats=[]
    templates={POLO:model_key('Volkswagen Polo K',2018),TENET:model_key('Chery Tiggo 7 Pro Max',2024)}
    for key in (POLO,TENET):
        if key not in wanted or key in existing:continue
        klass,model,year=MODELS[key]
        index=max(len(result),len(before['sheets'][TARGET].get('raw',[])))
        result.extend([[] for _ in range(index+1-len(result))])
        for c,value in enumerate((klass,model,year)):set_cell(result,index,c,value)
        n=index+1
        set_cell(result,index,8,formula(f'=IF(COUNT(Q{n};H{n})<2;"";Q{n}-H{n})'))
        set_cell(result,index,23,formula(f'=IF(COUNT(Q{n};U{n};W{n})<3;"";IF(Q{n}+U{n}=0;0;W{n}/(Q{n}+U{n})))'))
        source=existing.get(templates[key],next(iter(existing.values()),None))
        if source is not None:formats.append(dict(sheet=TARGET,source_row=source+1,row=n,cols=31))
        created.append(dict(sheet=TARGET,row=n,model=model,year=year))
        existing[key]=index
    return result,created,formats


def prepare_garage_rows(before, after, processing):
    virtual=copy.deepcopy(before)
    source_name=latest_garage([s['properties']['title'] for s in before['metadata']['sheets']])
    if source_name not in before['sheets']:raise AppError('Не найден текущий лист Гаража.')
    work_name=f'Гараж {processing:%d.%m.%Y}'
    source=virtual['sheets'][source_name];rows=source['values'];raw=source.setdefault('raw',copy.deepcopy(rows))
    target=after[TARGET];mapping=target_rows(target);created=[];formats=[]
    keys={car_key(row):i for i,row in enumerate(rows) if car_key(row)}
    tenet_key=('комфорт+','tenet t7','2026')
    if TENET in mapping and tenet_key not in keys:
        n=mapping[TENET]+1;m=max(len(rows),len(raw))+1
        base=['Комфорт+','Tenet T7',2026]
        for matrix in (rows,raw):matrix.extend([[] for _ in range(m-len(matrix))]);matrix[m-1]=base.copy()
        expressions={'D':f'=IF(\'Предложение цен\'!F{n}="";"";\'Предложение цен\'!F{n})',
            'E':f'=IF(\'Предложение цен\'!U{n}="";"";\'Предложение цен\'!U{n})',
            'F':f'=IF(\'Предложение цен\'!W{n}="";"";\'Предложение цен\'!W{n})',
            'G':f'=IF(\'Предложение цен\'!X{n}="";"";\'Предложение цен\'!X{n})',
            'J':f'=MIN(P{m}:BC{m})','K':f'=J{m}-D{m}',
            'L':f'=IFERROR(AVERAGE(P{m}:BC{m});"-")','M':f'=IFERROR(L{m}-D{m};"-")',
            'N':f'=COUNTA(P{m}:BC{m})','BE':f'=MIN(P{m}:BC{m})',
            'BF':f'=IFERROR(AVERAGE(BE{m};BG{m});"-")','BG':f'=IFERROR(AVERAGE(P{m}:BC{m});"-")',
            'BH':f'=IFERROR(AVERAGE(BI{m};BG{m});"-")','BI':f'=MAX(P{m}:BC{m})',
            'BJ':f'=COUNTIF(P{m}:BC{m};"<"&BF{m})','BK':f'=N{m}-(BJ{m}+BL{m})',
            'BL':f'=COUNTIF(P{m}:BC{m};">="&BH{m})','BN':f'=D{m}',
            'BO':f'=IF(COUNT(P{m}:BC{m})=0;"-";IF(BN{m}<=BF{m};"Ниже среднего";IF(BN{m}>=BH{m};"Выше среднего";"Средняя")))'}
        from openpyxl.utils import column_index_from_string
        formula_cells=source.setdefault('formula_cells',[[r,c] for r,row in enumerate(raw[:-1]) for c,v in enumerate(row) if isinstance(v,str) and v.startswith('=')])
        for column,text in expressions.items():
            c=column_index_from_string(column)-1;set_cell(raw,m-1,c,text);formula_cells.append([m-1,c])
        templates=[i for i,row in enumerate(raw[:-1]) if cell(row,56) and cell(row,58)]
        if templates:formats.append(dict(sheet=work_name,source_row=templates[-1]+1,row=m,cols=67))
        created.append(dict(sheet=work_name,row=m,model='Tenet T7',year=2026))
        keys[tenet_key]=m-1
    virtual_target=virtual['sheets'][TARGET]
    virtual_target['values']=copy.deepcopy(target)
    # New formula results are calculated by Google, never fabricated here.
    for row in virtual_target['values']:
        for i,value in enumerate(row):
            if isinstance(value,dict):row[i]=''
    target_raw=virtual_target.setdefault('raw',copy.deepcopy(virtual_target['values']))
    target_formula_cells=virtual_target.setdefault('formula_cells',[[r,c] for r,row in enumerate(target_raw) for c,v in enumerate(row) if isinstance(v,str) and v.startswith('=')])
    for key in (POLO,OCTAVIA,TENET):
        if key not in mapping:continue
        r=mapping[key];lookup=car_key(target[r]);garage_row=keys.get(lookup)
        if garage_row is None:raise AppError(f'Для «{target[r][1]}», {key[1]} не найдена строка Гаража для цен конкурентов.')
        m=garage_row+1
        for column,source_col in ((10,'N'),(11,'BE'),(12,'BF'),(13,'BG'),(14,'BH'),(15,'BI')):
            old=cell(target_raw[r] if r<len(target_raw) else [],column-1)
            if old not in ('',None):continue
            def text(sheet):
                ref=f"'{sheet}'!{source_col}{m}"
                return '='+ref if column==10 else f'=IF(COUNT(\'{sheet}\'!P{m}:BC{m})=0;"-";{ref})'
            set_cell(target,r,column-1,formula(text(work_name)))
            set_cell(target_raw,r,column-1,text(source_name))
            target_formula_cells.append([r,column-1])
    return virtual,created,formats
