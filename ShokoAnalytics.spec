# Windows folder distribution: keep the entire dist/ShokoAnalytics folder.
from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root / 'main.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'web'), 'web')],
    hiddenimports=['webview.platforms.edgechromium', 'google.oauth2.service_account', 'google.auth.transport.requests', 'telethon', 'win32crypt', 'truststore._windows'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['tkinter', 'matplotlib', 'pandas'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ShokoAnalytics', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ShokoAnalytics')
