"""Atomic compressed review plans; no credentials or Google cell formatting."""
import gzip
import io
import json
import os
import uuid
import zlib
from pathlib import Path
from .storage import AppError

MAX_PLAN_BYTES = 128 * 1024 * 1024


def plan_path(root, identity):
    if not isinstance(identity, str) or not identity or not all(c.isalnum() or c == '-' for c in identity):
        raise AppError('Неверный идентификатор плана.')
    return Path(root) / 'plans' / (identity + '.json.gz')


def save_plan(root, plan):
    path = plan_path(root, plan['id'])
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            with gzip.GzipFile(fileobj=stream, mode='wb', compresslevel=6, mtime=0) as compressed:
                with io.TextIOWrapper(compressed, encoding='utf-8') as writer:
                    encoder = json.JSONEncoder(ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                    size = 0
                    for chunk in encoder.iterencode(plan):
                        size += len(chunk.encode('utf-8'))
                        if size > MAX_PLAN_BYTES:
                            raise AppError('План превышает допустимый объём. Выберите меньший набор данных.')
                        writer.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def load_plan(root, identity):
    path = plan_path(root, identity)
    try:
        if path.exists():
            with gzip.open(path, 'rb') as stream:
                content = stream.read(MAX_PLAN_BYTES + 1)
            if len(content) > MAX_PLAN_BYTES:
                raise AppError('Сохранённый план слишком большой. Подготовьте обновление заново.')
        else:
            # Small older plans remain readable; never load a legacy 2 GB snapshot.
            path = path.with_suffix('')
            if path.stat().st_size > MAX_PLAN_BYTES:
                raise AppError('План старой сборки слишком большой. Подготовьте обновление заново.')
            content = path.read_bytes()
        return json.loads(content)
    except (OSError, EOFError, ValueError, UnicodeError, zlib.error):
        raise AppError('Сохранённый план повреждён или недоступен. Подготовьте обновление заново.') from None
