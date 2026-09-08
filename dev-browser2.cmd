@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev\astra_browsers.ps1" -Port 9233
exit /b %errorlevel%
