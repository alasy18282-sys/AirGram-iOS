@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo Patching Telegram.exe (no rebuild)...
python tools\patch_telegram_exe.py
if errorlevel 1 pause
exit /b %errorlevel%
