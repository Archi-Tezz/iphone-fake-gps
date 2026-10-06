@echo off
rem Start the ios-loc control panel.
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m iosloc ui %*
if errorlevel 1 pause
