@echo off
chcp 65001 >nul
if /I "%~1"=="/nopause" goto run
echo ============================================
echo  Сброс кэша DC/прокси Telegram (без пересборки)
echo ============================================
echo.
echo Закрой Telegram полностью, затем нажми любую клавишу...
pause >nul
:run

set T=E:\TBuild\mytelegram\out\Debug\tdata
if exist "%T%\settingss" del /f /q "%T%\settingss"
if exist "%T%\AB7BF51A34DD7CF2s" del /f /q "%T%\AB7BF51A34DD7CF2s"

echo.
echo Кэш сброшен.
if /I "%~1"=="/nopause" goto end
echo.
echo Дальше:
echo   1. mytelegram-python\tools\fix_client.bat
echo   2. mytelegram-python\run_server.bat
echo   3. Telegram -^> Настройки -^> Дополнительно -^> Тип подключения -^> БЕЗ прокси
echo.
pause
:end
