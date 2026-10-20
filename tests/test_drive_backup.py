import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from requests.exceptions import ReadTimeout
from shoko.drive_backup import DriveBackup, DRIVE, SHEETS, SHEET_MIME, folder_id
from shoko.storage import Store, AppError, digest, atomic_json
from shoko.engine import Engine

SOURCE = 'test_source_spreadsheet_12345'
FOLDER = 'test_shared_drive_folder_12345'
COPY = 'test_backup_spreadsheet_12345'


class DriveFixture:
    def __init__(self):
        self.calls = []
        self.posts = []
        self.version = '10'
        self.folder = {'id': FOLDER, 'name': 'Копии', 'mimeType': 'application/vnd.google-apps.folder',
                       'driveId': 'shared_drive', 'capabilities': {'canAddChildren': True}}
        self.comments = [{'id': 'one', 'content': 'Проверить цену', 'resolved': False,
                          'replies': [{'content': 'Проверено', 'action': ''}]},
                         {'id': 'two', 'content': 'Одинаковые комментарии учитываются отдельно', 'resolved': False},
                         {'id': 'closed', 'content': 'Закрытый', 'resolved': True}]
        self.copy_comments = None
        self.copy_id = COPY
        self.marker = {}
        self.fail_copy = False
        self.copy_timeout = False
        self.wrong_anchor = False
        self.wrong_structure = False
        self.changed_source = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, copy.deepcopy(kwargs)))
        params = kwargs.get('params', {})
        identity = url.split('/')[-1]
        status = 200
        if method == 'POST':
            self.posts.append((url, copy.deepcopy(kwargs)))
            if self.copy_timeout:
                raise ReadTimeout('sensitive transport body')
            self.marker = kwargs['json']['appProperties']
            status = 403 if self.fail_copy else 200
            result = {'id': self.copy_id}
        elif url == DRIVE + FOLDER:
            result = self.folder
        elif url.startswith(DRIVE) and identity == 'comments':
            is_copy = url == DRIVE + COPY + '/comments'
            values = self.copy_comments if is_copy and self.copy_comments is not None else self.comments
            result = {'comments': values[1:]} if params.get('pageToken') else {'comments': values[:1], 'nextPageToken': 'page-two'}
        elif url in (DRIVE + SOURCE, DRIVE + COPY):
            is_copy = identity == COPY
            result = {'id': identity, 'name': 'Рабочая таблица', 'mimeType': SHEET_MIME,
                      'version': '11' if self.changed_source and self.posts and not is_copy else self.version,
                      'capabilities': {'canCopy': True}}
            if is_copy:
                result.update(parents=[FOLDER], appProperties=self.marker, driveId='shared_drive')
        elif url.startswith(SHEETS) and params.get('commentsViewMode'):
            is_copy = identity == COPY
            values = self.copy_comments if is_copy and self.copy_comments is not None else self.comments
            values = [c for c in values if not c.get('resolved') and not c.get('deleted')]
            result = {'sheets': [{'properties': {'sheetId': 0, 'title': 'Предложение цен'},
                       'commentAnchors': [{'anchorId': f'a{i}', 'range': {'sheetId': 0, 'startRowIndex': i + (5 if is_copy and self.wrong_anchor else 0)}} for i in range(len(values))]}],
                      'comments': [{'status': 'OPEN', 'anchorId': f'a{i}', 'headPost': {'content': c['content']},
                                    'replies': [{'content': r['content']} for r in c.get('replies', [])]} for i, c in enumerate(values)]}
        elif url.startswith(SHEETS):
            result = {'sheets': [{'properties': {'title': 'Предложение цен', 'sheetId': 0}},
                                 {'properties': {'title': 'Другой лист', 'sheetId': 1}}],
                      'properties': {'locale': 'ru_RU'}}
            if identity == COPY and self.wrong_structure:
                result['sheets'].pop()
        else:
            raise AssertionError((method, url, kwargs))
        return SimpleNamespace(status_code=status, json=lambda: copy.deepcopy(result))


class DriveBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.fixture = DriveFixture()
        self.adapter = SimpleNamespace(id=SOURCE, session=self.fixture)
        self.backup = DriveBackup(self.adapter, {'google_backup_folder': FOLDER})

    def tearDown(self):
        self.temp.cleanup()

    def create(self):
        return self.backup.create(self.store, {'fingerprint': 'approved-fingerprint'}, 'test-run')

    def test_full_native_copy_checks_comments_replies_locations_and_all_sheets(self):
        result = self.create()
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['cloud_id'], COPY)
        self.assertEqual(result['comment_count'], 2)
        self.assertEqual(len(self.fixture.posts), 1)
        url, request = self.fixture.posts[0]
        self.assertEqual(url, DRIVE + SOURCE + '/copy')
        self.assertEqual(request['params']['copyComments'], 'true')
        self.assertEqual(request['params']['supportsAllDrives'], 'true')
        self.assertEqual(request['json']['parents'], [FOLDER])
        self.store.copy(result['id'])
        # Copy is the only cloud mutation; no source cells/comments are edited.
        self.assertTrue(all(method == 'GET' or url.endswith('/copy') for method, url, _ in self.fixture.calls))
        self.assertTrue(any(c[2].get('params', {}).get('pageToken') for c in self.fixture.calls))

    def test_readonly_status_never_creates_files(self):
        self.backup.check()
        self.assertEqual(len(self.fixture.posts), 0)
        self.assertEqual(self.store.copies(), [])

    def test_accessible_my_drive_folder_is_accepted_for_backup(self):
        del self.fixture.folder['driveId']
        self.backup.check()
        self.assertEqual(self.fixture.posts, [])  # Status remains read-only.
        self.assertEqual(self.create()['status'], 'verified')
        self.assertEqual(len(self.fixture.posts), 1)

    def test_folder_without_create_permission_blocks_copy(self):
        self.fixture.folder['capabilities']['canAddChildren'] = False
        with self.assertRaisesRegex(AppError, 'Нет права'):
            self.create()
        self.assertEqual(self.fixture.posts, [])

    def test_missing_comment_or_reply_fails_closed_and_retains_cloud_link(self):
        for value in ([self.fixture.comments[0]], [dict(self.fixture.comments[0], replies=[]), self.fixture.comments[1]]):
            self.fixture.copy_comments = value
            with self.assertRaisesRegex(AppError, 'не совпадают комментарии'):
                self.create()
            self.assertEqual(self.store.copies()[0]['status'], 'failed')
            self.assertEqual(self.store.copies()[0]['cloud_id'], COPY)

    def test_moved_comment_or_missing_sheet_blocks_write(self):
        self.fixture.wrong_anchor = True
        with self.assertRaisesRegex(AppError, 'ячейки'):
            self.create()
        self.fixture.wrong_anchor = False
        self.fixture.wrong_structure = True
        with self.assertRaisesRegex(AppError, 'структура листов'):
            self.create()

    def test_source_change_during_copy_blocks_write(self):
        self.fixture.changed_source = True
        with self.assertRaisesRegex(AppError, 'изменились во время'):
            self.create()

    def test_copy_permission_failure_is_recorded_and_sanitized(self):
        self.fixture.fail_copy = True
        with self.assertRaisesRegex(AppError, 'Google Drive API'):
            self.create()
        self.assertEqual(self.store.copies()[0]['status'], 'failed')

    def test_unknown_post_timeout_is_never_retried(self):
        self.fixture.copy_timeout = True
        with self.assertRaisesRegex(AppError, 'не подтвердил создание копии') as caught:
            self.create()
        self.assertIn('180 секунд', str(caught.exception))
        self.assertIn('Копия могла быть создана', str(caught.exception))
        self.assertIn('Автоматический повтор', str(caught.exception))
        self.assertEqual(len(self.fixture.posts), 1)
        self.assertNotIn('sensitive', self.store.copies()[0]['error'])
        self.assertEqual(self.store.copies()[0]['status'], 'failed')

    def test_empty_comments_and_duplicate_contents(self):
        self.fixture.comments = []
        self.assertEqual(self.create()['comment_count'], 0)
        self.fixture.comments = [self.fixture.comments[0]] if self.fixture.comments else [{'id': 'a', 'content': 'same'}, {'id': 'b', 'content': 'same'}]
        self.assertEqual(self.create()['comment_count'], 2)
        self.fixture.copy_comments = [{'id': 'c', 'content': 'same'}]
        with self.assertRaises(AppError):
            self.create()

    def test_folder_urls_are_parsed_without_accepting_external_hosts(self):
        self.assertEqual(folder_id('https://drive.google.com/drive/folders/' + FOLDER + '?usp=sharing'), FOLDER)
        for value in ['https://evil.example/drive/folders/' + FOLDER, '', '../x']:
            with self.assertRaises(AppError):
                folder_id(value)

    def test_interrupted_copy_is_not_displayed_as_running_after_restart(self):
        manifest = self.store.backup_json({'source': SOURCE}, 'google-drive', SOURCE)
        path = Path(manifest['path']) / 'manifest.json'
        manifest.update(status='creating', cloud_id=COPY)
        atomic_json(path, manifest)
        reopened = Store(self.temp.name)
        saved = reopened.copies()[0]
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['cloud_id'], COPY)
        self.assertIn('закрыто', saved['error'])


class GoogleWriteGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name)
        self.engine.save_settings({'target_mode': 'google', 'target': SOURCE, 'credentials': 'fixture.json', 'google_auth_mode': 'service_account',
                                   'google_backup_folder': FOLDER, 'test_copy_confirmed': True})
        self.fixture = DriveFixture()
        self.snapshot = {'mode': 'google', 'target': SOURCE, 'fingerprint': 'reviewed', 'sheets': {}}
        self.adapter = SimpleNamespace(id=SOURCE, session=self.fixture, snapshot=Mock(return_value=self.snapshot), prepare_write=Mock(return_value=b'prepared'), write=Mock(), verify=Mock())
        self.plan = {'id': 'fixture-plan', 'date': '2026-10-14', 'url': 'https://garage.example.test', 'created': '2026-10-14',
                     'warnings': [], 'before': self.snapshot, 'after': {}, 'changes': [{'sheet':'Fixture','row':1,'col':1,'after':1}], 'sources': {}, 'config_digest': self.engine.plan_config_digest()}
        self.plan['checksum'] = digest(self.plan)
        self.engine.plan = self.plan
        self.engine.state['phase'] = 'preview'
        atomic_json(self.engine.store.root / 'plans' / 'fixture-plan.json', self.plan)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def write(self):
        with patch('shoko.engine.target', return_value=self.adapter):
            self.engine.write(self.plan['id'], self.plan['checksum'])
            if self.engine.worker:
                self.engine.worker.join(10)
        self.assertFalse(self.engine.busy())

    def test_google_write_only_after_verified_cloud_backup(self):
        def write(plan, prepared=None):
            manifest = self.engine.store.copies()[0]
            self.assertEqual(manifest['status'], 'verified')
            self.assertEqual(manifest['cloud_id'], COPY)
            self.assertEqual(prepared, b'prepared')
        self.adapter.write.side_effect = write
        self.write()
        self.adapter.write.assert_called_once()
        self.adapter.verify.assert_called_once()
        self.assertEqual(self.engine.state['phase'], 'done', self.engine.state['error'])

    def test_comment_copy_failure_prevents_any_sheet_write(self):
        self.fixture.copy_comments = []
        self.write()
        self.adapter.write.assert_not_called()
        self.assertEqual(self.engine.state['phase'], 'error')
        self.assertEqual(self.engine.store.copies()[0]['status'], 'failed')

    def test_write_connection_failure_keeps_backup_and_never_retries(self):
        self.adapter.write.side_effect = AppError('Google не подтвердил результат записи. Код: ConnectionError / TimeoutError.')
        self.write()
        self.adapter.write.assert_called_once()
        self.adapter.verify.assert_not_called()
        self.assertEqual(self.engine.store.copies()[0]['status'], 'verified')
        self.assertEqual(self.engine.state['phase'], 'error')
        self.assertIn('Запись изменений:', self.engine.state['error'])
        history = self.engine.public_state()['history'][0]
        self.assertEqual(history['status'], 'uncertain')
        self.assertIn('ConnectionError / TimeoutError', history['message'])
        with self.assertRaises(AppError):
            self.engine.write(self.plan['id'], self.plan['checksum'])

    def test_verification_failure_is_distinct_from_write_failure(self):
        self.adapter.verify.side_effect = AppError('Не удалось прочитать Google.')
        self.write()
        self.adapter.write.assert_called_once()
        self.assertIn('Сверка результата:', self.engine.state['error'])
        self.assertEqual(self.engine.store.copies()[0]['status'], 'verified')

    def test_preflight_failure_creates_no_backup_and_sends_no_write(self):
        self.adapter.prepare_write.side_effect = AppError('Объём изменений превышает предел')
        self.write()
        self.adapter.write.assert_not_called()
        self.assertEqual(self.engine.store.copies(), [])
        self.assertEqual(self.engine.state['phase'], 'error')

    def test_confirmation_required_before_any_preflight_or_backup(self):
        self.engine.state['phase'] = 'idle'
        with self.assertRaises(AppError):
            self.engine.write(self.plan['id'], self.plan['checksum'])
        self.adapter.prepare_write.assert_not_called()
        self.assertEqual(self.engine.store.copies(), [])

    def test_partial_progress_is_durable_and_not_replayed_after_restart(self):
        def fail_after_first_part(plan, prepared=None):
            self.adapter.write_progress_callback({'total': 3, 'confirmed': 0, 'in_flight': 1})
            self.adapter.write_progress_callback({'total': 3, 'confirmed': 1, 'in_flight': None})
            self.adapter.write_progress_callback({'total': 3, 'confirmed': 1, 'in_flight': 2})
            raise AppError('Google подтвердил 1 из 3 частей; результат части 2 неизвестен.')
        self.adapter.write.side_effect = fail_after_first_part
        self.write()
        self.assertEqual(self.engine.state['phase'], 'error')
        result = self.engine.public_state()['history'][0]
        self.assertEqual(result['write_progress'], {'total': 3, 'confirmed': 1, 'in_flight': 2})
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(self.engine.store.copies()[0]['status'], 'verified')
        self.adapter.verify.assert_not_called()
        self.engine.close()
        self.engine = Engine(self.temp.name)
        self.assertEqual(self.engine.public_state()['history'][0]['write_progress'], result['write_progress'])
        self.adapter.write.assert_called_once()

    def test_interrupted_result_preserves_last_confirmed_part(self):
        atomic_json(self.engine.store.root / 'plans' / 'interrupted.result.json', {
            'id': 'interrupted', 'created': '2026-10-16T00:00:00+00:00', 'status': 'writing',
            'write_progress': {'total': 4, 'confirmed': 2, 'in_flight': 3}})
        self.engine.close()
        self.engine = Engine(self.temp.name)
        result = next(r for r in self.engine.public_state()['history'] if r['id'] == 'interrupted')
        self.assertEqual(result['status'], 'interrupted')
        self.assertIn('Google подтвердил 2 из 4 частей', result['message'])
        self.assertEqual(result['write_progress']['in_flight'], 3)
        self.adapter.write.assert_not_called()

    def test_changed_batch_summary_blocks_before_backup(self):
        from shoko.plans import save_plan
        self.plan.pop('checksum')
        self.plan['google_write'] = {'parts': 2, 'bytes': 100}
        self.plan['checksum'] = digest(self.plan)
        save_plan(self.engine.store.root, self.plan)
        self.write()
        self.assertIn('Разбиение записи изменилось', self.engine.state['error'])
        self.assertEqual(self.engine.store.copies(), [])
        self.adapter.write.assert_not_called()

    def test_all_parts_confirmed_is_not_success_until_full_verification(self):
        def complete_parts(plan, prepared=None):
            self.adapter.write_progress_callback({'total': 3, 'confirmed': 3, 'in_flight': None})
        self.adapter.write.side_effect = complete_parts
        self.adapter.verify.side_effect = AppError('Ошибка сверки Google: тестовая ячейка.')
        self.write()
        result = self.engine.public_state()['history'][0]
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(result['write_progress']['confirmed'], 3)
        self.assertEqual(self.engine.state['phase'], 'error')

    def test_all_status_checks_include_cloud_folder_but_never_copy(self):
        with patch.object(self.engine, 'check_one') as check:
            self.engine.check('all', '2026-10-14')
            if self.engine.worker:
                self.engine.worker.join(10)
        self.assertIn('backup', [call.args[0] for call in check.call_args_list])
        self.assertEqual(self.engine.store.copies(), [])
