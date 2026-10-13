param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $projectRoot
try {
    if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $Python -m venv .venv
        if ($LASTEXITCODE) { throw 'venv creation failed' }
    }
    $appPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    & $appPython -m pip install -r requirements.txt
    if ($LASTEXITCODE) { throw 'Python dependency install failed' }
    # Node 24 supports Windows system certificate trust without disabling TLS verification.
    $env:NODE_USE_SYSTEM_CA = '1'
    & npm.cmd ci --prefix frontend --no-audit --no-fund
    if ($LASTEXITCODE) { throw 'npm ci failed' }
    & npm.cmd run build --prefix frontend
    if ($LASTEXITCODE) { throw 'UI build failed' }
    & $appPython -m unittest discover -s tests -v
    if ($LASTEXITCODE) { throw 'Tests failed' }
    & $appPython -m PyInstaller --noconfirm ShokoAnalytics.spec
    if ($LASTEXITCODE) { throw 'Executable build failed' }
    & "$projectRoot\dist\ShokoAnalytics\ShokoAnalytics.exe" --smoke --data-dir "$projectRoot\runtime\build-smoke"
    if ($LASTEXITCODE) { throw 'Executable smoke failed' }
    & "$PSScriptRoot\create-shortcut.ps1"
    Write-Host "Ready: $projectRoot\dist\ShokoAnalytics\ShokoAnalytics.exe"
} finally { Pop-Location }
