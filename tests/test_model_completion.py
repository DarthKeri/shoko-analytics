"""Agreed model completion, source structure changes and reviewed row creation."""
import copy
import unittest
from datetime import date
from unittest.mock import patch
from shoko.docx_rules import fleet_by_model,apply_fleet,model_key,apply_actions
from shoko.model_rows import ensure_target_rows,prepare_garage_rows,requested_models,POLO,TENET
from shoko.garage import prepare_garage,comparison_before
from shoko.adapters import changes_for,GoogleTarget
from shoko.calculation import TARGET,set_cell,cell
from shoko.storage import AppError
from test_garage import snapshot,csv_rows


def source_rows(shift=0):
    rows=[[] for _ in range(420+shift)]
    for day in (18,11,4):
        start=11+(day-1)*9
        for c,title in enumerate(('всего','аренда','простой аренда','выкуп','выкуп простой','%','простой НВ','% НВ')):set_cell(rows,1,start+c,title)
    blocks=[(15,'Volkswagen Polo Эконом',2019,(28,9,10,9,0,.5,1,.052)),
            (25,'Volkswagen Polo Эконом К',2019,(63,42,9,12,0,.176,1,.0196)),
            (165,'Skoda Octavia Эконом',2019,(12,1,4,7,0,.8,0,0)),
            (175,'SKODA OCTAVIA Эконом К',2019,(249,128,32,88,1,.2,13,.08125)),
            (365,'Tenet T7 Комфорт +',2026,(19,17,1,1,0,.0556,0,0))]
    for n,name,year,values in blocks:
        set_cell(rows,n-1+shift,1,name);set_cell(rows,n+8+shift,1,year)
        for day in (18,11,4):
            for c,value in enumerate(values):set_cell(rows,n+8+shift,11+(day-1)*9+c,value)
    return rows


class DynamicFleetTests(unittest.TestCase):
    def test_row_shift_and_tenet_below_356_do_not_change_values(self):
        before=fleet_by_model(source_rows(),18);shifted=fleet_by_model(source_rows(17),18)
        for key,value in before.items():
            self.assertEqual({k:v for k,v in value.items() if k!='source_row'}, {k:v for k,v in shifted[key].items() if k!='source_row'})
        self.assertGreater(before[TENET]['source_row'],356)
        self.assertEqual(before[TENET]['total'],19)

    def test_separate_polo_octavia_and_three_periods(self):
        current=fleet_by_model(source_rows(),18);week=copy.deepcopy(current);fortnight=copy.deepcopy(current)
        week[POLO]['nv']=2;week[POLO]['percent_after']=.04;fortnight[POLO]['nv']=3
        target=[[],['Эконом','Volkswagen Polo',2019],['Эконом','Volkswagen Polo K',2019],['Эконом','Skoda Octavia',2019],['Эконом','Skoda Octavia К',2019],['Комфорт +','Tenet T7',2026]]
        after,count=apply_fleet(current,week,fortnight,target)
        self.assertEqual(count,5);self.assertEqual([row[15] for row in after[1:]],[28,63,12,249,19])
        self.assertEqual(after[2][24:26],[2,.04]);self.assertEqual(after[2][27],3)
        self.assertEqual(after[1][24],1)

    def test_missing_year_or_historical_value_blocks_with_exact_date(self):
        current=fleet_by_model(source_rows(),18);week=copy.deepcopy(current);week.pop(POLO)
        target=[[],['Эконом','Volkswagen Polo K',2019]]
        with self.assertRaisesRegex(AppError,'11.10.2026.*2019'):
            apply_fleet(current,week,current,target,dates=[date(2026,10,18),date(2026,10,11),date(2026,10,4)])
        target[1][2]=2017
        with self.assertRaisesRegex(AppError,'2017'):apply_fleet(current,current,current,target)

    def test_duplicate_year_and_duplicate_block_fail_even_if_zero(self):
        rows=source_rows();rows[25]=copy.deepcopy(rows[23])
        # Same year, within the same ordinary Polo block.
        rows[23][1]=2019;rows[22]=copy.deepcopy(rows[23])
        with self.assertRaisesRegex(AppError,'неоднозначная'):fleet_by_model(rows,18)
        rows=source_rows();rows.extend(copy.deepcopy(rows[14:24]))
        with self.assertRaisesRegex(AppError,'неоднозначная'):fleet_by_model(rows,18)

    def test_zero_is_valid_but_empty_data_and_formula_errors_fail(self):
        rows=source_rows();r=373;c=11+(18-1)*9
        rows[r][c]=0
        self.assertEqual(fleet_by_model(rows,18)[TENET]['total'],0)
        for value in ('',None,'#Нет сохранённого результата формулы','#N/A',-1,1.5):
            rows=source_rows();rows[r][c]=value
            with self.subTest(value=value),self.assertRaises(AppError):fleet_by_model(rows,18)

    def test_header_change_and_missing_day_are_clear_errors(self):
        rows=source_rows();set_cell(rows,1,11+(18-1)*9,'не всего')
        with self.assertRaisesRegex(AppError,'18-е число'):fleet_by_model(rows,18)
        with self.assertRaisesRegex(AppError,'19-е число'):fleet_by_model(source_rows(),19)

    def test_target_duplicate_with_latin_and_cyrillic_K_is_ambiguous(self):
        data=fleet_by_model(source_rows(),18)
        with self.assertRaisesRegex(AppError,'неоднозначная'):
            apply_fleet(data,data,data,[[],['Эконом','Skoda Octavia K',2019],['Эконом','Skoda Octavia К',2019]])

    def test_actions_recognize_tenet_and_cyrillic_target_variant(self):
        header=['']*41;header[7]='Скидка/наценка';header[38]='люкс';header[40]='год ПТС'
        rows=[header]
        for model,year,flag in [('Tenet T7 Комфорт +',2026,-100),('Skoda Octavia Эконом К',2019,0),('Volkswagen Polo Эконом К',2019,100)]:
            row=['']*41;row[7]=flag;row[38]=model;row[40]=year;rows.append(row)
        target=[[],['Комфорт +','Tenet T7',2026],['Эконом','Skoda Octavia К',2019],['Эконом','Volkswagen Polo K',2019]]
        after,count,_=apply_actions(rows,target)
        self.assertEqual(count,3);self.assertEqual([r[7] for r in after[1:]],[1,1,1])


class ModelRowsTests(unittest.TestCase):
    def setup_rows(self):
        before=snapshot();original=copy.deepcopy(before)
        rows,created,formats=ensure_target_rows(before,before['sheets'][TARGET]['values'],{POLO,TENET})
        self.assertEqual(before,original);self.assertEqual(len(created),2)
        return before,rows,created,formats

    def test_rows_append_once_without_overwriting_or_shifting_originals(self):
        before,rows,created,formats=self.setup_rows()
        self.assertEqual(rows[:len(before['sheets'][TARGET]['values'])],before['sheets'][TARGET]['values'])
        self.assertEqual([item['model'] for item in created],['Volkswagen Polo K','Tenet T7'])
        again,created2,formats2=ensure_target_rows(before,rows,{POLO,TENET})
        self.assertEqual(again,rows);self.assertEqual(created2,[]);self.assertEqual(formats2,[])
        self.assertEqual(len(formats),2)

    def test_new_garage_contains_tenet_only_and_reuses_polo_prices(self):
        before,rows,created,formats=self.setup_rows();after={TARGET:rows}
        virtual,new,styles=prepare_garage_rows(before,after,date(2026,10,14))
        name='Гараж 25.08.2026';garage=virtual['sheets'][name]
        self.assertEqual(len(garage['values']),len(before['sheets'][name]['values'])+1)
        self.assertEqual(new[0]['model'],'Tenet T7')
        self.assertEqual(garage['values'][-1][:3],['Комфорт+','Tenet T7',2026])
        self.assertEqual(cell(garage['raw'][-1],14),'')
        self.assertIn('!F5',cell(garage['raw'][-1],3));self.assertEqual(cell(garage['raw'][-1],65),'=D4')
        self.assertTrue(all(cell(garage['raw'][-1],c)=='' for c in range(15,55)))
        self.assertIn('!N2',after[TARGET][3][9]['formula'])
        self.assertIn('!N4',after[TARGET][4][9]['formula'])
        info=prepare_garage(virtual,after,csv_rows()+csv_rows(model='Tenet T7',year='2026',klass='Комфорт+')[1:],date(2026,10,14),update_links=False,archive_source=before['sheets'][name])
        archive=info['operations'][0]['target']
        self.assertEqual(after[archive],before['sheets'][name]['values'])
        self.assertEqual(info['garage']['matched'],2)
        self.assertEqual(cell(after['Гараж 14.10.2026'][-1],14),'')
        derived={(v['row'],v['col']):v['value'] for v in info['derived']}
        self.assertEqual(derived[(4,11)],1500)
        self.assertEqual(derived[(5,11)],1500)

    def test_new_rows_are_visible_in_plan_and_format_copy_never_copies_values(self):
        before,rows,created,formats=self.setup_rows();after={TARGET:rows}
        virtual,new,styles=prepare_garage_rows(before,after,date(2026,8,25))
        info=prepare_garage(virtual,after,csv_rows(),date(2026,8,25),update_links=False,archive_source=before['sheets']['Гараж 25.08.2026'])
        plan=dict(before=before,after=after,row_templates=formats+styles,model_rows=created+new,**info)
        plan['changes']=changes_for(comparison_before(plan),after)
        adapter=object.__new__(GoogleTarget)
        from shoko.google_batches import write_batches
        import json
        requests=[r for part in write_batches(adapter.prepare_write(plan)) for r in json.loads(part)['requests']]
        copying=[r['copyPaste'] for r in requests if 'copyPaste' in r]
        self.assertEqual(len(copying),len(formats+styles))
        self.assertTrue(all(c['pasteType']=='PASTE_FORMAT' for c in copying))
        self.assertFalse(any('insertDimension' in r for r in requests))

if __name__=='__main__':unittest.main()
