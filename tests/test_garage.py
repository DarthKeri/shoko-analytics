import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from openpyxl import load_workbook
from shoko.adapters import GoogleTarget, changes_for, LocalTarget
from shoko.calculation import TARGET, GARAGE, set_cell
from shoko.garage import prepare_garage, comparison_before, formula, normalize_model, ignored, bot_offers, managed_names
from shoko.storage import AppError, file_hash
from shoko.engine import Engine
from test_mvp import fixtures


def snapshot():
    name = 'Гараж 25.08.2026'
    rows = [['Класс', 'Модель', 'Год'] + [''] * 12 + [f'Конкурент {n}' for n in range(1, 41)] + [''] * 6,
            ['Эконом', 'Volkswagen Polo', 2019], ['Эконом', 'Kia Rio', 2021]]
    for row in rows[1:]:
        row.extend([''] * (61 - len(row)))
        row[15:18] = [999, 1000, 1100]
    raw = copy.deepcopy(rows)
    for r in (1, 2):
        for c, f in {12: f'=AVERAGE(P{r+1}:AY{r+1})', 14: f'=COUNTA(P{r+1}:BC{r+1})', 57: f'=MIN(P{r+1}:BC{r+1})', 58: f'=AVERAGE(BE{r+1};BG{r+1})', 59: f'=AVERAGE(P{r+1}:BC{r+1})', 60: f'=AVERAGE(BI{r+1};BG{r+1})', 61: f'=MAX(P{r+1}:BC{r+1})'}.items():
            raw[r][c-1] = f
            rows[r][c-1] = 1000
    proposal = [['Класс', 'Модель', 'Год'], ['Эконом', 'Polo', 2019], ['Эконом', 'Kia Rio', 2021]]
    raw_proposal = copy.deepcopy(proposal)
    for r in (1, 2):
        for c, letter in {10:'N',11:'BE',12:'BF',13:'L',14:'BH',15:'BI'}.items():
            set_cell(raw_proposal, r, c-1, f"='{name}'!{letter}{r+1}")
            set_cell(proposal, r, c-1, 1000)
    sheets = {name: dict(id=7, rows=34, cols=150, values=rows, raw=raw, merges=[]), TARGET: dict(id=9, rows=100, cols=31, values=proposal, raw=raw_proposal, merges=[])}
    return dict(mode='google', sheets=sheets, metadata={'sheets':[{'properties':{'sheetId':v['id'], 'title':k}} for k,v in sheets.items()]})


def csv_rows(price='1500', model='Volkswagen Polo', year='2019', klass='эконом'):
    return [['Марка и модель','Тариф','Год выпуска','Цена аренды 7 / 0','Тип топлива','КПП','Таксопарк'], [model,klass,year,price,'бензин','автомат','Тестовый парк']]


def plan_for(before=None, rows=None, processing=date(2026,10,14)):
    before = before or snapshot()
    after = {TARGET:copy.deepcopy(before['sheets'][TARGET]['values'])}
    info = prepare_garage(before, after, rows or csv_rows(), processing)
    plan = dict(before=before, after=after, **info)
    plan['changes'] = changes_for(comparison_before(plan), after)
    return plan


class GarageTests(unittest.TestCase):
    def test_archive_is_values_before_update_and_source_is_not_mutated(self):
        before=snapshot(); original=copy.deepcopy(before); plan=plan_for(before)
        self.assertEqual(before,original)
        self.assertEqual([op['kind'] for op in plan['operations']],['archive','rename'])
        archived=plan['after'][plan['operations'][0]['target']]
        self.assertEqual(archived,before['sheets']['Гараж 25.08.2026']['values'])
        self.assertFalse(any(isinstance(v,dict) for row in archived for v in row))
        self.assertEqual(plan['after']['Гараж 14.10.2026'][1][15:18],[1500.0,'',''])
        self.assertEqual(plan['after']['Гараж 14.10.2026'][2][15:55],['']*40)

    def test_all_no_matches_blocks_before_any_write(self):
        before=snapshot(); original=copy.deepcopy(before)
        with self.assertRaisesRegex(AppError,'ни одного совпадения'):
            plan_for(before,csv_rows(model='Toyota Vitz'))
        self.assertEqual(before,original)

    def test_filters_ignore_own_park_gas_manual_other_class(self):
        for index,value in ((4,'газ'),(5,'механика'),(6,'Автопорт'),(1,'бизнес')):
            data=csv_rows();data[1][index]=value
            self.assertEqual(bot_offers(data),[])

    def test_invalid_required_headers_and_prices_block(self):
        for price in ('бесплатно','-100','1500-2000','1 500,500'):
            with self.subTest(price=price),self.assertRaises(AppError):bot_offers(csv_rows(price))
        self.assertEqual(bot_offers(csv_rows('1 500,50 ₽'))[0][1],1500.5)
        with self.assertRaises(AppError):bot_offers([['model'],['Polo']])

    def test_model_normalization_and_real_exclusions(self):
        for text in ('Volkswagen Polo K','Volkswagen Polo К','Polo К'):
            self.assertEqual(normalize_model(text),'polo')
        self.assertTrue(ignored(('эконом',normalize_model('Kia Ceed'),'2018')))
        self.assertTrue(ignored(('комфорт+',normalize_model('Faw Bestune T77'),'2023')))

    def test_cheapest_40_duplicates_preserved_tail_cleared(self):
        data=csv_rows();data[1:]=[csv_rows(str(p))[1] for p in range(1600,1540,-1)]
        data.append(csv_rows('1500')[1]);data.append(csv_rows('1500')[1])
        plan=plan_for(rows=data)
        prices=plan['after']['Гараж 14.10.2026'][1][15:55]
        self.assertEqual(prices[:2],[1500,1500]);self.assertEqual(len(prices),40)
        self.assertEqual(prices,sorted(prices))

    def test_guarded_links_clear_prices_but_count_zero(self):
        plan=plan_for()
        expected={(e['row'],e['col']):e['value'] for e in plan['derived']}
        self.assertEqual(expected[(3,10)],0)
        self.assertEqual(expected[(3,11)],'')
        self.assertEqual(expected[(2,11)],1500)
        self.assertIn('COUNT(',plan['after'][TARGET][2][10]['formula'])

    def test_duplicate_keys_past_date_and_bad_formula_block(self):
        b=snapshot();b['sheets']['Гараж 25.08.2026']['values'].append(copy.deepcopy(b['sheets']['Гараж 25.08.2026']['values'][1]))
        with self.assertRaisesRegex(AppError,'Неоднозначные'):plan_for(b)
        with self.assertRaisesRegex(AppError,'раньше'):plan_for(processing=date(2026,8,24))
        b=snapshot();b['sheets']['Гараж 25.08.2026']['raw'][1][56]='=UNKNOWN()'
        with self.assertRaisesRegex(AppError,'Неподдерживаемая'):plan_for(b)

    def test_archive_collision_does_not_overwrite_history(self):
        b=snapshot();b['metadata']['sheets'].append({'properties':{'sheetId':10,'title':'Гараж 25/08/2026 (архив)'}})
        self.assertEqual(plan_for(b)['operations'][0]['target'],'Гараж 25/08/2026 (архив 2)')

    def test_same_date_has_no_archive_and_repeated_plan_has_no_changes(self):
        b=snapshot();plan=plan_for(b,processing=date(2026,8,25))
        self.assertEqual(plan['operations'],[])
        for name, rows in plan['after'].items():
            b['sheets'][name]['raw']=[[v['formula'] if isinstance(v,dict) else v for v in row] for row in rows]
            b['sheets'][name]['values']=copy.deepcopy(b['sheets'][name]['raw'])
        # Google calculates the formula-backed model and summary cells.
        for name in plan['after']:
            if name!=TARGET:
                b['sheets'][name]['values'][1][:3]=['Эконом','Volkswagen Polo',2019]
                b['sheets'][name]['values'][2][:3]=['Эконом','Kia Rio',2021]
        for e in plan['derived']:set_cell(b['sheets'][TARGET]['values'],e['row']-1,e['col']-1,e['value'])
        again=plan_for(b,processing=date(2026,8,25))
        self.assertEqual(again['changes'],[])

    def test_google_batch_archives_freezes_renames_and_keeps_csv_text_safe(self):
        plan=plan_for(); adapter=object.__new__(GoogleTarget)
        payload=json.loads(adapter.prepare_write(plan))
        self.assertIn('duplicateSheet',payload['requests'][0])
        self.assertIn('start',payload['requests'][1]['updateCells'])
        rename=next(req for req in payload['requests'] if 'updateSheetProperties' in req)
        self.assertEqual(rename['updateSheetProperties']['properties']['sheetId'],7)
        cells=[c for req in payload['requests'] if 'updateCells' in req for row in req['updateCells']['rows'] for c in row['values']]
        self.assertTrue(any('formulaValue' in c.get('userEnteredValue',{}) for c in cells))
        with patch.object(adapter,'request') as request:
            adapter.write(plan,prepared=adapter.prepare_write(plan))
        self.assertEqual(request.call_count,1)

    def test_dynamic_snapshot_reads_working_garage_not_entire_history(self):
        names=['Простои',TARGET,'Гараж 25.08.2026','Гараж 08.04.2026','Гараж 25/08/2026 (архив)']
        self.assertEqual(managed_names(names),['Простои',TARGET,'Гараж 25.08.2026'])

    def test_literal_formula_like_template_cell_is_never_promoted_to_formula(self):
        b=snapshot();source=b['sheets']['Гараж 25.08.2026']
        source['formula_cells']=[[r,c] for r,row in enumerate(source['raw']) for c,v in enumerate(row) if isinstance(v,str) and v.startswith('=')]
        source['raw'][1][7]='=IMPORTXML("untrusted")';source['values'][1][7]='=IMPORTXML("untrusted")'
        plan=plan_for(b)
        self.assertEqual(plan['after']['Гараж 14.10.2026'][1][7],'=IMPORTXML("untrusted")')

    def test_google_empty_formula_result_is_blank_not_formula_text(self):
        adapter=object.__new__(GoogleTarget);adapter.id='test-copy'
        properties={'sheetId':1,'title':TARGET,'gridProperties':{'rowCount':2,'columnCount':2}}
        with patch.object(adapter,'check',return_value={'sheets':[{'properties':properties}]}),patch.object(adapter,'request',return_value={'sheets':[{'properties':properties,'data':[{'rowData':[{'values':[{'userEnteredValue':{'formulaValue':'=IF(1;"";2)'}}]}]}]}]}):
            result=adapter.snapshot()
        self.assertEqual(result['sheets'][TARGET]['values'],[])
        self.assertEqual(result['sheets'][TARGET]['raw'],[['=IF(1;"";2)']])
        self.assertEqual(result['sheets'][TARGET]['formula_cells'],[[0,0]])

    def verified_result(self, plan):
        result=copy.deepcopy(comparison_before(plan))
        for name,rows in plan['after'].items():
            result['sheets'][name]=dict(copy.deepcopy(result['sheets'][name]),raw=[[v['formula'] if isinstance(v,dict) else v for v in row] for row in rows],values=copy.deepcopy(rows))
        for expected in plan['derived']:
            set_cell(result['sheets'][TARGET]['values'],expected['row']-1,expected['col']-1,expected['value'])
        return result

    def test_verification_checks_archived_values_and_calculated_results(self):
        plan=plan_for();adapter=object.__new__(GoogleTarget);result=self.verified_result(plan)
        with patch.object(adapter,'snapshot',return_value=result):adapter.verify(plan)
        archive=plan['operations'][0]['target'];result['sheets'][archive]['raw'][1][15]=0
        with patch.object(adapter,'snapshot',return_value=result),self.assertRaisesRegex(AppError,'архив Гаража'):
            adapter.verify(plan)

    def test_verification_waits_for_recalculation_without_replaying_write(self):
        plan=plan_for();adapter=object.__new__(GoogleTarget);good=self.verified_result(plan);bad=copy.deepcopy(good)
        bad['sheets'][TARGET]['values'][1][10]=999
        with patch.object(adapter,'snapshot',side_effect=[bad,good]) as read,patch('shoko.adapters.time.sleep'),patch.object(adapter,'request') as write:
            adapter.verify(plan);self.assertEqual(read.call_count,2);write.assert_not_called()
        with patch.object(adapter,'snapshot',return_value=bad),patch('shoko.adapters.time.sleep'),self.assertRaisesRegex(AppError,'пересчёта Гаража'):
            adapter.verify(plan)

    def test_special_cross_class_matches_and_no_offer_clears_exclusions(self):
        b=snapshot();b['sheets']['Гараж 25.08.2026']['values'][2][:3]=['Комфорт+','JAC J7',2022]
        b['sheets']['Гараж 25.08.2026']['raw'][2][:3]=['Комфорт+','JAC J7',2022]
        b['sheets'][TARGET]['values'][2][:3]=['Комфорт +','JAC J7',2022]
        b['sheets'][TARGET]['raw'][2][:3]=['Комфорт +','JAC J7',2022]
        data=csv_rows();data.append(csv_rows(model='JAC J7',year='2022',klass='комфорт')[1])
        self.assertEqual(plan_for(b,data)['garage']['matched'],2)

    def test_csv_formula_stays_text_and_only_explicit_formula_tag_writes_formula(self):
        b=snapshot();after={TARGET:copy.deepcopy(b['sheets'][TARGET]['values']),GARAGE:[['=IMPORTXML("x")']]}
        plan=dict(before=b,after=after,changes=changes_for(b,after))
        adapter=object.__new__(GoogleTarget);payload=json.loads(adapter.prepare_write(plan))
        cells=[c for req in payload['requests'] if 'updateCells' in req for row in req['updateCells']['rows'] for c in row['values']]
        self.assertIn({'userEnteredValue':{'stringValue':'=IMPORTXML("x")'}},cells)

    def test_archive_freezes_existing_errors_as_text_and_preserves_merge_coordinates(self):
        b=snapshot();b['sheets']['Гараж 25.08.2026']['values'][2][11]='#DIV/0!'
        b['sheets']['Гараж 25.08.2026']['merges']=[dict(sheetId=7,startRowIndex=0,endRowIndex=1,startColumnIndex=3,endColumnIndex=5)]
        plan=plan_for(b);self.assertEqual(plan['garage']['archived_errors'],1)
        result=self.verified_result(plan);name=plan['operations'][0]['target']
        result['sheets'][name]['merges'][0]['sheetId']=99
        adapter=object.__new__(GoogleTarget)
        with patch.object(adapter,'snapshot',return_value=result):adapter.verify(plan)
        payload=json.loads(adapter.prepare_write(plan))
        first_updates=[req['updateCells'] for req in payload['requests'] if 'updateCells' in req and req['updateCells']['start']['sheetId']==10]
        cells=[v for update in first_updates for row in update['rows'] for v in row['values']]
        self.assertIn({'userEnteredValue':{'stringValue':'#DIV/0!'}},cells)


class GarageEngineTests(unittest.TestCase):
    def test_no_matches_changes_neither_target_nor_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=fixtures(Path(folder)/'files');Path(paths['csv']).write_text(';'.join(csv_rows()[0])+'\n'+';'.join(csv_rows(model='Toyota Vitz')[1]),encoding='utf-8')
            engine=Engine(Path(folder)/'app')
            try:
                engine.save_settings(paths);before=file_hash(paths['target'])
                engine.prepare('https://garage.example.test/a','2026-08-20',True);engine.worker.join(20)
                self.assertEqual(engine.state['phase'],'error');self.assertIn('ни одного совпадения',engine.state['error'])
                self.assertIsNone(engine.plan);self.assertEqual(file_hash(paths['target']),before);self.assertEqual(engine.store.copies(),[])
            finally:engine.close()

    def test_noop_write_is_blocked_without_new_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=fixtures(Path(folder)/'files');engine=Engine(Path(folder)/'app')
            try:
                engine.save_settings(paths)
                for attempt in range(2):
                    engine.prepare('https://garage.example.test/a','2026-08-20',True);engine.worker.join(20)
                    self.assertEqual(engine.state['phase'],'preview',engine.state['error']);plan=engine.plan
                    if attempt==0:
                        engine.write(plan['id'],plan['checksum']);engine.worker.join(20)
                        self.assertEqual(engine.state['phase'],'done',engine.state['error']);copies=len(engine.store.copies())
                    else:
                        self.assertEqual(plan['changes'],[])
                        with self.assertRaisesRegex(AppError,'Изменений нет'):engine.write(plan['id'],plan['checksum'])
                        self.assertEqual(len(engine.store.copies()),copies)
            finally:engine.close()
