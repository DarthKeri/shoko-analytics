import copy
import unittest
from unittest.mock import patch
from shoko.adapters import GoogleTarget, changes_for, normalize_merged_output
from shoko.calculation import PROSTOI
from shoko.storage import AppError


MERGE = {'sheetId': 7, 'startRowIndex': 1, 'endRowIndex': 3, 'startColumnIndex': 4, 'endColumnIndex': 5}


def snapshot(merges=None):
    values = [[''] * 6 for _ in range(4)]
    values[1][4] = 'Заголовок'
    return {'mode': 'google', 'sheets': {PROSTOI: {'id': 7, 'rows': 4, 'cols': 6,
            'values': copy.deepcopy(values), 'raw': values, 'merges': [copy.deepcopy(MERGE)] if merges is None else merges}}}


class MergedCellTests(unittest.TestCase):
    def setUp(self):
        self.adapter = object.__new__(GoogleTarget)

    def test_vertical_placeholder_is_removed_before_review_without_mutation(self):
        before = snapshot()
        after = {PROSTOI: copy.deepcopy(before['sheets'][PROSTOI]['values'])}
        after[PROSTOI][2][4] = '0'
        original = copy.deepcopy(after)
        normalized, warnings = normalize_merged_output(before, after)
        self.assertEqual(after, original)
        self.assertEqual(normalized[PROSTOI][1][4], 'Заголовок')
        self.assertEqual(normalized[PROSTOI][2][4], '')
        self.assertEqual(changes_for(before, normalized), [])
        self.assertEqual(len(warnings), 1)
        self.assertIn('1 служебных нулей', warnings[0])

    def test_horizontal_zero_and_percentage_placeholders_are_removed(self):
        before = snapshot([{'sheetId': 7, 'endRowIndex': 1, 'startColumnIndex': 1, 'endColumnIndex': 5}])
        after = {PROSTOI: [['', 'Шапка', 0, '0.0%', 0.0]]}
        normalized, warnings = normalize_merged_output(before, after)
        self.assertEqual(normalized[PROSTOI], [['', 'Шапка', '', '', '']])
        self.assertIn('3 служебных нулей', warnings[0])

    def test_meaningful_data_in_follower_blocks_preparation(self):
        before = snapshot()
        for value in (123, 'Данные', False, True, '00', '0.01%'):
            with self.subTest(value=value):
                after = {PROSTOI: copy.deepcopy(before['sheets'][PROSTOI]['values'])}
                after[PROSTOI][2][4] = value
                with self.assertRaisesRegex(AppError, 'E3.*непустые данные'):
                    normalize_merged_output(before, after)

    def test_blank_followers_and_outside_ranges_create_no_rows_or_warnings(self):
        before = snapshot([dict(MERGE, endRowIndex=1_000_000, endColumnIndex=1000)])
        after = {PROSTOI: [['']]}
        normalized, warnings = normalize_merged_output(before, after)
        self.assertEqual(normalized, after)
        self.assertIs(normalized[PROSTOI], after[PROSTOI])
        self.assertEqual(warnings, [])

    def test_write_preflight_rejects_unfiltered_reviewed_changes_even_zero(self):
        plan = {'before': snapshot(), 'after': {PROSTOI: [['']]},
                'changes': [{'sheet': PROSTOI, 'row': 3, 'col': 5, 'cell': 'E3', 'after': '0'}]}
        with patch.object(self.adapter, 'request') as request:
            with self.assertRaisesRegex(AppError, 'Подготовьте новый план'):
                self.adapter.write(plan)
        request.assert_not_called()

    def test_anchor_change_stays_in_exact_plan_and_verifies(self):
        before = snapshot()
        after = {PROSTOI: copy.deepcopy(before['sheets'][PROSTOI]['values'])}
        after[PROSTOI][1][4] = 123
        after[PROSTOI][2][4] = 0
        normalized, _ = normalize_merged_output(before, after)
        plan = {'before': before, 'after': normalized, 'changes': changes_for(before, normalized)}
        self.assertEqual([c['cell'] for c in plan['changes']], ['E2'])
        self.assertIsNotNone(self.adapter.prepare_write(plan))
        current = copy.deepcopy(before)
        current['sheets'][PROSTOI]['raw'][1][4] = 123
        with patch.object(self.adapter, 'snapshot', return_value=current):
            self.adapter.verify(plan)

    def test_anchor_mismatch_is_not_ignored(self):
        before = snapshot()
        plan = {'before': before, 'after': {PROSTOI: []},
                'changes': [{'sheet': PROSTOI, 'row': 2, 'col': 5, 'cell': 'E2', 'after': 123}]}
        with patch.object(self.adapter, 'snapshot', return_value=before):
            with self.assertRaisesRegex(AppError, 'E2'):
                self.adapter.verify(plan)

    def test_ordinary_cell_mismatch_is_not_ignored(self):
        before = snapshot()
        plan = {'before': before, 'after': {PROSTOI: []},
                'changes': [{'sheet': PROSTOI, 'row': 4, 'col': 1, 'cell': 'A4', 'after': 123}]}
        with patch.object(self.adapter, 'snapshot', return_value=before):
            with self.assertRaisesRegex(AppError, 'A4'):
                self.adapter.verify(plan)

    def test_merge_change_after_write_is_detected(self):
        before = snapshot()
        plan = {'before': before, 'after': {PROSTOI: []}, 'changes': []}
        with patch.object(self.adapter, 'snapshot', return_value=snapshot([])):
            with self.assertRaisesRegex(AppError, 'изменились объединения'):
                self.adapter.verify(plan)

    def test_snapshot_includes_merges_in_fingerprint(self):
        props = {'title': PROSTOI, 'sheetId': 7, 'gridProperties': {'rowCount': 4, 'columnCount': 6}}
        meta = {'sheets': [{'properties': props, 'merges': [MERGE]}]}
        data = {'sheets': [{'properties': props, 'data': []}]}
        self.adapter.id = 'test-copy'
        with patch.object(self.adapter, 'check', return_value=meta), patch.object(self.adapter, 'request', return_value=data):
            first = self.adapter.snapshot()
        self.assertEqual(first['sheets'][PROSTOI]['merges'], [MERGE])
        changed = copy.deepcopy(meta)
        changed['sheets'][0]['merges'] = []
        with patch.object(self.adapter, 'check', return_value=changed), patch.object(self.adapter, 'request', return_value=data):
            self.assertNotEqual(first['fingerprint'], self.adapter.snapshot()['fingerprint'])


if __name__ == '__main__':
    unittest.main()
