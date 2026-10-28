@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
python tools\setup_proxy_secret.py
echo.
echo Restart run_server.bat, then in Telegram set MTProto proxy password to the hex above.
pause
