"""Approved link, missing-market and acyclic pricing behavior on synthetic data."""
import copy
import re
import unittest
from openpyxl.utils import column_index_from_string as ix
from shoko.calculation import TARGET,cell,set_cell
from shoko.driver_prices import target_value_formula,bind_driver_prices
from shoko.garage_repairs import repair_garage_template,market_guard,pricing_values,verify_garage_repairs,unguard,rental_values,rental_ratio_formula,rental_guard
from shoko.garage import summary_value,formula
from shoko.storage import AppError


def fixture():
    rows=[['Класс','Модель','Год'],['Эконом','Volkswagen Polo',2020]]
    raw=copy.deepcopy(rows)
    fields={'D':"='Простои'!C50",'E':"='Предложение цен'!U9",'F':"='Предложение цен'!W9",'G':'=#REF!',
      'J':'=MIN(P2:AY2)','K':'=J2-D2','L':'=AVERAGE(P2:AY2)','M':'=L2-D2','N':'=COUNTA(P2:BC2)',
      'BE':'=MIN(P2:BC2)','BF':'=AVERAGE(BE2;BG2)','BG':'=AVERAGE(P2:BC2)',
      'BH':'=AVERAGE(BI2;BG2)','BI':'=MAX(P2:BC2)','BO':'=IF(BN2<=BF2;"Ниже среднего";"Средняя")',
      'BQ':'=IF(BP2<=BF2;"Ниже среднего";"Средняя")','BR':'=IFERROR(VALUE(SUBSTITUTE($D2;" ";""));0)',
      'BS':'=IFERROR(VALUE(REGEXREPLACE(TO_TEXT($BF2);"[^\\d-]";""));0)',
      'BW':'=IFERROR(ROUND($E9/$G9;0);0)','BX':'=IF($BW2>0;$F2/$BW2;0)',
      'CB':'=MIN(2,5;IF($CA2>0;$BX2/$CA2;0))','CK':'=$CB2*(0,6+0,2*$CD2+0,2*$BY2)',
      'CH':'=MAX(50;$BU2-$BS2)','CI':'=$BS2+MAX(0,5-$CF2)*($BT2-$BS2)','CJ':'=MAX(0;$BR2-$BU2)',
      'CL':'=MIN(MAX($BT2-0,3*$CK2*$CH2-0,7*$CJ2-0,05*$CD2*$BT2;$CI2);$BU2)',
      'CM':'=$CO2','CN':'=IF(OR($CK2>1;$CJ2>0);$CL2;IF(AND($CG2>=$CF2;$CK2<=0,6);$CM2;MIN(MAX($BU2;$CI2);$BV2)))',
      'CO':'=MIN(IF($BV2>0;$BV2;$BU2);MAX(ROUND(($BR2+0,5*($CN2-$BR2))/100;0)*100;CEILING($CI2+100;100)))',
      'CP':'=MIN($CO2;$BP2)','CQ':'=$CO2-$CP2','CR':'=IF($CP2<$BS2;"Ниже среднего";"Средняя")',
      'CS':'=IF($CO2<$BS2;"Ниже среднего";"Средняя")','CT':'=$CP2','CU':'=$CR2','CV':'=$CO2','CW':'=$CS2',
      'CX':'="Акция: "&$CP2','CY':'="Действующая: "&$CO2'}
    for col,text in fields.items():set_cell(raw,1,ix(col)-1,text)
    set_cell(raw,0,90,'Цель вверх — Target_up')
    source=dict(values=rows,raw=raw,formula_cells=[[1,ix(c)-1] for c in fields])
    target=[[],['Эконом','Volkswagen Polo',2019,'','',1200,2000],['Эконом','Volkswagen Polo',2020,'','',1300,2100]]
    for r in (1,2):
        for c,v in ((15,30),(16,15),(20,5),(22,1),(23,.05)):set_cell(target,r,c,v)
    return source,target


class GarageRepairTests(unittest.TestCase):
    def test_links_use_model_year_and_distinct_price_roles(self):
        source,target=fixture();before=copy.deepcopy(source['values'])
        checks=repair_garage_template(source,target,'Гараж')
        self.assertEqual(source['values'],before)
        for dest,col in (('D','G'),('E','U'),('F','W'),('G','X')):
            self.assertEqual(cell(source['raw'][1],ix(dest)-1),target_value_formula(col))
        self.assertEqual(checks[0]['price_column'],'G')
        self.assertEqual(cell(source['raw'][1],74),target_value_formula('P'))
        self.assertEqual(cell(source['raw'][1],75),rental_ratio_formula(2))
        self.assertIn("'Предложение цен'!Q:Q",cell(source['raw'][1],75))
        self.assertNotIn('$BW2',cell(source['raw'][1],75))
        target[1:]=list(reversed(target[1:]))
        self.assertEqual(repair_garage_template(source,target,'Гараж')[0]['price_column'],'G')

    def test_tenet_preserves_approved_F_and_current_price_G_is_separate(self):
        source,target=fixture();source['values'][1][1:3]=['Tenet T7',2026]
        target.append(['Комфорт+','Tenet T7',2026,'','',3000,3500])
        for c,v in ((15,19),(16,17),(20,1),(22,0)):set_cell(target,len(target)-1,c,v)
        checks=repair_garage_template(source,target,'Гараж')
        self.assertEqual(checks[0]['price_column'],'F')
        self.assertEqual(cell(source['raw'][1],3),target_value_formula('F'))

    def test_missing_and_ambiguous_models_block(self):
        source,target=fixture()
        for data in (target[:-1],target+[copy.deepcopy(target[-1])]):
            with self.assertRaises(AppError):repair_garage_template(copy.deepcopy(source),data,'Гараж')

    def test_idempotence_preserves_manual_dashes_unrelated_formulas_and_ranges(self):
        source,target=fixture();set_cell(source['raw'],1,11,'-');set_cell(source['raw'],1,78,'=MAX(0;2025-$C2)')
        repair_garage_template(source,target,'Гараж');first=copy.deepcopy(source)
        repair_garage_template(source,target,'Гараж')
        self.assertEqual(source,first)
        self.assertEqual(cell(source['raw'][1],11),'-')
        self.assertEqual(cell(source['raw'][1],78),'=MAX(0;2025-$C2)')
        self.assertEqual(unguard(cell(source['raw'][1],9)),'=MIN(P2:AY2)')

    def test_cycle_is_removed_and_other_pricing_coefficients_preserved(self):
        source,target=fixture();old=copy.deepcopy(source['raw'][1])
        repair_garage_template(source,target,'Гараж')
        cm=cell(source['raw'][1],90)
        self.assertEqual(unguard(cm),'=MIN(MAX($BU2;$CI2);$BV2)')
        self.assertFalse(any(x in cm for x in ('$CO2','$CN2','$CP2')))
        for col in ('CL','CN','CO','CP'):
            self.assertEqual(unguard(cell(source['raw'][1],ix(col)-1)),cell(old,ix(col)-1))
        self.assertNotIn('IFERROR',cell(source['raw'][1],69))
        self.assertNotIn('IFERROR',cell(source['raw'][1],70))

    def test_missing_market_summary_is_dash_count_stays_zero_and_returns_when_market_reappears(self):
        source,target=fixture();repair_garage_template(source,target,'Гараж')
        rows=copy.deepcopy(source['values'])
        for c,text in enumerate(source['raw'][1]):
            if isinstance(text,str) and text.startswith('='):set_cell(rows,1,c,formula(text))
        for col in ('J','L','BE','BF','BG','BH','BI'):
            self.assertEqual(summary_value(rows,1,ix(col),native=True),'-')
        self.assertEqual(summary_value(rows,1,ix('N'),native=True),0)
        for c,v in ((15,1000),(16,2000)):set_cell(rows,1,c,v)
        self.assertEqual(summary_value(rows,1,ix('BG'),native=True),1500)
        self.assertEqual(summary_value(rows,1,ix('BF'),native=True),1250)
        self.assertEqual(summary_value(rows,1,ix('BH'),native=True),1750)

    def test_intermediate_market_arithmetic_is_guarded_too(self):
        source,target=fixture();repair_garage_template(source,target,'Гараж')
        for col in ('CH','CI','CJ'):
            self.assertTrue(cell(source['raw'][1],ix(col)-1).startswith('=IF(COUNT(P2:BC2)=0;"-";'))
        self.assertEqual(unguard(cell(source['raw'][1],ix('CH')-1)),'=MAX(50;$BU2-$BS2)')

    def test_formula_strings_are_balanced_and_guard_does_not_accumulate(self):
        source,target=fixture();repair_garage_template(source,target,'Гараж')
        for expression in source['raw'][1]:
            if not isinstance(expression,str) or not expression.startswith('='):continue
            depth=0;quoted=False
            for char in expression:
                if char=='"':quoted=not quoted
                if not quoted:
                    depth+=(char=='(')-(char==')');self.assertGreaterEqual(depth,0,expression)
            self.assertEqual(depth,0,expression)
        for comment in (False,True):
            value=market_guard('=$CO2',2,ready=True,comment=comment)
            self.assertEqual(market_guard(value,2,ready=True,comment=comment),value)

    def test_oracle_up_down_and_bankers_rounding_boundary(self):
        row=[]
        for col,v in {'BR':2000,'BS':1400,'BT':1600,'BU':1800,'BV':2200,'CI':1500,'CJ':0,'CK':.2,'CH':400,'CD':.25,'CG':.6,'CF':.2,'BP':2100}.items():set_cell([row],0,ix(col)-1,v)
        up=pricing_values(row)
        self.assertEqual(up['CM'],1800);self.assertEqual(up['CN'],1800);self.assertEqual(up['CO'],1900);self.assertEqual(up['CP'],1700)
        row[ix('CK')-1]=1.2
        down=pricing_values(row)
        self.assertEqual(down['CN'],down['CL']);self.assertEqual(down['CL'],1500)
        self.assertEqual(down['CO'],1800)  # 17.5 rounds away from zero, as in Sheets.

    def test_unrelated_recommendation_layout_is_not_rewritten(self):
        source,target=fixture();source['raw'][0][90]='Другая формула'
        before=copy.deepcopy(source)
        with self.assertRaisesRegex(AppError,'CM1'):repair_garage_template(source,target,'Гараж')
        self.assertEqual(source,before)

    def test_no_market_verifier_rejects_false_zero_recommendation_and_bad_link(self):
        source,target=fixture();bind_driver_prices(source,target,'Гараж');checks=repair_garage_template(source,target,'Гараж')
        check=checks[0];check['columns']=['D','E','F','G'];check['recommendations']=['CO','CP','CX','CY']
        row=copy.deepcopy(source['values'][1])
        for col,v in {'D':2100,'E':5,'F':1,'G':.05,'BP':2100,'BW':30,'BX':.05,'CO':'-','CP':'-','CX':'Недостаточно рыночных данных','CY':'Недостаточно рыночных данных'}.items():set_cell([row],0,ix(col)-1,v)
        snap={'sheets':{TARGET:{'values':target},'Гараж':{'values':[[],row]}}};plan={'garage_checks':checks,'after':{'Гараж':[[],[]]}}
        verify_garage_repairs(snap,plan)
        row[ix('CH')-1]='#VALUE!'
        with self.assertRaisesRegex(AppError,'CH2'):verify_garage_repairs(snap,plan)
        row[ix('CH')-1]='-'
        row[ix('CO')-1]=0
        with self.assertRaisesRegex(AppError,'CO2'):verify_garage_repairs(snap,plan)
        row[ix('CO')-1]='-';row[ix('D')-1]=2000
        with self.assertRaisesRegex(AppError,'D2'):verify_garage_repairs(snap,plan)


    def test_buyout_changes_total_but_not_rental_idle_ratio(self):
        source,target=fixture();row=target[-1]
        self.assertEqual(rental_values(row,'test'),(30,.05))
        row[15]=100
        self.assertEqual(rental_values(row,'test'),(100,.05))
        row[22]=0
        self.assertEqual(rental_values(row,'test'),(100,0))

    def test_no_rental_fleet_is_not_a_zero_idle_percentage(self):
        source,target=fixture();row=target[-1]
        for c,v in ((15,3),(16,0),(20,0),(22,0)):row[c]=v
        self.assertEqual(rental_values(row,'test'),(3,'-'))
        checks=repair_garage_template(source,target,'Гараж')
        self.assertEqual(checks[0]['rental_columns'],['BW','BX'])
        for c in ('CB','CK'):
            self.assertIn('COUNT($BX2)=0;"-"',cell(source['raw'][1],ix(c)-1))
        for c in ('CX','CY'):
            self.assertIn('$BX2="-";"Нет арендного парка"',cell(source['raw'][1],ix(c)-1))

    def test_invalid_fleet_counts_block_preparation(self):
        for column,bad in [('P',''),('Q','15'),('U',None),('W',True),('P',float('nan')),('Q',float('inf')),('U',-1),('W',6),('P',19)]:
            with self.subTest(column=column,bad=bad):
                source,target=fixture();target[-1][ix(column)-1]=bad
                with self.assertRaises(AppError):repair_garage_template(source,target,'Гараж')

    def test_empty_additional_block_is_not_populated(self):
        source,target=fixture()
        for c in range(ix('BR')-1,ix('CY')):set_cell(source['raw'],1,c,'')
        checks=repair_garage_template(source,target,'Гараж')
        self.assertEqual(checks[0]['rental_columns'],[])
        self.assertEqual(checks[0]['recommendations'],[])
        self.assertTrue(all(v=='' for v in source['raw'][1][ix('BR')-1:ix('CY')]))

    def test_rental_guards_preserve_coefficients_and_do_not_accumulate(self):
        source,target=fixture();old=copy.deepcopy(source['raw'][1])
        repair_garage_template(source,target,'Гараж')
        for c in ('CB','CK'):
            actual=cell(source['raw'][1],ix(c)-1)
            self.assertEqual(unguard(actual),cell(old,ix(c)-1))
            self.assertEqual(rental_guard(actual,2),actual)
        self.assertTrue(cell(source['raw'][1],ix('BX')-1).endswith('IF(rented+$E2=0;"-";$F2/(rented+$E2))))'))

    def test_verifier_rejects_total_denominator_and_old_zero_total(self):
        source,target=fixture();checks=repair_garage_template(source,target,'Гараж')
        checks[0]['columns']=['BW','BX'];checks[0]['recommendations']=[]
        row=[]
        for c,v in {'D':2100,'E':5,'F':1,'G':.05,'BP':2100,'BW':30,'BX':.05}.items():set_cell([row],0,ix(c)-1,v)
        snap={'sheets':{TARGET:{'values':target},'Гараж':{'values':[[],row]}}}
        plan={'garage_checks':checks,'after':{'Гараж':[[],[]]}}
        verify_garage_repairs(snap,plan)
        row[ix('BX')-1]=1/30
        with self.assertRaisesRegex(AppError,'BX2'):verify_garage_repairs(snap,plan)
        row[ix('BX')-1]=.05;row[ix('BW')-1]=0
        with self.assertRaisesRegex(AppError,'BW2'):verify_garage_repairs(snap,plan)

    def test_zero_base_with_market_suppresses_recommendations_and_explains_reason(self):
        source,target=fixture()
        for c,v in ((15,3),(16,0),(20,0),(22,0),(23,0)):target[-1][c]=v
        checks=repair_garage_template(source,target,'Гараж')
        checks[0]['columns']=['BW','BX','CB','CK'];checks[0]['recommendations']=['CO','CP','CX','CY']
        row=[]
        for c,v in {'D':2100,'E':0,'F':0,'G':0,'BP':2100,'P':2000,'BR':2100,'BW':3,'BX':'-',
                    'CB':'-','CK':'-','CO':'-','CP':'-','CX':'Нет арендного парка','CY':'Нет арендного парка'}.items():set_cell([row],0,ix(c)-1,v)
        snap={'sheets':{TARGET:{'values':target},'Гараж':{'values':[[],row]}}}
        plan={'garage_checks':checks,'after':{'Гараж':[[],[]]}}
        verify_garage_repairs(snap,plan)
        for c,bad in [('BX',0),('CB',0),('CK',0),('CO',2000),('CX','Недостаточно исходных данных')]:
            old=row[ix(c)-1];row[ix(c)-1]=bad
            with self.subTest(column=c),self.assertRaises(AppError):verify_garage_repairs(snap,plan)
            row[ix(c)-1]=old

    def test_zero_numerator_keeps_valid_recommendation_inputs(self):
        source,target=fixture();target[-1][22]=0
        checks=repair_garage_template(source,target,'Гараж')
        checks[0]['columns']=['BW','BX','CB','CK'];checks[0]['recommendations']=['CX']
        row=[]
        for c,v in {'D':2100,'E':5,'F':0,'G':.05,'BP':2100,'P':2000,'BR':2100,'BW':30,'BX':0,
                    'CA':.05,'CB':0,'CD':.375,'BY':0,'CK':0,'CX':'Действующая: ...'}.items():set_cell([row],0,ix(c)-1,v)
        snap={'sheets':{TARGET:{'values':target},'Гараж':{'values':[[],row]}}}
        verify_garage_repairs(snap,{'garage_checks':checks,'after':{'Гараж':[[],[]]}})


if __name__=='__main__':unittest.main()
