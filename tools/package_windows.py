"""Transparent Windows folder distribution with a vendor-signed Python runtime."""
import argparse
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
VERSION = '0.1.0'
PYTHON_VERSION = '3.13.16'
PYTHON_SHA256 = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'
PYTHON_URL = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip'


def checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def inside(path, parent):
    path = Path(path).resolve()
    if not path.is_relative_to(parent.resolve()) or path == parent.resolve():
        raise ValueError('Packaging path must stay inside ' + str(parent))
    return path


def assemble(args):
    candidate = inside(args.output, ROOT / 'build')
    if candidate.exists():
        raise ValueError('Build a new candidate; existing folders are never overwritten')
    archive_path = Path(args.python_zip)
    if checksum(archive_path) != PYTHON_SHA256:
        raise ValueError('Official Python archive checksum mismatch')
    candidate.mkdir(parents=True)
    runtime = candidate / 'runtime'
    runtime.mkdir()
    with ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Python archive CRC error')
        for member in archive.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or '..' in relative.parts or '\\' in member.filename:
                raise ValueError('Unsafe Python archive path')
            destination = inside(runtime.joinpath(*relative.parts), runtime)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
    shutil.copytree(args.site_packages, runtime / 'Lib/site-packages')
    # _pth provides an app-private import path; no user Python or PATH is needed.
    (runtime / 'python313._pth').write_text('python313.zip\n.\nLib\nLib/site-packages\n../app\nimport site\n', encoding='ascii')
    app = candidate / 'app'
    app.mkdir()
    shutil.copy2(ROOT / 'main.py', app / 'main.py')
    (app / 'shoko').mkdir()
    for source in (ROOT / 'shoko').glob('*.py'):
        shutil.copy2(source, app / 'shoko' / source.name)
    for folder in ('web', 'assets'):
        shutil.copytree(ROOT / folder, app / folder)
    shutil.copy2(args.launcher, candidate / 'ShokoAnalytics.exe')
    shutil.copy2(ROOT / 'requirements-runtime.txt', candidate / 'requirements-runtime.txt')
    forbidden = {'.vault', '.xlsx', '.xls', '.csv', '.session'}
    files = sorted(p for p in candidate.rglob('*') if p.is_file())
    for path in files:
        if path.suffix.lower() in forbidden or path.name == 'settings.local.json':
            raise ValueError('User data must not be included: ' + path.name)
    manifest = {'product': 'Shoko Analytics', 'version': VERSION, 'format': 'python-folder',
                'python': {'version': PYTHON_VERSION, 'url': PYTHON_URL, 'archive_sha256': PYTHON_SHA256},
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'files': {p.relative_to(candidate).as_posix(): checksum(p) for p in files}}
    (candidate / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'candidate': str(candidate), 'files': len(files) + 1, 'version': VERSION}))


def verify(candidate):
    manifest = json.loads((candidate / 'build-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != VERSION or manifest.get('format') != 'python-folder':
        raise ValueError('Unexpected distribution manifest')
    required = {'ShokoAnalytics.exe', 'runtime/python.exe', 'runtime/pythonw.exe', 'runtime/python313.dll', 'app/main.py', 'app/shoko/__init__.py'}
    if not required.issubset(manifest['files']):
        raise ValueError('Required application files missing from manifest')
    actual = {p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file() and p.name != 'build-manifest.json'}
    if actual != set(manifest['files']):
        raise ValueError('Unlisted or missing distribution files')
    for name, expected in manifest['files'].items():
        path = inside(candidate / name, candidate)
        if checksum(path) != expected:
            raise ValueError('Build file changed: ' + name)
    return manifest


def publish(args):
    candidate = inside(args.candidate, ROOT / 'build')
    # Integration subprocesses can generate caches. Ship the recorded files only.
    listed = json.loads((candidate / 'build-manifest.json').read_text(encoding='utf-8'))['files']
    for cache in candidate.rglob('*.pyc'):
        if '__pycache__' in cache.parts and cache.relative_to(candidate).as_posix() not in listed:
            inside(cache, candidate).unlink()
    manifest = verify(candidate)
    output = inside(ROOT / 'dist/ShokoAnalytics', ROOT / 'dist')
    output.parent.mkdir(exist_ok=True)
    package = output.parent / ('ShokoAnalytics-' + VERSION + '-Windows.zip')
    temporary = package.with_name(package.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with ZipFile(temporary, 'w', compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(candidate.rglob('*')):
                if path.is_file():
                    archive.write(path, PurePosixPath('ShokoAnalytics') / path.relative_to(candidate).as_posix())
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Distribution archive CRC error')
        # Only a complete, verified archive may precede installation replacement.
        if output.exists():
            old = inside(ROOT / 'build' / ('previous-distribution-' + uuid.uuid4().hex), ROOT / 'build')
            output.rename(old)
        try:
            candidate.rename(output)
            temporary.replace(package)
        except Exception:
            if output.exists() and not candidate.exists():
                output.rename(candidate)
            if 'old' in locals():
                old.rename(output)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    result = {'version': VERSION, 'format': 'python-folder', 'files': len(manifest['files']) + 1,
              'zip_bytes': package.stat().st_size, 'zip_sha256': checksum(package),
              'exe_sha256': checksum(output / 'ShokoAnalytics.exe')}
    (output.parent / 'SHA256SUMS.txt').write_text(result['zip_sha256'] + '  ' + package.name + '\n' + result['exe_sha256'] + '  ShokoAnalytics/ShokoAnalytics.exe\n', encoding='ascii')
    result_path = inside(ROOT / 'runtime/portable-build/build-result.json', ROOT)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('assemble')
    build.add_argument('--output', required=True)
    build.add_argument('--python-zip', required=True)
    build.add_argument('--site-packages', required=True)
    build.add_argument('--launcher', required=True)
    release = commands.add_parser('publish')
    release.add_argument('--candidate', required=True)
    args = parser.parse_args()
    assemble(args) if args.command == 'assemble' else publish(args)
