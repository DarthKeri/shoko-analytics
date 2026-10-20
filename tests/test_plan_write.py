import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from shoko.adapters import GoogleTarget, MAX_GOOGLE_WRITE_BYTES
from shoko.calculation import TARGET, GARAGE
from shoko.plans import load_plan, save_plan, plan_path
from shoko.storage import AppError, digest


def plan_for(changes):
    return {'id': 'reviewed-plan', 'before': {'sheets': {TARGET: {'id': 9, 'rows': 2000, 'cols': 400}},
            'metadata': {'sheets': [{'properties': {'sheetId': 10, 'title': 'Untouched'}}]}},
            'after': {TARGET: [['']]}, 'changes': changes}


def cells_from(payload):
    cells = {}
    for command in json.loads(payload)['requests']:
        if 'updateCells' not in command:
            continue
        update = command['updateCells']
        assert update['fields'] == 'userEnteredValue'
        start = update['start']
        for r, row in enumerate(update['rows'], start['rowIndex'] + 1):
            for c, value in enumerate(row['values'], start['columnIndex'] + 1):
                key = (start['sheetId'], r, c)
                assert key not in cells
                cells[key] = value['userEnteredValue']
    return cells


class GooglePlanWriteTests(unittest.TestCase):
    def setUp(self):
        self.adapter = object.__new__(GoogleTarget)

    def test_merge_rows_preserves_holes_formulas_types_and_clears(self):
        changes = [{'sheet': TARGET, 'row': r, 'col': c, 'after': value}
                   for r in (1, 2, 4) for c, value in ((1, '=text()'), (2, False), (4, ''), (5, 1.25))]
        plan = plan_for(list(reversed(changes)))
        original = copy.deepcopy(plan)
        payload = self.adapter.prepare_write(plan)
        expected = {(9, c['row'], c['col']): {'stringValue': '=text()'} if c['col'] == 1 else
                    {'boolValue': False} if c['col'] == 2 else {} if c['col'] == 4 else {'numberValue': 1.25}
                    for c in changes}
        self.assertEqual(cells_from(payload), expected)
        self.assertEqual(len(json.loads(payload)['requests']), 4)
        self.assertEqual(plan, original)

    def test_realistic_176786_changes_are_sent_once_in_bounded_parts(self):
        from shoko.google_batches import write_batches
        changes = [{'sheet': TARGET, 'row': n // 345 + 1, 'col': n % 345 + 1,
                    'after': '' if n % 3 == 0 else 'Абакан' if n % 3 == 1 else n}
                   for n in range(176786)]
        plan = plan_for(changes)
        prepared = self.adapter.prepare_write(plan)
        parts = write_batches(prepared)
        self.assertGreater(len(parts), 1)
        cells = {}
        for part in parts:
            self.assertLessEqual(len(part), MAX_GOOGLE_WRITE_BYTES)
            current = cells_from(part)
            self.assertFalse(cells.keys() & current.keys())
            cells.update(current)
        self.assertEqual(len(cells), 176786)
        with patch.object(self.adapter, 'request') as request, patch('shoko.adapters.time.sleep'):
            self.adapter.write(plan, prepared=prepared)
        self.assertEqual(request.call_count, len(parts))
        for call, payload in zip(request.call_args_list, parts):
            self.assertEqual(call.args, ('POST', ':batchUpdate'))
            self.assertEqual(call.kwargs['timeout'], (45, 180))
            self.assertEqual(call.kwargs['headers'], {'Content-Type': 'application/json; charset=utf-8', 'Content-Encoding': 'gzip'})
            self.assertEqual(gzip.decompress(call.kwargs['data']), payload)
            self.assertLess(len(call.kwargs['data']), len(payload) // 4)

    def test_small_batch_remains_plain_json_and_keeps_raw_text(self):
        plan = plan_for([{'sheet': TARGET, 'row': 1, 'col': 1, 'after': '=literal'}])
        payload = self.adapter.prepare_write(plan)
        with patch.object(self.adapter, 'request') as request:
            self.adapter.write(plan, prepared=payload)
        self.assertEqual(request.call_args.kwargs['data'], payload)
        self.assertNotIn('Content-Encoding', request.call_args.kwargs['headers'])
        self.assertEqual(cells_from(payload), {(9, 1, 1): {'stringValue': '=literal'}})

    def test_http_upload_has_correct_gzip_header_length_and_exact_cells(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import requests
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                received.append((self.headers['Content-Encoding'], body))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        plan = plan_for([{'sheet': TARGET, 'row': r, 'col': c, 'after': '' if c == 1 else '=text()'}
                         for r in range(1, 1500) for c in (1, 2)])
        payload = self.adapter.prepare_write(plan)
        try:
            self.adapter.base = f'http://127.0.0.1:{server.server_port}/fixture'
            with requests.Session() as session:
                session.trust_env = False  # localhost fixture only; production proxy untouched
                self.adapter.session = session
                self.adapter.write(plan, prepared=payload)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0][0], 'gzip')
            self.assertEqual(gzip.decompress(received[0][1]), payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_oversize_is_rejected_before_any_network_request(self):
        plan = plan_for([{'sheet': TARGET, 'row': 1, 'col': 1, 'after': 'x' * MAX_GOOGLE_WRITE_BYTES}])
        with patch.object(self.adapter, 'request') as request:
            with self.assertRaisesRegex(AppError, 'Запись не начата'):
                self.adapter.write(plan)
        request.assert_not_called()

    def test_new_sheet_id_does_not_collide_and_grid_can_expand(self):
        plan = plan_for([{'sheet': TARGET, 'row': 2001, 'col': 1, 'after': 1},
                         {'sheet': GARAGE, 'row': 1, 'col': 1, 'after': '=literal'}])
        plan['after'] = {TARGET: [['']] * 2001, GARAGE: [['=literal']]}
        body = json.loads(self.adapter.prepare_write(plan))
        self.assertEqual(body['requests'][0]['updateSheetProperties']['properties']['gridProperties']['rowCount'], 2001)
        self.assertEqual(body['requests'][1]['addSheet']['properties']['sheetId'], 11)
        self.assertEqual(cells_from(json.dumps(body)), {(9, 2001, 1): {'numberValue': 1}, (11, 1, 1): {'stringValue': '=literal'}})

    def test_snapshot_does_not_download_or_store_cell_formatting(self):
        meta = {'spreadsheetId': 'test', 'sheets': [{'properties': {'title': TARGET, 'sheetId': 9}}]}
        properties = {'title': TARGET, 'sheetId': 9, 'gridProperties': {'rowCount': 1000, 'columnCount': 30}}
        cell = {'userEnteredValue': {'formulaValue': '=1+1'}, 'effectiveValue': {'numberValue': 2},
                'userEnteredFormat': {'unneeded': 'x' * 1_000_000}}
        data = {'sheets': [{'properties': properties, 'data': [{'rowData': [{'values': [cell, {}]}, {'values': [{}, {}]}]}]}]}
        self.adapter.id = 'test'
        with patch.object(self.adapter, 'check', return_value=meta), patch.object(self.adapter, 'request', return_value=data) as request:
            snapshot = self.adapter.snapshot()
        fields = request.call_args.kwargs['params']['fields']
        self.assertIn('userEnteredValue,effectiveValue', fields)
        self.assertNotIn('Format', fields)
        self.assertEqual(snapshot['sheets'][TARGET]['raw'], [['=1+1']])
        self.assertEqual(snapshot['sheets'][TARGET]['values'], [[2]])
        self.assertNotIn('data', snapshot['metadata']['sheets'][0])
        self.assertLess(len(json.dumps(snapshot)), 1000)
        changed = copy.deepcopy(data)
        changed['sheets'][0]['data'][0]['rowData'][0]['values'][0]['effectiveValue'] = {'numberValue': 3}
        with patch.object(self.adapter, 'check', return_value=meta), patch.object(self.adapter, 'request', return_value=changed):
            self.assertEqual(snapshot['fingerprint'], self.adapter.snapshot()['fingerprint'])
        changed['sheets'][0]['data'][0]['rowData'][0]['values'][0]['userEnteredValue'] = {'formulaValue': '=2+1'}
        with patch.object(self.adapter, 'check', return_value=meta), patch.object(self.adapter, 'request', return_value=changed):
            self.assertNotEqual(snapshot['fingerprint'], self.adapter.snapshot()['fingerprint'])


class CompressedPlanTests(unittest.TestCase):
    def test_compressed_plan_roundtrip_and_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'plans').mkdir()
            plan = plan_for([{'sheet': TARGET, 'row': n, 'col': 1, 'before': 'До', 'after': 'После'} for n in range(10000)])
            plan['checksum'] = digest(plan)
            path = save_plan(folder, plan)
            self.assertTrue(path.name.endswith('.json.gz'))
            self.assertLess(path.stat().st_size, len(json.dumps(plan).encode()) // 10)
            restored = load_plan(folder, plan['id'])
            self.assertEqual(restored, plan)
            self.assertEqual(restored.pop('checksum'), digest(restored))
            self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_corrupted_and_oversized_compressed_plan_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'plans').mkdir()
            path = plan_path(folder, 'test')
            path.write_bytes(b'damaged')
            with self.assertRaisesRegex(AppError, 'повреждён'):
                load_plan(folder, 'test')
            path.write_bytes(gzip.compress(b'x' * 1001))
            with patch('shoko.plans.MAX_PLAN_BYTES', 1000):
                with self.assertRaisesRegex(AppError, 'слишком большой'):
                    load_plan(folder, 'test')

    def test_small_legacy_plans_stay_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'plans').mkdir()
            plan = plan_for([])
            (Path(folder) / 'plans' / (plan['id'] + '.json')).write_text(json.dumps(plan), encoding='utf-8')
            self.assertEqual(load_plan(folder, plan['id']), plan)
