@echo off
rem Environment check: drivers, device, Developer Mode.
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m iosloc doctor
pause
