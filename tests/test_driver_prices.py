"""BP has an independent model/year lookup, preserving all other raw cells."""
import copy
import re
import unittest
from shoko.driver_prices import bind_driver_prices, driver_price_formula
from shoko.calculation import cell, set_cell
from shoko.storage import AppError


class DriverPriceTests(unittest.TestCase):
    def fixture(self):
        rows=[['Класс','Модель','Год'],['Эконом','Volkswagen Polo',2019],
              ['Эконом','Volkswagen Polo К',2019],['Комфорт+','Tenet T7',2026]]
        raw=copy.deepcopy(rows)
        for r in range(1,4):
            set_cell(raw,r,67,"='Простои'!B50")
            set_cell(raw,r,90,'=CM2')  # Existing unrelated cycle remains exact.
        source=dict(values=rows,raw=raw,formula_cells=[[r,c] for r in range(1,4) for c in (67,90)])
        target=[[],['Эконом','Tenet T7',2026,'','',900,3500],
                ['Эконом',' Volkswagen  Polo K ',2019,'','',800,2000],
                ['Эконом','Volkswagen Polo',2019,'','',700,2100]]
        return source,target

    def test_model_year_variant_and_row_reordering(self):
        source,target=self.fixture(); original=copy.deepcopy(source)
        for data in (target,[target[0]]+list(reversed(target[1:]))):
            actual=bind_driver_prices(source,data,'Гараж 19.10.2026')
            self.assertEqual([x['value'] for x in actual],[2100,2000,3500])
            self.assertTrue(all(x['col']==68 for x in actual))
        self.assertEqual(source['values'],original['values'])
        for r,row in enumerate(source['raw']):
            for c,value in enumerate(row):
                if c!=67:self.assertEqual(value,cell(original['raw'][r],c))
        once=copy.deepcopy(source)
        bind_driver_prices(source,target,'Гараж 19.10.2026')
        self.assertEqual(source,once)

    def test_missing_model_year_and_ambiguous_variant_block(self):
        source,target=self.fixture()
        for data in (target[:-1], [*target,copy.deepcopy(target[2])]):
            with self.assertRaises(AppError):bind_driver_prices(copy.deepcopy(source),data,'Гараж')
        target[-1][2]=2020
        with self.assertRaisesRegex(AppError,'BP2.*2019'):bind_driver_prices(source,target,'Гараж')

    def test_zero_blank_and_invalid_source_prices(self):
        source,target=self.fixture()
        for price in (0,'',None,2100.5):
            target[-1][6]=price
            actual=bind_driver_prices(source,target,'Гараж')
            self.assertEqual(actual[0]['value'],'' if price is None else price)
        for price in ('2026','#REF!',-1,float('nan'),float('inf'),True):
            target[-1][6]=price
            with self.subTest(price=price), self.assertRaisesRegex(AppError,'G4'):
                bind_driver_prices(source,target,'Гараж')

    def test_formula_uses_model_year_and_G_without_fixed_rows(self):
        expression=driver_price_formula()
        self.assertIn("'Предложение цен'!G:G",expression)
        self.assertIn('INDEX(B:B;ROW())',expression)
        self.assertIn('INDEX(C:C;ROW())',expression)
        self.assertNotIn('Простои',expression)
        self.assertFalse(re.search(r'![A-Z]+[0-9]',expression))
        self.assertIn('ROWS(found)<>1;NA()',expression)
        self.assertIn('ISBLANK',expression)
        self.assertIn('[кk]$',expression)
        depth=0;quoted=False
        for char in expression:
            if char=='"':quoted=not quoted
            if not quoted:
                depth += (char=='(')-(char==')')
                self.assertGreaterEqual(depth,0)
        self.assertEqual(depth,0)


if __name__=='__main__':unittest.main()
