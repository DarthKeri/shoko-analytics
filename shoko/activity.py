"""Local daily activity. Polling and previews never count as launches or updates."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .storage import atomic_json

MOSCOW = timezone(timedelta(hours=3))
START_MESSAGE = 'Приложение запущено. Запись выполняется только после просмотра и подтверждения.'


def moment(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Activity timestamp must include a timezone')
    return parsed.astimezone(MOSCOW)


class ActivityLedger:
    def __init__(self, root):
        self.path = Path(root) / 'logs' / 'activity.json'
        self.launches = {}
        self.error = ''
        try:
            if self.path.exists():
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                if saved.get('version') != 1 or not isinstance(saved.get('launches'), dict):
                    raise ValueError('Invalid activity file')
                for day, count in saved['launches'].items():
                    if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day or type(count) is not int or count < 0:
                        raise ValueError('Invalid daily activity')
                self.launches = saved['launches']
            else:
                # Import historical launch messages once, before the new launch is logged.
                log = Path(root) / 'logs' / 'application.log'
                if log.is_file():
                    with log.open(encoding='utf-8', errors='replace') as stream:
                        for line in stream:
                            match = re.fullmatch(r'\[([^\]]+)\] \[INFO\] ' + re.escape(START_MESSAGE) + r'\s*', line)
                            if match:
                                try:
                                    day = moment(match[1]).date().isoformat()
                                except ValueError:
                                    continue
                                self.launches[day] = self.launches.get(day, 0) + 1
        except (OSError, ValueError, TypeError, AttributeError):
            self.error = 'Статистика запусков недоступна. Проверьте файл logs/activity.json.'

    def record_launch(self, now=None):
        if self.error:
            return False
        day = (now or datetime.now(timezone.utc)).astimezone(MOSCOW).date().isoformat()
        candidate = dict(self.launches)
        candidate[day] = candidate.get(day, 0) + 1
        try:
            atomic_json(self.path, {'version': 1, 'launches': candidate})
        except OSError:
            self.error = 'Не удалось сохранить статистику запусков. Проверьте доступ к рабочей папке.'
            return False
        self.launches = candidate
        return True

    def summary(self, results, now=None):
        today = (now or datetime.now(timezone.utc)).astimezone(MOSCOW).date()
        days = {(today - timedelta(days=offset)).isoformat(): 0 for offset in range(13, -1, -1)}
        for result in results:
            if result.get('status') != 'done':
                continue
            try:
                day = moment(result.get('completed') or result['created']).date().isoformat()
            except (KeyError, ValueError, TypeError):
                continue
            if day in days:
                days[day] += 1
        return {'timezone': 'Europe/Moscow', 'error': self.error,
                'days': [{'date': day, 'launches': self.launches.get(day, 0), 'updates': count} for day, count in days.items()]}
