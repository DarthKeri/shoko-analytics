param([string]$Python, [switch]$StageOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (!$Python) { $Python = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$cache = Join-Path $projectRoot 'runtime\portable-build'
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$pythonZip = Join-Path $cache 'python-3.13.16-embed-amd64.zip'
if (!(Test-Path -LiteralPath $pythonZip)) {
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip' -OutFile $pythonZip
}
if ((Get-FileHash -LiteralPath $pythonZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297') {
    throw 'Official Python archive checksum mismatch'
}
$buildId = [guid]::NewGuid().ToString('N')
$sitePackages = Join-Path $projectRoot ('build\portable-dependencies-' + $buildId)
$wheels = Join-Path $cache 'wheels'
New-Item -ItemType Directory -Path $wheels -Force | Out-Null
# These two pure Python packages publish source archives; build their wheels first.
& $Python -m pip wheel --disable-pip-version-check --no-deps --wheel-dir $wheels 'pyaes==1.6.1' 'proxy_tools==0.1.0'
if ($LASTEXITCODE) { throw 'Pure Python dependency wheel build failed' }
& $Python -m pip install --disable-pip-version-check --no-compile --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313 --find-links $wheels --target $sitePackages -r "$projectRoot\requirements-runtime.txt"
if ($LASTEXITCODE) { throw 'Runtime dependency install failed' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $compiler)) { throw 'Windows .NET Framework C# compiler is required' }
$launcher = Join-Path $projectRoot ('build\launcher-' + $buildId + '.exe')
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$launcher" "/win32icon:$projectRoot\assets\shoko.ico" "/win32manifest:$projectRoot\assets\launcher.manifest" /reference:System.Windows.Forms.dll "$PSScriptRoot\Launcher.cs"
if ($LASTEXITCODE) { throw 'Desktop launcher build failed' }
$candidate = Join-Path $projectRoot ('build\portable-candidate-' + $buildId)
& $Python "$PSScriptRoot\package_windows.py" assemble --output $candidate --python-zip $pythonZip --site-packages $sitePackages --launcher $launcher
if ($LASTEXITCODE) { throw 'Folder distribution assembly failed' }
foreach ($runtimeFile in @('python.exe', 'pythonw.exe', 'python313.dll')) {
    $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $candidate ('runtime\' + $runtimeFile))
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw ('Official runtime signature validation failed: ' + $runtimeFile)
    }
}
$candidatePython = Join-Path $candidate 'runtime\python.exe'
& $candidatePython -B -m unittest discover -s "$projectRoot\tests" -v
if ($LASTEXITCODE) { throw 'Tests on packaged Python failed' }
$smokeRoot = Join-Path $cache ('Smoke ' + $buildId + ' проверка')
$smokeArguments = '--smoke --data-dir "' + $smokeRoot + '"'
$smoke = Start-Process -FilePath (Join-Path $candidate 'ShokoAnalytics.exe') -ArgumentList $smokeArguments -WorkingDirectory $candidate -WindowStyle Hidden -PassThru
if (!$smoke.WaitForExit(60000)) {
    & taskkill.exe /PID $smoke.Id /T /F | Out-Null
    throw 'Desktop launcher smoke timed out'
}
if ($smoke.ExitCode -or !(Test-Path -LiteralPath (Join-Path $smokeRoot 'smoke-ok.txt'))) { throw 'Desktop launcher smoke failed' }
Set-Content -LiteralPath (Join-Path $cache 'candidate.path') -Value $candidate -Encoding utf8
if (!$StageOnly) {
    $livePath = Join-Path $projectRoot 'dist\ShokoAnalytics\'
    if (Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($livePath, [StringComparison]::OrdinalIgnoreCase) }) {
        throw 'Close the current Shoko Analytics window before replacing the application'
    }
    & $Python "$PSScriptRoot\package_windows.py" publish --candidate $candidate
    if ($LASTEXITCODE) { throw 'Distribution publication failed' }
}
