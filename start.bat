@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Prefer py launcher, fallback to python
set PY=py -3
%PY% --version >nul 2>&1
if %errorlevel% neq 0 (
    set PY=python
)

echo Checking Python installation...
%PY% --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Python3 not found. Install it from https://python.org
    pause
    exit /b 1
)

echo Checking dependencies...
echo.

REM Auto-install missing requirements
%PY% -c "import PySide6; import tqdm" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Installing requirements (PySide6, tqdm)...
    %PY% -m pip install -r requirements.txt -q
    if %ERRORLEVEL% neq 0 (
        echo.
        echo Failed to install requirements. Please run:
        echo   %PY% -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo Requirements installed successfully.
    echo.
)

echo.
echo  [1] GUI
echo  [2] CLI
echo.
set /p choice="Choice [1]: "
if "!choice!"=="2" (
    %PY% main.py
) else (
    %PY% gui.py
)
pause
