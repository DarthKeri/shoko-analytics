"""Actionable Google errors without raw responses, tokens or authorization codes."""


def http_error(response):
    try:
        error = response.json().get('error', {})
        reasons = {item.get('reason') for item in error.get('errors', [])}
        reasons.update(item.get('reason') for item in error.get('details', []))
    except (ValueError, TypeError, AttributeError):
        reasons = set()
    groups = [
        ({'storageQuotaExceeded'}, 'Google не разрешил создать копию: у подключённого аккаунта нет доступной квоты Google Drive. Для обычной папки используйте вход через свой Google-аккаунт и проверьте его хранилище.'),
        ({'accessNotConfigured', 'SERVICE_DISABLED'}, 'В проекте Google Cloud не включён нужный API. Включите Google Drive API и Google Sheets API.'),
        ({'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'insufficientPermissions'}, 'Google не предоставил необходимые разрешения. Войдите в Google заново и разрешите доступ к Drive и таблицам.'),
        ({'insufficientFilePermissions'}, 'У подключённого Google-аккаунта нет прав на выбранную таблицу или папку копий.'),
        ({'rateLimitExceeded', 'userRateLimitExceeded'}, 'Google ограничил частоту запросов. Повторите позже.'),
        ({'domainPolicy', 'ORG_RESTRICTION_VIOLATION'}, 'Доступ ограничен правилами организации Google. Обратитесь к администратору домена.'),
    ]
    for codes, message in groups:
        matched = sorted(codes & reasons)
        if matched:
            return f'{message} (HTTP {response.status_code}; {matched[0]})'
    messages = {400: 'Google отклонил запрос. Проверьте параметры таблицы и копирования.',
                401: 'Google не принял авторизацию. Войдите в аккаунт заново.',
                403: 'Google запретил операцию. Проверьте разрешения аккаунта, доступ к файлам и включение Google Drive API.',
                404: 'Google-таблица или папка не найдена либо недоступна подключённому аккаунту.',
                429: 'Google ограничил частоту запросов. Повторите позже.'}
    return f'{messages.get(response.status_code, "Не удалось выполнить запрос к Google.")} (HTTP {response.status_code})'
