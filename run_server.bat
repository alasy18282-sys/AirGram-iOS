@echo off
chcp 65001 >nul
echo =========================================
echo   MyTelegram Python Server
echo =========================================
cd /d "%~dp0"

set PYEXE=E:\TBuild\ThirdParty\python\Scripts\python.exe
set PIPEXE=E:\TBuild\ThirdParty\python\Scripts\pip.exe

:: Verify ThirdParty Python actually works (not just a broken launcher stub)
if exist "%PYEXE%" (
    "%PYEXE%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo ThirdParty Python broken, trying system python...
        set PYEXE=python
        set PIPEXE=pip
    )
) else (
    echo ThirdParty Python not found, trying system python...
    set PYEXE=python
    set PIPEXE=pip
)

"%PYEXE%" --version
if errorlevel 1 (
    echo [ERROR] No Python found. Install Python 3.10+
    pause
    exit /b 1
)

:: Install pycryptodome if missing
"%PYEXE%" -c "from Crypto.Cipher import AES" >nul 2>&1
if errorlevel 1 (
    echo Installing pycryptodome...
    "%PIPEXE%" install pycryptodome cryptography sympy
    if errorlevel 1 (
        echo [ERROR] pip install failed
        pause
        exit /b 1
    )
)

echo.
echo Starting server on port 20443...
echo.
"%PYEXE%" server.py
pause
