import copy
import json
import unittest
from unittest.mock import Mock, patch
from shoko.adapters import GoogleTarget
from shoko.google_batches import prepare_batches, write_batches, batch_summary, MAX_GOOGLE_WRITE_BYTES
from shoko.storage import AppError
from shoko.sheet_backups import planned_backups
from test_plan_write import plan_for, cells_from
from test_garage import plan_for as garage_plan
from shoko.calculation import TARGET


class GoogleBatchTests(unittest.TestCase):
    def setUp(self):
        self.adapter = object.__new__(GoogleTarget)

    def all_cells(self, parts):
        result = {}
        for payload in parts:
            cells = cells_from(payload)
            self.assertFalse(result.keys() & cells.keys(), 'A cell must never be sent twice')
            result.update(cells)
        return result

    def test_more_than_14mb_is_complete_and_bounded_before_compression(self):
        text = 'Я' * 1400
        changes = [{'sheet': TARGET, 'row': r, 'col': 1, 'after': text} for r in range(1, 5001)]
        plan = plan_for(changes)
        plan['before']['sheets'][TARGET]['rows'] = 6000
        plan['after'][TARGET] = [[text] for _ in changes]
        original = copy.deepcopy(plan)
        prepared = self.adapter.prepare_write(plan)
        parts = write_batches(prepared)
        self.assertGreater(sum(map(len, parts)), 14_000_000)
        self.assertTrue(all(len(p) <= MAX_GOOGLE_WRITE_BYTES for p in parts))
        expected = {(9, r, 1): {'stringValue': text} for r in range(1, 5001)}
        self.assertEqual(self.all_cells(parts), expected)
        self.assertEqual(plan, original)
        self.assertEqual(batch_summary(prepared), {'parts': len(parts), 'bytes': sum(map(len, parts))})
        progress = []
        self.adapter.write_progress_callback = progress.append
        with patch.object(self.adapter, 'request') as request, patch('shoko.adapters.time.sleep'):
            self.adapter.write(plan, prepared)
        self.assertEqual(request.call_count, len(parts))
        self.assertEqual(progress[-1], {'total': len(parts), 'confirmed': len(parts), 'in_flight': None})

    def test_wide_row_splits_by_columns_without_touching_holes_or_types(self):
        changes = [{'sheet': TARGET, 'row': 4, 'col': c, 'after': 'Тест' * 60} for c in range(7, 27)]
        changes += [{'sheet': TARGET, 'row': 4, 'col': 30, 'after': ''},
                    {'sheet': TARGET, 'row': 4, 'col': 31, 'after': '=literal'},
                    {'sheet': TARGET, 'row': 4, 'col': 32, 'after': False},
                    {'sheet': TARGET, 'row': 4, 'col': 33, 'after': 1.25},
                    {'sheet': TARGET, 'row': 4, 'col': 34, 'after': '=1+1', 'kind': 'formula'}]
        plan = plan_for(changes)
        whole = self.adapter.prepare_write(plan)
        with patch('shoko.google_batches.MAX_GOOGLE_WRITE_BYTES', 1200):
            parts = write_batches(self.adapter.prepare_write(plan))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 1200 for p in parts))
        self.assertEqual(self.all_cells(parts), cells_from(whole))

    def test_backup_archive_rename_and_grid_operations_keep_dependency_order(self):
        plan = garage_plan()
        plan['sheet_backups'] = planned_backups(plan['before'], plan['changes'], plan['operations'])
        whole = json.loads(self.adapter.prepare_write(plan))['requests']
        with patch('shoko.google_batches.MAX_GOOGLE_WRITE_BYTES', 800):
            parts = write_batches(self.adapter.prepare_write(plan))
        requests = [r for p in parts for r in json.loads(p)['requests']]
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 800 for p in parts))
        structural = lambda items: [r for r in items if 'updateCells' not in r]
        self.assertEqual(structural(requests), structural(whole))
        bf_ids = {r['duplicateSheet']['newSheetId'] for r in requests if r.get('duplicateSheet', {}).get('newSheetName', '').startswith('BF_')}
        first_live = next(i for i,r in enumerate(requests) if r.get('updateCells', {}).get('start', {}).get('sheetId') in (7,9))
        self.assertTrue(all(i < first_live for i,r in enumerate(requests) if r.get('duplicateSheet', {}).get('newSheetId') in bf_ids or r.get('updateCells', {}).get('start', {}).get('sheetId') in bf_ids))
        self.assertEqual(self.all_cells(parts), cells_from(json.dumps({'requests': whole})))

    def test_timeout_on_second_part_stops_without_repeating_or_sending_third(self):
        plan = plan_for([])
        progress, reports = [], []
        self.adapter.write_progress_callback = progress.append
        self.adapter.report = reports.append
        parts = (b'{"requests":[]}',) * 3
        with patch.object(self.adapter, 'request', side_effect=[{}, AppError('Google не подтвердил результат записи.')]) as request, patch('shoko.adapters.time.sleep'):
            with self.assertRaisesRegex(AppError, 'Google подтвердил 1 из 3 частей'):
                self.adapter.write(plan, parts)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(progress[-1], {'total': 3, 'confirmed': 1, 'in_flight': 2})
        self.assertTrue(any('Часть 2/3' in r for r in reports))
        self.assertFalse(any('Часть 3/3' in r for r in reports))

    def test_no_post_if_any_later_part_is_unsendable(self):
        changes = [{'sheet': TARGET, 'row': r, 'col': 1, 'after': 'текст' * 30} for r in range(1, 12)]
        changes.append({'sheet': TARGET, 'row': 30, 'col': 1, 'after': 'Я' * 2000})
        with patch('shoko.google_batches.MAX_GOOGLE_WRITE_BYTES', 1000), patch.object(self.adapter, 'request') as request:
            with self.assertRaisesRegex(AppError, 'Запись не начата'):
                self.adapter.write(plan_for(changes))
        request.assert_not_called()

    def test_intent_must_be_persisted_before_next_request(self):
        progress = Mock(side_effect=[None, None, None, OSError('fixture disk full')])
        self.adapter.write_progress_callback = progress
        with patch.object(self.adapter, 'request') as request, patch('shoko.adapters.time.sleep'):
            with self.assertRaises(OSError):
                self.adapter.write(plan_for([]), (b'first', b'second', b'third'))
        request.assert_called_once()

    def test_batch_size_counts_utf8_and_exact_envelope(self):
        request = {'updateCells': {'start': {'sheetId': 9, 'rowIndex': 2, 'columnIndex': 3},
                    'rows': [{'values': [{'userEnteredValue': {'stringValue': 'Я' * 20}}]}], 'fields': 'userEnteredValue'}}
        size = len(prepare_batches([request]))
        with patch('shoko.google_batches.MAX_GOOGLE_WRITE_BYTES', size):
            self.assertEqual(len(prepare_batches([request])), size)
            self.assertEqual(len(write_batches(prepare_batches([request, request]))), 2)
        with patch('shoko.google_batches.MAX_GOOGLE_WRITE_BYTES', size-1):
            with self.assertRaisesRegex(AppError, 'Запись не начата'):
                prepare_batches([request])

    def test_paces_batches_without_retrying(self):
        with patch.object(self.adapter, 'request') as request, patch('shoko.adapters.time.monotonic', return_value=10), patch('shoko.adapters.time.sleep') as sleep:
            self.adapter.write(plan_for([]), (b'first', b'second', b'third'))
        self.assertEqual(request.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1.2, 1.2])

    def test_empty_plan_sends_nothing(self):
        self.assertIsNone(prepare_batches([]))
        self.assertEqual(batch_summary(None), {'parts': 0, 'bytes': 0})
        with patch.object(self.adapter, 'request') as request:
            self.adapter.write(plan_for([]), prepared=())
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
