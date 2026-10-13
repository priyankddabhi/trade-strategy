@echo off
rem Opens the bot website on this PC. Close this window to stop it.
cd /d "%~dp0.."
".venv\Scripts\python.exe" run.py web
if errorlevel 1 pause
