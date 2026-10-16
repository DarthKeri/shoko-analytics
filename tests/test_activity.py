import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from shoko.activity import ActivityLedger, START_MESSAGE
from shoko.engine import Engine
from shoko.server import Server
from shoko.storage import AppError, atomic_json, file_hash
from test_mvp import fixtures

NOW = datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)


class ActivityTests(unittest.TestCase):
    def test_legacy_launches_are_imported_once_with_moscow_day(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp, 'logs/application.log'); log.parent.mkdir()
            log.write_text(f'[2026-10-15T22:30:00+00:00] [INFO] {START_MESSAGE}\n'
                           f'[2026-10-15T20:00:00+00:00] [INFO] {START_MESSAGE}\n'
                           '[2026-10-15T22:40:00+00:00] [INFO] Проверка источника csv: доступен.\n', encoding='utf-8')
            ledger = ActivityLedger(temp)
            ledger.record_launch(NOW)
            self.assertEqual(ledger.summary([], NOW)['days'][-1]['launches'], 2)
            before = file_hash(ledger.path)
            for _ in range(5):
                ActivityLedger(temp).summary([], NOW)
            self.assertEqual(file_hash(ledger.path), before)
            self.assertEqual(ActivityLedger(temp).launches, {'2026-10-15': 1, '2026-10-16': 2})

    def test_only_verified_updates_count_on_completion_day_without_history_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            results = [{'status': 'done', 'date': '2026-08-20', 'created': '2026-10-15T20:00:00+00:00', 'completed': '2026-10-15T22:00:00+00:00'} for _ in range(105)]
            results += [{'status': status, 'created': NOW.isoformat()} for status in ('writing', 'uncertain', 'interrupted')]
            results += [{'status': 'done', 'created': '2026-10-14T20:00:00+00:00'}, {'status': 'done', 'created': 'bad-date'}]
            summary = ActivityLedger(temp).summary(results, NOW)
            self.assertEqual(len(summary['days']), 14)
            self.assertEqual(summary['days'][-1], {'date': '2026-10-16', 'launches': 0, 'updates': 105})
            self.assertEqual(summary['days'][-3]['updates'], 1)
            self.assertEqual(sum(d['updates'] for d in summary['days']), 106)

    def test_corrupt_statistics_are_reported_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, 'logs/activity.json'); path.parent.mkdir(); path.write_text('broken', encoding='utf-8')
            ledger = ActivityLedger(temp)
            self.assertFalse(ledger.record_launch(NOW))
            self.assertTrue(ledger.summary([], NOW)['error'])
            self.assertEqual(path.read_text(encoding='utf-8'), 'broken')

    def test_smoke_and_polling_do_not_add_launches(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(temp, record_launch=False)
            for _ in range(3):
                self.assertEqual(sum(d['launches'] for d in engine.public_state()['activity']['days']), 0)
            engine.close()
            engine = Engine(temp)
            for _ in range(3):
                self.assertEqual(sum(d['launches'] for d in engine.public_state()['activity']['days']), 1)
            engine.close()
            engine = Engine(temp)
            self.assertEqual(sum(d['launches'] for d in engine.public_state()['activity']['days']), 2)
            engine.close()

    def test_preference_survives_restart_without_finishing_setup_or_making_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(temp)
            server = Server(engine, temp)
            try:
                self.assertEqual(server.action({'action': 'chart-type', 'value': 'bars'}), {'chart_type': 'bars'})
                self.assertFalse(engine.config()['initialized'])
                with self.assertRaises(AppError): server.action({'action': 'chart-type', 'value': 'unknown'})
                self.assertEqual(engine.config()['chart_type'], 'bars')
            finally:
                server.close(); engine.close()
            engine = Engine(temp)
            self.assertEqual(engine.config()['chart_type'], 'bars')
            self.assertEqual(engine.store.copies(), [])
            self.assertEqual(len(list(Path(temp).glob('settings*'))), 1)
            engine.close()

    def test_switching_after_review_keeps_approval_backup_and_verified_write(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = fixtures(Path(temp, 'files'))
            engine = Engine(Path(temp, 'data'))
            try:
                engine.save_settings(paths)
                engine.prepare('', paths['last_date'], False)
                engine.worker.join(15)
                self.assertEqual(engine.state['phase'], 'preview', engine.state['error'])
                self.assertEqual(sum(d['updates'] for d in engine.public_state()['activity']['days']), 0)
                plan = engine.plan
                engine.set_chart_type('bars')
                self.assertIs(engine.plan, plan)
                self.assertEqual(engine.plan_config_digest(), plan['config_digest'])
                engine.write(plan['id'], plan['checksum']); engine.worker.join(15)
                self.assertEqual(engine.state['phase'], 'done', engine.state['error'])
                result = engine.public_state()['history'][0]
                self.assertTrue(result['completed'])
                self.assertTrue(engine.store.copy(result['backup']))
                self.assertEqual(sum(d['updates'] for d in engine.public_state()['activity']['days']), 1)
                engine.public_state()
                self.assertEqual(sum(d['updates'] for d in engine.public_state()['activity']['days']), 1)
            finally: engine.close()


if __name__ == '__main__':
    unittest.main()
