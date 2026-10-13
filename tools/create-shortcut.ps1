param([switch]$Desktop)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $projectRoot 'dist\ShokoAnalytics\ShokoAnalytics.exe'
if (!(Test-Path -LiteralPath $exe)) { throw 'Build ShokoAnalytics first' }
$shortcutDirectory = if ($Desktop) { [Environment]::GetFolderPath('Desktop') } else { $projectRoot }
$shortcutPath = Join-Path $shortcutDirectory 'Shoko Analytics.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Shoko Analytics'
$shortcut.Save()
Write-Host $shortcutPath
