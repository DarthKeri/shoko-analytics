"""Native, verified Google Drive copies. No source comments are created or edited."""
import copy
import re
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse
from .adapters import google_request_error
from .storage import AppError, atomic_json, digest, stamp

DRIVE = 'https://www.googleapis.com/drive/v3/files/'
SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets/'
SHEET_MIME = 'application/vnd.google-apps.spreadsheet'
FILE_FIELDS = 'id,name,mimeType,parents,driveId,trashed,version,appProperties,capabilities(canCopy,canAddChildren)'


def folder_id(value):
    value = (value or '').strip()
    if value.startswith('https://'):
        parsed = urlparse(value)
        match = re.fullmatch(r'/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)/*', parsed.path)
        if parsed.netloc != 'drive.google.com' or not match:
            raise AppError('Укажите ссылку на папку Google Drive или её ID.')
        value = match.group(1)
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,}', value):
        raise AppError('Укажите папку для резервных копий Google Drive в настройках Google.')
    return value


def post_signature(comment):
    return dict(content=comment.get('content', ''), replies=[dict(content=r.get('content', ''), action=r.get('action', ''))
                for r in comment.get('replies', []) if not r.get('deleted')])


class DriveBackup:
    def __init__(self, adapter, config, progress=lambda text: None):
        self.source = adapter.id
        self.session = adapter.session
        self.folder = folder_id(config.get('google_backup_folder'))
        self.progress = progress

    def request(self, method, url, **kwargs):
        try:
            response = self.session.request(method, url, timeout=(10, 45), **kwargs)
        except Exception as exc:
            raise AppError(google_request_error(exc)) from None
        if response.status_code >= 400:
            # Never expose raw response bodies, JWTs, keys or document contents.
            messages = {400: 'Google отклонил копирование или чтение комментариев. Обновление таблицы заблокировано.',
                        401: 'Google не принял ключ доступа.',
                        403: 'Нет прав на резервное копирование. Включите Google Drive API и предоставьте сервисному аккаунту доступ к таблице и папке копий.',
                        404: 'Таблица или папка копий не найдена. Проверьте ссылку и права доступа.',
                        429: 'Google ограничил частоту запросов. Повторите позже.'}
            raise AppError(messages.get(response.status_code, f'Копирование Google: ошибка HTTP {response.status_code}.'))
        return response.json()

    def metadata(self, identity):
        return self.request('GET', DRIVE + identity, params={'supportsAllDrives': 'true', 'fields': FILE_FIELDS})

    def check(self):
        folder = self.metadata(self.folder)
        if folder.get('trashed') or folder.get('mimeType') != 'application/vnd.google-apps.folder':
            raise AppError('Место для копий должно быть папкой Google Drive.')
        if not folder.get('capabilities', {}).get('canAddChildren'):
            raise AppError('Нет права создавать файлы в папке копий. Проверьте права сервисного аккаунта.')
        source = self.metadata(self.source)
        if source.get('trashed') or source.get('mimeType') != SHEET_MIME:
            raise AppError('Источник резервной копии должен быть Google-таблицей.')
        if not source.get('capabilities', {}).get('canCopy'):
            raise AppError('Google запрещает копировать эту таблицу. Проверьте ограничения владельца.')
        if not source.get('version'):
            raise AppError('Google не вернул версию таблицы. Проверка актуальности копии невозможна.')
        return source

    def comments(self, identity):
        result, tokens, token = [], set(), ''
        while True:
            params = {'pageSize': 100, 'includeDeleted': 'false',
                      'fields': 'nextPageToken,comments(id,content,resolved,deleted,replies(content,action,deleted))'}
            if token:
                params['pageToken'] = token
            page = self.request('GET', DRIVE + identity + '/comments', params=params)
            result.extend(c for c in page.get('comments', []) if not c.get('resolved') and not c.get('deleted'))
            token = page.get('nextPageToken', '')
            if not token:
                break
            if token in tokens or len(tokens) >= 1000:
                raise AppError('Не удалось дочитать комментарии Google. Запись заблокирована.')
            tokens.add(token)
        return result

    def comment_locations(self, identity, comments):
        if not comments:
            return Counter()
        data = self.request('GET', SHEETS + identity, params={'commentsViewMode': 'COMMENTS_VIEW_MODE_INCLUDED',
                            'fields': 'comments,sheets(properties(sheetId,title),commentAnchors)'})
        anchors = {}
        for sheet in data.get('sheets', []):
            for anchor in sheet.get('commentAnchors', []):
                coordinates = copy.deepcopy(anchor.get('range', {}))
                coordinates.pop('sheetId', None)
                anchors[anchor['anchorId']] = {'sheet': sheet['properties']['title'], 'range': coordinates}
        threads = [c for c in data.get('comments', []) if c.get('status') == 'OPEN']
        if len(threads) != len(comments):
            raise AppError('Не удалось сверить все текущие комментарии таблицы. Запись заблокирована.')
        if any(c.get('anchorId') and c['anchorId'] not in anchors for c in threads):
            raise AppError('Не удалось определить ячейки комментариев. Запись заблокирована.')
        return Counter(digest({'location': anchors.get(c.get('anchorId')), 'head': c.get('headPost', {}).get('content', ''),
                               'replies': [r.get('content', '') for r in c.get('replies', [])]}) for c in threads)

    def structure(self, identity):
        data = self.request('GET', SHEETS + identity, params={'fields': 'sheets(properties),properties(locale,timeZone),namedRanges'})
        # Native copy keeps all sheets and formatting; verify every sheet's structure.
        for named_range in data.get('namedRanges', []):
            named_range.pop('namedRangeId', None)
        return digest(data)

    def create(self, store, snapshot, run_id):
        self.progress('Проверяем папку и возможность копирования')
        source = self.check()
        manifest = store.backup_json({'source': self.source, 'source_version': source['version'], 'fingerprint': snapshot['fingerprint']},
                                     'google-drive', self.source, run_id)
        manifest.update(status='creating', folder_id=self.folder)
        path = store.root / 'backups' / manifest['id'] / 'manifest.json'
        atomic_json(path, manifest)
        try:
            self.progress('Читаем текущие комментарии и все листы таблицы')
            comments = self.comments(self.source)
            locations = self.comment_locations(self.source, comments)
            structure = self.structure(self.source)
            if self.metadata(self.source)['version'] != source['version']:
                raise AppError('Таблица изменилась перед копированием. Подготовьте обновление заново.')
            self.progress('Создаём полную копию Google-таблицы с комментариями')
            name = f'{source["name"]} — копия {datetime.now():%Y-%m-%d %H-%M-%S}'
            # Do not retry a mutating request after an unknown timeout. A retained marker
            # identifies a possible orphan for an administrator; source writes never follow.
            marker = {'shokoBackup': manifest['id'], 'shokoSource': self.source}
            copied = self.request('POST', DRIVE + self.source + '/copy',
                                  params={'supportsAllDrives': 'true', 'copyComments': 'true', 'fields': FILE_FIELDS},
                                  json={'name': name, 'parents': [self.folder], 'appProperties': marker})
            identity = copied.get('id', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{10,}', identity) or identity == self.source:
                raise AppError('Google не подтвердил создание отдельной копии. Запись заблокирована.')
            manifest.update(cloud_id=identity, cloud_name=name)
            atomic_json(path, manifest)
            self.progress('Проверяем копию, комментарии и ответы')
            checked = self.metadata(identity)
            if checked.get('trashed') or checked.get('mimeType') != SHEET_MIME or self.folder not in checked.get('parents', []) or any(checked.get('appProperties', {}).get(k) != v for k, v in marker.items()):
                raise AppError('Копия не прошла проверку расположения и происхождения. Запись заблокирована.')
            copied_comments = self.comments(identity)
            expected = Counter(digest(post_signature(c)) for c in comments)
            actual = Counter(digest(post_signature(c)) for c in copied_comments)
            if expected != actual or locations != self.comment_locations(identity, copied_comments):
                raise AppError('В копии не совпадают комментарии, ответы или их ячейки. Запись заблокирована.')
            if self.structure(identity) != structure:
                raise AppError('В копии не совпадает структура листов. Запись заблокирована.')
            if self.metadata(self.source)['version'] != source['version'] or Counter(digest(post_signature(c)) for c in self.comments(self.source)) != expected:
                raise AppError('Таблица или комментарии изменились во время копирования. Подготовьте новый план.')
            manifest.update(status='verified', verified=stamp(), comment_count=len(comments), comment_hash=digest(sorted(expected.elements())), source_version=source['version'])
            atomic_json(path, manifest)
            return manifest
        except Exception as exc:
            manifest.update(status='failed', error=str(exc) if isinstance(exc, AppError) else 'Копирование не завершено. Запись заблокирована.')
            atomic_json(path, manifest)
            raise
