@echo off
set "APP=%~dp0dist\ShokoAnalytics\ShokoAnalytics.exe"
if not exist "%APP%" (
  echo Build the application first: powershell -File tools\build.ps1
  pause
  exit /b 1
)
start "" "%APP%"
