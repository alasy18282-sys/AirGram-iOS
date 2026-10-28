@echo off
chcp 65001 >nul
echo Reset cached DC config (keeps accounts, clears stale server addresses)
echo Close Telegram before continuing!
pause

set TDATA=E:\TBuild\mytelegram\out\Debug\tdata
if not exist "%TDATA%" (
    echo tdata not found
    pause
    exit /b 1
)

:: Cached MTP/DC config blobs — stale IPs survive exe patches here
for %%F in (
    "%TDATA%\settingss"
    "%TDATA%\settings"
    "%TDATA%\usertag"
) do if exist %%F del /f /q %%F

echo Done. Now run tools\patch_telegram_exe.bat and start Telegram.
