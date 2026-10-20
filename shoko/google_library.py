"""Read-only Drive picker and bounded public Google profile photographs."""
import base64
from urllib.parse import urlparse
import requests
from .storage import AppError
from .google_errors import http_error

MIMES = {'sheets': 'application/vnd.google-apps.spreadsheet', 'folders': 'application/vnd.google-apps.folder'}

def avatar_data(url):
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return ''
    if not any(parsed.hostname == host or parsed.hostname.endswith('.' + host) for host in ('googleusercontent.com', 'ggpht.com')):
        return ''
    try:
        with requests.get(url, stream=True, timeout=(10, 15), allow_redirects=False) as response:
            mime = response.headers.get('Content-Type', '').split(';')[0].lower()
            if response.status_code != 200 or mime not in ('image/jpeg', 'image/png', 'image/webp'):
                return ''
            content = bytearray()
            for block in response.iter_content(16_384):
                content.extend(block)
                if len(content) > 262_144:
                    return ''
            return 'data:' + mime + ';base64,' + base64.b64encode(content).decode() if content else ''
    except requests.RequestException:
        return ''  # A photograph must not prevent account connection.

def list_items(auth, config, kind, query='', page_token=''):
    if kind not in MIMES or not isinstance(query, str) or len(query) > 150 or not isinstance(page_token, str) or len(page_token) > 4096:
        raise AppError('Неверные параметры выбора файла Google Drive.')
    if config.get('google_auth_mode') != 'oauth':
        raise AppError('Сначала войдите в свой Google-аккаунт.')
    escaped = query.strip().replace('\\', '\\\\').replace("'", "\\'")
    q = "trashed = false and mimeType = '" + MIMES[kind] + "'"
    if escaped:
        q += " and name contains '" + escaped + "'"
    params = dict(q=q, pageSize=50, orderBy='modifiedTime desc', fields='nextPageToken,files(id,name,webViewLink)', supportsAllDrives='true', includeItemsFromAllDrives='true')
    if page_token:
        params['pageToken'] = page_token
    from .adapters import google_http_request, google_request_error
    with auth.session(config) as session:
        try:
            response = google_http_request(session, 'GET', 'https://www.googleapis.com/drive/v3/files', params=params, timeout=(10, 45))
        except Exception as exc:
            raise AppError(google_request_error(exc)) from None
        if response.status_code >= 400:
            raise AppError(http_error(response))
        data = response.json()
    return {'items': [{'id': item['id'], 'name': item['name']} for item in data.get('files', [])], 'next': data.get('nextPageToken', '')}
