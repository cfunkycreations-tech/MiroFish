"""
MiroFish desktop (Windows x64) packaging script

Produces two kinds of build:

  --mode offline   the fully offline build: bundles the Python runtime and every dependency, ready to run after extraction (about 1 GB unpacked)
  --mode setup     the online installer: launcher + sources + Setup.bat only; downloads the dependencies on first run (about 1-2 MB)

Build layout (both modes share the same directory conventions):

    MiroFish-0.1.0-windows-x64/
      MiroFish.exe            double-click to start (no console)
      MiroFish-Console.bat    troubleshooting: console window + logs
      Setup.bat               required by the online build: initializes the Python runtime
      README.txt / LICENSE / .env.example
      app/
        app.py                 the desktop launcher (Flask + pywebview)
        icon.py
        backend/               the backend sources (with requirements-desktop.txt)
        frontend_dist/         the built frontend
      runtime/python/         [offline only] bundled Python and dependencies
      runtime/venv/           [created on the first run of the online build]

Usage:
    # 1) fetch the assets (Windows dependencies, embeddable Python, MiroFish.exe)
    python desktop/fetch_sources.py --build-dir build

    # 2) package it (build the frontend first)
    cd frontend && npm ci && npm run build && cd ..
    python desktop/build.py --mode offline --out dist
    python desktop/build.py --mode setup   --out dist
"""

import argparse
import os
import shutil
import subprocess
import sys
import zipfile

APP_NAME = 'MiroFish'
def _read_version() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'version.txt')
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip() or '0.1.0'
    except OSError:
        return '0.1.0'


VERSION = _read_version()
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP_DIR = os.path.join(REPO_ROOT, 'desktop')

BACKEND_EXCLUDE_DIRS = {'uploads', 'logs', '__pycache__', '.pytest_cache'}
BACKEND_EXCLUDE_FILES = {'.env'}


def log(msg):
    print(f'[build] {msg}', flush=True)


def rmtree(path):
    shutil.rmtree(path, ignore_errors=True)


def copy_tree(src, dst, ignore=None):
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)


def assemble_app(stage):
    app_dir = os.path.join(stage, 'app')
    os.makedirs(app_dir, exist_ok=True)
    for name in ('app.py', 'icon.py', 'version.txt'):
        src = os.path.join(DESKTOP_DIR, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(app_dir, name))
    # The app icon (window icon / taskbar icon)
    for name in ('icon.png', 'icon.ico'):
        src = os.path.join(DESKTOP_DIR, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(app_dir, name))


def assemble_backend(stage):
    dst = os.path.join(stage, 'app', 'backend')
    log(f'Copying the backend sources -> {dst}')

    def _ignore(directory, names):
        out = []
        for name in names:
            if name in BACKEND_EXCLUDE_DIRS or name in BACKEND_EXCLUDE_FILES:
                out.append(name)
            elif name.endswith('.pyc'):
                out.append(name)
        return out

    copy_tree(os.path.join(REPO_ROOT, 'backend'), dst, ignore=_ignore)


def assemble_frontend(stage):
    src = os.path.join(REPO_ROOT, 'frontend', 'dist')
    if not os.path.isdir(src):
        raise SystemExit('frontend/dist does not exist; run cd frontend && npm ci && npm run build first')
    dst = os.path.join(stage, 'app', 'frontend_dist')
    log(f'Copying the built frontend -> {dst}')
    copy_tree(src, dst)


def write_requirements(stage):
    for name in ('requirements-desktop.txt', 'requirements-desktop-slim.txt'):
        src = os.path.join(DESKTOP_DIR, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(stage, 'app', 'backend', name))

    # The slim-mode patch script (Setup.bat calls it once the install finishes)
    tools_src = os.path.join(REPO_ROOT, 'tools')
    dst_tools = os.path.join(stage, 'tools')
    os.makedirs(dst_tools, exist_ok=True)
    patch = os.path.join(tools_src, 'patch_oasis_recsys.py')
    if os.path.exists(patch):
        shutil.copy2(patch, os.path.join(dst_tools, 'patch_oasis_recsys.py'))


def populate_runtime(stage, wheels_dir, embed_dir):
    py_dir = os.path.join(stage, 'runtime', 'python')
    # A parent directory may be passed in (with a cp3xx/ level inside)
    if not os.path.exists(os.path.join(embed_dir, 'python.exe')):
        nested = [d for d in os.listdir(embed_dir)
                  if os.path.isfile(os.path.join(embed_dir, d, 'python.exe'))]
        if nested:
            embed_dir = os.path.join(embed_dir, nested[0])
        else:
            raise SystemExit(f'no python.exe inside the --embed directory: {embed_dir}')

    log(f'Copying the Python runtime -> {py_dir}')
    copy_tree(embed_dir, py_dir)

    for name in os.listdir(py_dir):
        if name.endswith('._pth'):
            os.remove(os.path.join(py_dir, name))
    pth_name = next((n for n in os.listdir(py_dir) if n.startswith('python3') and n.endswith('.zip')), 'python312.zip')
    with open(os.path.join(py_dir, pth_name.replace('.zip', '._pth')), 'w', encoding='utf-8') as f:
        f.write(f'{pth_name}\n.\nLib\\site-packages\n\nimport site\n')

    site_packages = os.path.join(py_dir, 'Lib', 'site-packages')
    os.makedirs(site_packages, exist_ok=True)
    log(f'Copying the Windows dependencies -> {site_packages}')
    copy_tree(wheels_dir, site_packages,
              ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyi'))


def ensure_launcher(exe_path):
    """Restore the launcher from the repository cache when it is missing (and cannot be compiled here)."""
    if os.path.exists(exe_path):
        return exe_path
    cached = os.path.join(DESKTOP_DIR, 'MiroFish.exe')
    if os.path.exists(cached):
        os.makedirs(os.path.dirname(exe_path), exist_ok=True)
        shutil.copy2(cached, exe_path)
        log(f'Using the prebuilt launcher from the repository -> {exe_path}')
        return exe_path
    raise SystemExit(
        f'The launcher {exe_path} is missing.\n'
        '  Run this on a machine with a compiler (or on Windows): python desktop/fetch_sources.py\n'
        '  or use the prebuilt desktop/MiroFish.exe from the repository')


def copy_tools(stage, tools_dir):
    """Copy the tools (uv.exe and friends) the online build uses to install the dependencies."""
    if not tools_dir or not os.path.isdir(tools_dir):
        return
    dst = os.path.join(stage, 'tools')
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(tools_dir):
        src = os.path.join(tools_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dst, name))
    log(f'Tools -> {dst}')


def write_console_bat(stage):
    """Console launcher script: make sure the environment is initialized, then start with a log window (for troubleshooting)."""
    path = os.path.join(stage, f'{APP_NAME}-Console.bat')
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write('@echo off\r\n'
                'chcp 65001 >nul\r\n'
                'title MiroFish Console (troubleshooting)\r\n'
                'cd /d "%~dp0"\r\n'
                'echo ============================================================\r\n'
                'echo  MiroFish console mode: the app window opens and this window shows the logs\r\n'
                'echo ============================================================\r\n'
                'echo.\r\n'
                'if not exist "runtime\\venv\\Scripts\\python.exe" (\r\n'
                '    if not exist "runtime\\python\\python.exe" (\r\n'
                '        echo [Setup] No runtime installed yet, running Setup.bat first ...\r\n'
                '        echo.\r\n'
                '        call "%~dp0Setup.bat" --no-pause\r\n'
                '        if errorlevel 1 (\r\n'
                '            echo.\r\n'
                '            echo [X] Setup failed; please screenshot the error above and report it.\r\n'
                '            pause\r\n'
                '            exit /b 1\r\n'
                '        )\r\n'
                '    )\r\n'
                ')\r\n'
                'echo Starting MiroFish (close the app window to exit)...\r\n'
                'echo.\r\n'
                f'"{APP_NAME}.exe" --console\r\n'
                'echo.\r\n'
                'echo [Exit] The app has finished.\r\n'
                'pause\r\n')


def write_readme(stage, mode):
    offline = mode == 'offline'
    extra = ('This package bundles the Python runtime and every dependency; no internet and no Python install needed.\n'
             if offline else
             'This is the online installer: the first launch of MiroFish.exe offers to download the runtime\n'
             '(about 500 MB - 1 GB), after which it works offline. You can also run Setup.bat by hand.\n')
    text = f"""MiroFish {VERSION} - Windows x64 desktop build
=================================================

1. Starting
{extra}
    Double-click MiroFish.exe. Data and configuration live in:
        %APPDATA%\\MiroFish\\
            .env              the configuration file (LLM / Zep API keys)
            uploads\\           uploaded documents, graphs, simulations and reports
            logs\\desktop.log  the run log
    Upgrading the app never overwrites your configuration or data.

2. API keys are required on first use
    1. Open %APPDATA%\\MiroFish\\.env in Notepad
    2. Fill in:
           LLM_API_KEY      the LLM API key (any OpenAI-compatible endpoint)
           LLM_BASE_URL     the endpoint address, e.g. https://dashscope.aliyuncs.com/compatible-mode/v1
           LLM_MODEL_NAME   the model name, e.g. qwen-plus
           ZEP_API_KEY      the Zep Cloud memory graph key (free tier at https://app.getzep.com/)
    3. Save the file and restart MiroFish.exe

3. Not enough disk space?
    The install needs about 3 GB at peak (about 1.1 GB once finished). If space is tight:
      * double-click Setup-Slim.bat (or run Setup.bat --slim): no torch is installed,
        which needs about 1.5 GB at peak and about 0.4 GB once finished;
        on the twitter platform "personalized recommendations" degrade to random/popularity ranking, everything else is unchanged.
      * every cache stays in runtime\\.cache inside the install directory, so the C: drive is untouched.

4. When something goes wrong
    * Double-click MiroFish-Console.bat: it keeps a console window open so the error is visible.
    * Logs: %APPDATA%\\MiroFish\\logs\\desktop.log
    * The online download was interrupted: double-click MiroFish.exe or Setup.bat again.
    * A blank window: Edge WebView2 is required (usually included with Win10/11);
      install it from https://developer.microsoft.com/microsoft-edge/webview2/;
      as a stopgap run  MiroFish.exe --browser  to use your default browser.

5. Networking and security
    The backend only listens on a random free 127.0.0.1 port and is never exposed.
    While a simulation runs, it calls the LLM / Zep cloud services you configured in .env.

6. License
    AGPL-3.0, see LICENSE.
"""
    with open(os.path.join(stage, 'README.txt'), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write(text)
    shutil.copy2(os.path.join(DESKTOP_DIR, 'env.example'), os.path.join(stage, '.env.example'))
    license_src = os.path.join(REPO_ROOT, 'LICENSE')
    if os.path.exists(license_src):
        shutil.copy2(license_src, os.path.join(stage, 'LICENSE'))


def make_zip(stage, out_dir, name):
    """Zip the installer.

    Prefers the system zip (multi-threaded and faster), falling back to Python zipfile.
    """
    zip_path = os.path.join(out_dir, name + '.zip')
    if os.path.exists(zip_path):
        os.remove(zip_path)
    log(f'Compressing -> {zip_path}')

    if shutil.which('zip'):
        parent = os.path.dirname(os.path.abspath(stage))
        try:
            subprocess.check_call(['zip', '-r', '-q', '-6', os.path.abspath(zip_path), name], cwd=parent)
        except subprocess.CalledProcessError as exc:
            log(f'The system zip failed ({exc}), switching to Python zipfile')
            if os.path.exists(zip_path):
                os.remove(zip_path)
        else:
            count = sum(len(files) for _, _, files in os.walk(stage))
            size = os.path.getsize(zip_path) / 1024 / 1024
            log(f'Done: {zip_path} ({count} files, {size:.0f} MB)')
            return zip_path

    count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, _, files in os.walk(stage):
            for fn in files:
                full = os.path.join(root, fn)
                zf.write(full, os.path.join(name, os.path.relpath(full, stage)))
                count += 1

    size = os.path.getsize(zip_path) / 1024 / 1024
    log(f'Done: {zip_path} ({count} files, {size:.0f} MB)')
    return zip_path


def main():
    ap = argparse.ArgumentParser(description='Package the MiroFish Windows desktop build')
    ap.add_argument('--mode', choices=['offline', 'setup'], default='setup')
    ap.add_argument('--wheels', default='', help='the Windows site-packages directory (required in offline mode)')
    ap.add_argument('--embed', default='', help='the embeddable Python directory (required in offline mode)')
    ap.add_argument('--exe', default=os.path.join(REPO_ROOT, 'build', 'MiroFish.exe'))
    ap.add_argument('--relaunch', action='store_true',
                    help='recompile and replace MiroFish.exe with the new icon right after packaging (needs zig)')
    ap.add_argument('--tools', default=os.path.join(REPO_ROOT, 'build', 'tools'),
                    help='the directory holding uv.exe (optional)')
    ap.add_argument('--out', default=os.path.join(REPO_ROOT, 'dist'))
    ap.add_argument('--zip', action='store_true')
    args = ap.parse_args()

    args.exe = ensure_launcher(args.exe)

    suffix = 'offline' if args.mode == 'offline' else 'setup'
    name = f'{APP_NAME}-{VERSION}-windows-x64-{suffix}'
    stage = os.path.join(args.out, name)
    os.makedirs(args.out, exist_ok=True)
    rmtree(stage)
    os.makedirs(stage, exist_ok=True)

    log(f'Mode: {args.mode} -> {stage}')
    assemble_app(stage)
    assemble_backend(stage)
    assemble_frontend(stage)
    write_requirements(stage)
    shutil.copy2(args.exe, os.path.join(stage, f'{APP_NAME}.exe'))
    shutil.copy2(os.path.join(DESKTOP_DIR, 'Setup.bat'), os.path.join(stage, 'Setup.bat'))
    shutil.copy2(os.path.join(DESKTOP_DIR, 'Setup-Slim.bat'), os.path.join(stage, 'Setup-Slim.bat'))
    copy_tools(stage, args.tools)
    write_console_bat(stage)
    write_readme(stage, args.mode)

    if args.mode == 'offline':
        if not (args.wheels and args.embed):
            raise SystemExit('offline mode needs --wheels and --embed')
        populate_runtime(stage, args.wheels, args.embed)

    total = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(stage) for f in fs)
    log(f'Uncompressed size: {total / 1024 / 1024:.1f} MB')

    # Recompile the launcher with icon.ico from the installer so the exe icon matches the window icon
    try:
        sys.path.insert(0, DESKTOP_DIR)
        from fetch_sources import compile_launcher
        compile_launcher(os.path.dirname(args.exe))
        shutil.copy2(os.path.join(os.path.dirname(args.exe), 'MiroFish.exe'),
                     os.path.join(stage, f'{APP_NAME}.exe'))
        log('Recompiled MiroFish.exe with the latest icon')
    except SystemExit as exc:
        log(f'Skipped recompiling the launcher: {exc}')
    except Exception as exc:  # noqa: BLE001
        log(f'Skipped recompiling the launcher: {exc}')

    if args.zip:
        make_zip(stage, args.out, name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
