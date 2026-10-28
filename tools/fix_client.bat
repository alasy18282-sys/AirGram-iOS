@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo.
echo [1/3] Patch Telegram.exe (RSA + IP, no rebuild)
python tools\patch_telegram_exe.py
if errorlevel 1 goto fail
echo.
echo [2/3] Clear stale DC/proxy cache
call tools\clear_client_cache.bat /nopause
echo.
echo [3/3] Done.
echo.
echo 1. Start run_server.bat  (restart if already running!)
echo 2. Start Telegram.exe
echo.
echo IMPORTANT: Settings -^> Advanced -^> Connection type -^> DISABLE proxy
echo   Direct connection to 192.168.100.14:20443 is required.
echo   If you keep MTProto proxy, set secret in config.py / MYTELEGRAM_MTPROXY_SECRET
echo.
echo Logs: mytelegram-python\logs\server.log
echo       mytelegram\out\Debug\log.txt
goto end
:fail
echo Patch failed.
:end
pause
