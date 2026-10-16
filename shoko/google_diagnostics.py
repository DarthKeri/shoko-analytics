"""Safe request diagnostics: fixed operation labels, no URLs, IDs, bodies or credentials."""
import re
from urllib.parse import urlparse
from .storage import stamp


def operation(method, url):
    parsed = urlparse(url); path = parsed.path
    if parsed.hostname == 'sheets.googleapis.com':
        return 'Google Sheets', 'Запись таблицы' if method.upper() != 'GET' else 'Чтение таблицы'
    if '/drive/' in path:
        if path.endswith('/copy'): return 'Google Drive', 'Создание резервной копии'
        if path.endswith('/comments'): return 'Google Drive', 'Чтение комментариев'
        if path.endswith('/about'): return 'Google Drive', 'Профиль аккаунта'
        if path.endswith('/files'): return 'Google Drive', 'Список файлов и папок'
        return 'Google Drive', 'Проверка файла или папки'
    return 'Google', 'Подключение к Google'


def reasons(response):
    try:
        error = response.json().get('error', {})
        values = [error.get('status', '')]
        values += [item.get('reason', '') for key in ('errors', 'details') for item in error.get(key, []) if isinstance(item, dict)]
        return ', '.join(sorted({v for v in values if isinstance(v, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,79}', v)}))
    except (ValueError, TypeError, AttributeError):
        return ''


def event(method, url, timeout, elapsed, attempt, attempts, *, response=None, transport='', retry=False):
    from .google_errors import http_error
    service, label = operation(method, url)
    message = http_error(response) if response is not None else ('Не удалось проверить HTTPS-сертификат Google.' if 'SSLError' in transport else 'Истекло время ожидания ответа Google.' if 'Timeout' in transport else 'Google не принял сохранённый вход.' if 'RefreshError' in transport else 'Не удалось установить соединение с Google.')
    connect, read = timeout if isinstance(timeout, (tuple, list)) else (timeout, timeout)
    return {'at': stamp(), 'service': service, 'operation': label, 'method': method.upper(),
            'status': int(response.status_code) if response is not None else None,
            'reason': reasons(response) if response is not None else '', 'transport': transport, 'message': message,
            'uncertain': response is None and method.upper() != 'GET',
            'connect_timeout': connect, 'read_timeout': read, 'elapsed': round(elapsed, 2),
            'attempt': attempt, 'attempts': attempts, 'retry': retry}
