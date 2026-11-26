@echo off
rem Collagen Reflectance Analyzer - Windows launcher.
rem Double-click to start. The first start installs the Python packages (2-5 minutes, internet needed).
rem The packages go to %LOCALAPPDATA%\CollagenAnalyzer (not into this folder).
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "APPDIR=%~dp0"
set "SUPPORT=%LOCALAPPDATA%\CollagenAnalyzer"
set "VENV=%SUPPORT%\venv"
if not exist "%SUPPORT%" mkdir "%SUPPORT%"

rem --- find Python 3.9 or newer (the "py" launcher from python.org first)
set "PY="
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=%%~C"
  )
)
if not defined PY (
  echo Python 3.9 or newer is needed. Opening python.org ...
  echo Install it with the option "Add python.exe to PATH" ticked, then start this file again.
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)

rem --- create the environment once, reinstall when requirements.txt changes
for /f "skip=1 tokens=* delims=" %%H in ('certutil -hashfile "%APPDIR%requirements.txt" SHA1') do (
  if not defined REQHASH set "REQHASH=%%H"
)
set "OLDHASH="
if exist "%SUPPORT%\req_hash" set /p OLDHASH=<"%SUPPORT%\req_hash"
if not exist "%VENV%\Scripts\python.exe" goto setup
if not "!OLDHASH!"=="!REQHASH!" goto setup
goto run

:setup
echo First start: setting up. This takes a few minutes...
if exist "%VENV%" rmdir /s /q "%VENV%"
%PY% -m venv "%VENV%"
if errorlevel 1 (
  echo Could not create the Python environment in %VENV%.
  pause
  exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
"%VENV%\Scripts\python.exe" -m pip install -r "%APPDIR%requirements.txt"
if errorlevel 1 (
  echo.
  echo Installing the Python packages failed - see the messages above. Check the internet connection.
  pause
  exit /b 1
)
> "%SUPPORT%\req_hash" echo !REQHASH!
echo Setup finished.

:run
echo Starting Collagen Reflectance Analyzer...
start "" "%VENV%\Scripts\pythonw.exe" "%APPDIR%CollagenAnalyzer.py" %*
exit /b 0
