"""Small artificial examples shipped with the app, never user/customer data."""
from pathlib import Path
from openpyxl import Workbook
from .calculation import TARGET


def create_samples(folder):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    wb = Workbook(); ws = wb.active; ws.title = 'Кол_состав_08-26'
    ws.cell(1, 13, 6); ws.cell(1, 22, 13); ws.cell(1, 31, 19)
    ws.cell(2, 2, 'Volkswagen Polo Эконом')
    ws.cell(3, 2, 2019); ws.cell(3, 3, 1200); ws.cell(3, 4, 1300)
    for c in range(13, 39): ws.cell(3, c, c)
    wb.save(folder / 'prostoi.xlsx'); wb.close()
    wb = Workbook(); ws = wb.active; ws.title = '19'
    ws.cell(1, 1, 'Год ПТС'); ws.cell(1, 2, 'Скидка/наценка'); ws.cell(1, 23, 'Автомобиль')
    ws.cell(2, 1, 2019); ws.cell(2, 2, -100); ws.cell(2, 23, 'Volkswagen Polo №001')
    wb.save(folder / 'drivers.xlsx'); wb.close()
    wb = Workbook(); ws = wb.active; ws.title = TARGET
    ws.append(['Класс', 'Модель', 'Год']); ws.append(['Эконом', 'Polo', 2019])
    ws['F2'] = 999; ws['G2'] = 888; ws['H2'] = 777
    garage = wb.create_sheet('Гараж 20.08.2026')
    garage.append(['Класс', 'Модель', 'Год выпуска'] + [''] * 12 + [f'Конкурент {i}' for i in range(1, 41)])
    garage.append(['Эконом', 'Volkswagen Polo', 2019])
    wb.save(folder / 'target.xlsx'); wb.close()
    (folder / 'garage.csv').write_text('Марка и модель;Тариф;Год выпуска;Цена аренды 7 / 0;Тип топлива;КПП;Таксопарк\nVolkswagen Polo;эконом;2019;1500;бензин;автомат;Тестовый парк\n', encoding='utf-8')
    return dict(prostoi=str(folder/'prostoi.xlsx'), drivers=str(folder/'drivers.xlsx'), csv=str(folder/'garage.csv'),
                target=str(folder/'target.xlsx'), target_mode='local', last_date='2026-08-20', test_copy_confirmed=True)
