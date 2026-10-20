param([string]$Python = 'python', [string]$OAuthClient = '')
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
    $env:NODE_USE_SYSTEM_CA = '1'
    & npm.cmd ci --prefix frontend --no-audit --no-fund
    if ($LASTEXITCODE) { throw 'npm ci failed' }
    & npm.cmd run build --prefix frontend
    if ($LASTEXITCODE) { throw 'UI build failed' }
    # The portable builder runs the tests on the exact Python shipped in the app.
    & "$PSScriptRoot\build-portable.ps1" -Python $appPython -OAuthClient $OAuthClient
    & "$PSScriptRoot\create-shortcut.ps1"
    Write-Host "Ready: $projectRoot\dist\ShokoAnalytics\ShokoAnalytics.exe"
} finally { Pop-Location }
