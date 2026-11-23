@echo off
setlocal EnableDelayedExpansion
rem The scripts are pure ASCII, so UTF-8 is only needed for the messages uv prints.
rem If the console cannot switch (very old builds), carry on instead of dying here.
chcp 65001 >nul 2>&1
title MiroFish first-run setup
cd /d "%~dp0"

set "ROOT=%~dp0"
set "DRIVE=%ROOT:~0,1%:"
set "VENV=%ROOT%runtime\venv"
set "PY=%VENV%\Scripts\python.exe"
set "REQ=%ROOT%app\backend\requirements-desktop.txt"
set "UV=%ROOT%tools\uv.exe"
set "CACHE=%ROOT%runtime\.cache"
set "TMPDIR=%CACHE%\tmp"

rem ============================================================================
rem  Every cache and temp file stays on the install drive (e.g. D:), never on C:
rem  -- that was the root cause of the earlier "not enough disk space": uv caches by default in
rem     C:\Users\<you>\AppData\Local\uv\cache
rem ============================================================================
set "UV_CACHE_DIR=%CACHE%\uv"
set "UV_LINK_MODE=copy"
set "UV_PYTHON_DOWNLOADS=never"
set "PIP_CACHE_DIR=%CACHE%\pip"
set "TMP=%TMPDIR%"
set "TEMP=%TMPDIR%"

echo ================================================================
echo  MiroFish setup
echo ================================================================
echo   Install directory: %ROOT%
echo   Cache directory: %CACHE%   ^(all on this drive, nothing on C:^)
echo.

rem ---------------- Mode selection ----------------
rem   --slim : slim install (no torch, about 400 MB; twitter personalized recommendations degrade to random/popularity)
rem   default: full install (with torch, about 1.1 GB; every recommendation algorithm available)
set "SLIM=0"
for %%A in (%*) do (
    if /i "%%~A"=="--slim" set "SLIM=1"
    if /i "%%~A"=="--full" set "SLIM=0"
)
set "REQSLIM=%ROOT%app\backend\requirements-desktop-slim.txt"
if "%SLIM%"=="1" (
    set "REQ=%REQSLIM%"
    echo   [!] Slim mode: torch is not installed (saves about 700 MB of disk and download time)
    echo       Personalized recommendations on the twitter platform fall back to random/popularity.
    echo.
)

if /i "%~1"=="--clean" goto :cleanonly

rem ---------------- 0. Disk space check ----------------
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "[math]::Floor((Get-PSDrive -Name '%DRIVE:~0,1%').Free/1MB)" 2^>nul`) do set "FREEMB=%%A"
if not defined FREEMB set "FREEMB=0"
set /a FREEGB=%FREEMB%/1024
echo [0/5] %DRIVE% free space: %FREEGB% GB
set "NEEDMB=3000"
if "%SLIM%"=="1" set "NEEDMB=1500"
if %FREEMB% LSS %NEEDMB% (
    echo.
    echo [X] Not enough space: this drive needs at least %NEEDMB% MB free.
    echo     The install peaks at about 2.5 GB (including the download cache); the cache is cleaned up afterwards,
    echo     so it ends up using about 1.1 GB (full) or about 0.4 GB (slim).
    echo.
    echo     Suggestion: move the whole folder to a drive with more space and retry,
    echo     or free some space on this drive first. Run "%~nx0 --clean" to remove leftovers from a previous attempt.
    pause
    exit /b 1
)
if %FREEMB% LSS 5000 (
    echo     [!] Below the recommended 5 GB; do not close this window while the install runs.
)

if not exist "%CACHE%" mkdir "%CACHE%"
if not exist "%TMPDIR%" mkdir "%TMPDIR%"

rem ---------------- Already installed? ----------------
if exist "%PY%" (
    "%PY%" -c "import flask, webview, waitress, openai, pydantic, fitz, camel, oasis, mcp" >nul 2>&1
    if not errorlevel 1 (
        echo [x] The runtime is intact, skipping the install.
        goto :shortcut
    )
    echo [x] An incomplete runtime was found, filling in the missing dependencies...
    goto :install
)

rem ---------------- 1. Find a base Python ----------------
set "BASEPY="
for %%V in (3.12 3.13 3.11 3.10) do (
    if not defined BASEPY (
        py -%%V-64 -c "import sys" >nul 2>&1 && set "BASEPY=py -%%V-64"
    )
)
if not defined BASEPY (
    python -c "import sys;assert (3,10)<=sys.version_info[:2]<(3,14)" >nul 2>&1 && set "BASEPY=python"
)

if not defined BASEPY (
    echo [!] No Python 3.10 - 3.13 found; trying to install Python 3.12 with winget ...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo [X] winget is not available. Please install Python 3.12 by hand:
        echo     https://www.python.org/downloads/windows/
        echo     Tick "Add python.exe to PATH" during the install.
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
    set "BASEPY=py -3.12-64"
    !BASEPY! -c "import sys" >nul 2>&1 || (
        echo [X] Python is still unavailable after installing; restart the computer and run this script again.
        pause
        exit /b 1
    )
)
echo [1/5] Base Python: %BASEPY%

rem ---------------- 2. Create the standalone runtime ----------------
echo [2/5] Creating the standalone environment ^(about 25 MB^)...
if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
!BASEPY! -m venv "%VENV%"
if errorlevel 1 (
    echo [X] Failed to create the virtual environment. If it mentions disk space, free some space on this drive first.
    pause
    exit /b 1
)

rem ---------------- 3. Install the dependencies ----------------
:install
echo [3/5] Downloading and installing the dependencies ^(about 1.1 GB, the slowest step^)...
echo       Cache location: %UV_CACHE_DIR%
echo.
if exist "%UV%" (
    "%UV%" pip install --python "%PY%" -r "%REQ%"
    if errorlevel 1 (
        echo.
        echo [!] The first install did not finish, retrying (uv reuses what it already downloaded)...
        "%UV%" pip install --python "%PY%" -r "%REQ%"
    )
    if errorlevel 1 (
        echo [!] Retrying with the Tsinghua mirror...
        "%UV%" pip install --python "%PY%" -r "%REQ%" --default-index https://pypi.tuna.tsinghua.edu.cn/simple
    )
) else (
    "%PY%" -m pip install --upgrade pip --disable-pip-version-check -q
    "%PY%" -m pip install --disable-pip-version-check -r "%REQ%"
    if errorlevel 1 (
        "%PY%" -m pip install --disable-pip-version-check -r "%REQ%" ^
            -i https://pypi.tuna.tsinghua.edu.cn/simple ^
            --trusted-host pypi.tuna.tsinghua.edu.cn
    )
)

if "%SLIM%"=="1" (
    echo       Adding OASIS itself (slim mode only)...
    if exist "%UV%" (
        "%UV%" pip install --python "%PY%" --no-deps camel-oasis==0.2.5
    )
    "%PY%" "%ROOT%tools\patch_oasis_recsys.py" --slim "%VENV%\Lib\site-packages"
)

rem ---------------- 4. Verify ----------------
echo.
echo [4/5] Verifying the dependencies...
"%PY%" -c "import flask, webview, waitress, openai, pydantic, fitz, camel, oasis, mcp; print('OK: all dependencies are ready')"
if errorlevel 1 (
    echo.
    echo [X] Dependency verification failed.
    echo     1^) If it says "not enough disk space", free some space on this drive and run this script again;
    echo        whatever was downloaded stays in %UV_CACHE_DIR%, so nothing is downloaded twice.
    echo     2^) If the network dropped, simply run it again to resume.
    echo     3^) You can run "%~nx0 --clean" to wipe the cache and retry.
    pause
    exit /b 1
)

rem ---------------- 5. Clean the cache ----------------
if /i "%~1"=="--keep-cache" (
    echo [5/5] Keeping the download cache: %CACHE%
) else (
    echo [5/5] Cleaning the download cache to free space...
    rd /s /q "%CACHE%" 2>nul
    if exist "%CACHE%" echo       [!] Some cache entries could not be deleted; remove %CACHE% by hand
)

rem ---------------- Desktop shortcut ----------------
:shortcut
echo.
echo Creating the desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\MiroFish.lnk');" ^
  "$s.TargetPath='%ROOT%MiroFish.exe'; $s.WorkingDirectory='%ROOT%';" ^
  "$s.IconLocation='%ROOT%MiroFish.exe,0'; $s.Save()" >nul 2>&1

echo.
echo ================================================================
echo   Setup complete! Double-click MiroFish.exe to start.
echo   On first start, fill in %APPDATA%\MiroFish\.env -
echo   LLM_API_KEY and ZEP_API_KEY (the app opens the settings page automatically).
echo ================================================================
echo.
if /i not "%~1"=="--quiet" (
    if /i not "%~1"=="--no-pause" pause
)
exit /b 0

:cleanonly
echo Cleaning the cached downloads...
if exist "%CACHE%" (
    echo Directory: %CACHE%
    rd /s /q "%CACHE%" 2>nul
)
if exist "%CACHE%" (
    echo [!] Could not delete everything; close any program using those files and retry.
) else (
    echo Done, the space is freed.
)
echo.
pause
exit /b 0
