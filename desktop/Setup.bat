@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title MiroFish 首次运行初始化
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
rem  所有缓存/临时文件都放在安装目录(例如 D:)，不使用 C 盘
rem  —— 这是上一次“磁盘空间不足”的根因：uv 默认把缓存写进
rem     C:\Users\<你>\AppData\Local\uv\cache
rem ============================================================================
set "UV_CACHE_DIR=%CACHE%\uv"
set "UV_LINK_MODE=copy"
set "UV_PYTHON_DOWNLOADS=never"
set "PIP_CACHE_DIR=%CACHE%\pip"
set "TMP=%TMPDIR%"
set "TEMP=%TMPDIR%"

echo ================================================================
echo   MiroFish 初始化
echo ================================================================
echo   安装目录: %ROOT%
echo   缓存目录: %CACHE%   ^(全部在本盘，不占用 C 盘^)
echo.

if /i "%~1"=="--clean" goto :cleanonly

rem ---------------- 0. 磁盘空间检查 ----------------
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "[math]::Floor((Get-PSDrive -Name '%DRIVE:~0,1%').Free/1MB)" 2^>nul`) do set "FREEMB=%%A"
if not defined FREEMB set "FREEMB=0"
set /a FREEGB=%FREEMB%/1024
echo [0/5] %DRIVE% 剩余空间: %FREEGB% GB
if %FREEMB% LSS 3000 (
    echo.
    echo [X] 空间不足：本盘至少需要 3 GB 可用空间。
    echo     安装依赖峰值约 2.5 GB(含下载缓存)，安装完成后自动清理缓存，
    echo     实际占用约 1.1 GB。
    echo.
    echo     建议：把整个文件夹移动到空间更大的磁盘后重试，
    echo     或先清理本盘空间。可用 "%~nx0 --clean" 清理上次残留缓存。
    pause
    exit /b 1
)
if %FREEMB% LSS 5000 (
    echo     [!] 低于推荐的 5 GB，安装过程中请勿关闭窗口。
)

if not exist "%CACHE%" mkdir "%CACHE%"
if not exist "%TMPDIR%" mkdir "%TMPDIR%"

rem ---------------- 已装好？ ----------------
if exist "%PY%" (
    "%PY%" -c "import flask, webview, waitress, openai, pydantic, fitz, camel, oasis, mcp" >nul 2>&1
    if not errorlevel 1 (
        echo [x] 运行环境完好，跳过安装。
        goto :shortcut
    )
    echo [x] 检测到不完整的运行环境，继续补齐依赖...
    goto :install
)

rem ---------------- 1. 寻找基础 Python ----------------
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
    echo [!] 未找到 Python 3.10 - 3.13，尝试用 winget 安装 Python 3.12 ...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo [X] 系统没有 winget。请先手动安装 Python 3.12：
        echo     https://www.python.org/downloads/windows/
        echo     安装时请勾选 "Add python.exe to PATH"。
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
    set "BASEPY=py -3.12-64"
    !BASEPY! -c "import sys" >nul 2>&1 || (
        echo [X] Python 安装后仍不可用，请重启电脑后再次运行本脚本。
        pause
        exit /b 1
    )
)
echo [1/5] 基础 Python：%BASEPY%

rem ---------------- 2. 创建独立运行时 ----------------
echo [2/5] 创建独立运行环境 ^(约 25 MB^)...
if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
!BASEPY! -m venv "%VENV%"
if errorlevel 1 (
    echo [X] 创建虚拟环境失败。若提示空间不足，请先释放本盘空间。
    pause
    exit /b 1
)

rem ---------------- 3. 安装依赖 ----------------
:install
echo [3/5] 下载并安装依赖 ^(约 1.1 GB，最慢的一步^)...
echo       缓存位置: %UV_CACHE_DIR%
echo.
if exist "%UV%" (
    "%UV%" pip install --python "%PY%" -r "%REQ%"
    if errorlevel 1 (
        echo.
        echo [!] 首次安装未完成，正在重试(uv 会复用已下载的部分)...
        "%UV%" pip install --python "%PY%" -r "%REQ%"
    )
    if errorlevel 1 (
        echo [!] 改用清华镜像重试...
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

rem ---------------- 4. 校验 ----------------
echo.
echo [4/5] 校验依赖...
"%PY%" -c "import flask, webview, waitress, openai, pydantic, fitz, camel, oasis, mcp; print('OK: 依赖全部就绪')"
if errorlevel 1 (
    echo.
    echo [X] 依赖校验失败。
    echo     1^) 若是“磁盘空间不足”，请清理本盘空间后重新运行本脚本；
    echo        已下载的内容会保留在 %UV_CACHE_DIR%，不会重复下载。
    echo     2^) 若是网络中断，直接重新运行即可续传。
    echo     3^) 可运行 "%~nx0 --clean" 清理缓存后再试。
    pause
    exit /b 1
)

rem ---------------- 5. 清理缓存 ----------------
if /i "%~1"=="--keep-cache" (
    echo [5/5] 保留下载缓存：%CACHE%
) else (
    echo [5/5] 清理下载缓存以释放空间...
    rd /s /q "%CACHE%" 2>nul
    if exist "%CACHE%" echo       [!] 部分缓存未能删除，可手动删除 %CACHE%
)

rem ---------------- 桌面快捷方式 ----------------
:shortcut
echo.
echo 创建桌面快捷方式...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\MiroFish.lnk');" ^
  "$s.TargetPath='%ROOT%MiroFish.exe'; $s.WorkingDirectory='%ROOT%';" ^
  "$s.IconLocation='%ROOT%MiroFish.exe,0'; $s.Save()" >nul 2>&1

echo.
echo ================================================================
echo   初始化完成！双击 MiroFish.exe 即可启动。
echo   首次启动请填写 %APPDATA%\MiroFish\.env 中的
echo   LLM_API_KEY 与 ZEP_API_KEY(程序里会自动打开配置页)。
echo ================================================================
echo.
if /i not "%~1"=="--quiet" (
    if /i not "%~1"=="--no-pause" pause
)
exit /b 0

:cleanonly
echo 清理缓存的下载文件...
if exist "%CACHE%" (
    echo 目录: %CACHE%
    rd /s /q "%CACHE%" 2>nul
)
if exist "%CACHE%" (
    echo [!] 未能完全删除，请关闭占用文件的程序后重试。
) else (
    echo 完成，已释放缓存空间。
)
echo.
pause
exit /b 0
