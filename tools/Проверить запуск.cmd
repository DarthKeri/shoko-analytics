@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Shoko Analytics - диагностика запуска
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "REPORT=%TEMP%\Shoko-Startup-%RANDOM%-%RANDOM%.txt"
echo Проверяем запуск Shoko Analytics. Это займёт до двух минут.
echo Тестовое окно приложения может открыться и закрыться автоматически.
echo Настройки и таблицы не изменяются, отчёт никуда не отправляется.
echo.
if not exist "%~dp0runtime\python.exe" goto missing_runtime
if not exist "%~dp0app\startup_diagnostics.py" goto missing_helper
"%~dp0runtime\python.exe" -I -B "%~dp0app\startup_diagnostics.py" > "%REPORT%" 2>&1
set "SHOKO_RESULT=%ERRORLEVEL%"
echo.>> "%REPORT%"
echo Код завершения проверки: %SHOKO_RESULT% >> "%REPORT%"
goto show_report
:missing_runtime
> "%REPORT%" echo ОШИБКА: рядом с диагностикой не найден runtime\python.exe.
>> "%REPORT%" echo Распакуйте весь архив приложения. Сохраните папки app и runtime рядом с ShokoAnalytics.exe.
goto show_report
:missing_helper
> "%REPORT%" echo ОШИБКА: не найден app/startup_diagnostics.py. Распакуйте весь архив приложения.
:show_report
echo Отчёт сохранён: %REPORT%
echo Откроется Блокнот. Сохраните или отправьте этот текстовый файл.
start "" notepad.exe "%REPORT%"
echo.
pause
