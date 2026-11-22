"""
MiroFish Desktop - the desktop launcher (Windows)

Responsibilities:
  1. find a user-writable data directory (%APPDATA%\MiroFish, or data\ next to the install directory);
     on the first run, write the default .env template and generate a high-entropy SECRET_KEY.
  2. start the embedded Flask backend (listening on 127.0.0.1 only, on a random free port),
     serving the built frontend as static assets on the same port,
     so the desktop build has no CORS or hard-coded port problems.
  3. open the native window with pywebview (WebView2 / Edge on Windows).
  4. reclaim the backend logs and child processes (OASIS simulations) on exit.

Command-line arguments:
  --desktop        hide the console and go straight into desktop mode (the default of the packaged MiroFish.exe)
  --headless       start the local service only, no window (handy for troubleshooting; prints the URL)
  --port N         use the given port (a free port is picked automatically by default)
  --debug          enable the pywebview debugging features
"""

import atexit
import os
import secrets
import signal
import socket
import sys
import threading
import time

APP_NAME = 'MiroFish'


def _read_version() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(root, 'version.txt'),
                      os.path.join(os.path.dirname(root), 'desktop', 'version.txt')):
        if os.path.exists(candidate):
            try:
                with open(candidate, encoding='utf-8') as f:
                    value = f.read().strip()
                if value:
                    return value
            except OSError:
                pass
    return '0.1.0'


VERSION = _read_version()
DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 920

ENV_TEMPLATE = """\
# ===== MiroFish desktop configuration file =====
# This file lives in your user data directory and is never overwritten by an upgrade.
# Change the API keys below and restart MiroFish for them to take effect.

# ---- LLM configuration (any OpenAI-compatible endpoint)----
# e.g. Alibaba Bailian: https://dashscope.aliyuncs.com/compatible-mode/v1  /  qwen-plus
#     OpenAI  : https://api.openai.com/v1                          /  gpt-4o-mini
#     DeepSeek: https://api.deepseek.com/v1                        /  deepseek-chat
LLM_API_KEY=
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL_NAME=qwen-plus

# ---- Zep Cloud memory graph (https://app.getzep.com/)----
ZEP_API_KEY=

# ---- Optional: boost LLM ----
# LLM_BOOST_API_KEY=
# LLM_BOOST_BASE_URL=
# LLM_BOOST_MODEL_NAME=

# ---- Optional: simulation parameters ----
# OASIS_DEFAULT_MAX_ROUNDS=10
"""


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def app_root() -> str:
    """The directory holding the exe after packaging; the repository root when run from source."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_root() -> str:
    """The read-only asset directory (the built frontend, the bundled Python runtime)."""
    if is_frozen():
        # PyInstaller onedir: the assets live in the _internal directory
        return getattr(sys, '_MEIPASS', app_root())
    return app_root()


def data_root() -> str:
    """The writable data directory."""
    if os.environ.get('MIROFISH_HOME'):
        return os.path.abspath(os.environ['MIROFISH_HOME'])

    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.expanduser('~\\AppData\\Roaming')
        return os.path.join(base, APP_NAME)
    base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return os.path.join(base, APP_NAME)


def ensure_data_dir() -> str:
    """Prepare the data directory and .env, then return the data directory."""
    root = data_root()
    os.makedirs(root, exist_ok=True)
    os.makedirs(os.path.join(root, 'uploads'), exist_ok=True)
    os.makedirs(os.path.join(root, 'logs'), exist_ok=True)

    env_path = os.path.join(root, '.env')
    if not os.path.exists(env_path):
        content = ENV_TEMPLATE + f"\nSECRET_KEY={secrets.token_hex(32)}\n"
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(content)

    return root


def env_file() -> str:
    return os.path.join(data_root(), '.env')


# --------------------------------------------------------------------------- #
# Backend
# --------------------------------------------------------------------------- #
def backend_source_dir() -> str:
    root = resource_root()
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(root, 'backend'),
                      os.path.join(root, 'app', 'backend'),
                      os.path.join(here, 'backend'),
                      os.path.join(root, 'resources', 'backend')):
        if os.path.isdir(candidate):
            return candidate
    raise RuntimeError('Cannot find the backend code directory (backend/); the package may be incomplete.')


def frontend_dist_dir() -> str:
    root = resource_root()
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(root, 'frontend', 'dist'),
                      os.path.join(root, 'app', 'frontend_dist'),
                      os.path.join(here, 'frontend_dist'),
                      os.path.join(root, 'resources', 'frontend_dist')):
        if os.path.isdir(candidate):
            return candidate
    return ''


def free_port(preferred: int = 0) -> int:
    if preferred:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('127.0.0.1', preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def prepare_runtime_env(port: int) -> None:
    """The environment variables and sys.path setup used in desktop mode."""
    root = ensure_data_dir()

    os.environ['MIROFISH_DATA_DIR'] = os.path.join(root, 'uploads')
    os.environ['MIROFISH_ENV_FILE'] = env_file()
    os.environ['MIROFISH_DESKTOP'] = '1'
    os.environ['MIROFISH_VERSION'] = VERSION
    os.environ['FLASK_HOST'] = '127.0.0.1'
    os.environ['FLASK_PORT'] = str(port)
    # Disable Flask debug in the desktop build (otherwise the reloader child process is spawned)
    os.environ['FLASK_DEBUG'] = 'false'
    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

    backend_dir = backend_source_dir()
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    scripts_dir = os.path.join(backend_dir, 'scripts')
    if os.path.isdir(scripts_dir) and scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


def log_file() -> str:
    os.makedirs(os.path.join(data_root(), 'logs'), exist_ok=True)
    return os.path.join(data_root(), 'logs', 'desktop.log')


class _Tee(object):
    """Write the output to the console and the log file at once (harmless with no console after packaging)."""

    def __init__(self, stream, fileobj):
        self.stream = stream
        self.fileobj = fileobj

    def write(self, data):
        try:
            if self.stream is not None:
                self.stream.write(data)
        except Exception:
            pass
        try:
            self.fileobj.write(data)
            self.fileobj.flush()
        except Exception:
            pass

    def flush(self):
        for target in (self.stream, self.fileobj):
            try:
                if target is not None:
                    target.flush()
            except Exception:
                pass


def start_logging() -> None:
    try:
        fh = open(log_file(), 'a', encoding='utf-8', buffering=1)
    except Exception:
        return
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)


def start_backend(port: int):
    """Create the Flask app and serve the frontend static files. Returns app."""
    from flask import Flask, send_from_directory

    from app import create_app

    app = create_app()
    dist = frontend_dist_dir()

    if dist:
        dist_abs = os.path.abspath(dist)

        @app.route('/', defaults={'path': ''})
        @app.route('/<path:path>')
        def serve_spa(path):
            # The API and the health check are handled by Flask's own routes
            if path.startswith(('api/', 'health')):
                return {'success': False, 'error': 'Not Found'}, 404
            candidate = os.path.join(dist_abs, path)
            if path and os.path.isfile(candidate):
                return send_from_directory(dist_abs, path)
            # vue-router history mode: every other path falls back to index.html
            return send_from_directory(dist_abs, 'index.html')

    return app


def install_shutdown_hooks() -> None:
    """Reclaim the simulation child processes on any exit path (signal support is limited on Windows, so atexit is the safety net)."""
    def _cleanup(*_args):
        try:
            from app.services.simulation_runner import SimulationRunner
            SimulationRunner.cleanup_all_simulations()
        except Exception:
            pass

    try:
        atexit.register(_cleanup)
    except Exception:
        pass

    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, lambda *_: (os._exit(0)))
        except Exception:
            pass


def run_server(app, port: int) -> None:
    """Start with waitress (steadier than werkzeug on Windows, and it spawns no reloader)."""
    try:
        from waitress import serve
        serve(app, host='127.0.0.1', port=port, threads=16, channel_timeout=1800,
              clear_untrusted_proxy_headers=True)
    except ImportError:
        app.run(host='127.0.0.1', port=port, debug=False, threaded=True, use_reloader=False)


def wait_until_ready(port: int, timeout: float = 90.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    url = f'http://127.0.0.1:{port}/health'
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def icon_path() -> str:
    """Return the window icon path (optional; falls back to no icon when missing)."""
    root = resource_root()
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(root, 'app', 'icon.png'),
                      os.path.join(here, 'icon.png'),
                      os.path.join(root, 'icon.png')):
        if os.path.exists(candidate):
            return candidate

    # Fallback: generate one with the standard library alone
    try:
        from icon import png_bytes
        path = os.path.join(data_root(), 'mirofish-icon.png')
        if not os.path.exists(path):
            with open(path, 'wb') as f:
                f.write(png_bytes(256))
        return path
    except Exception:
        return ''


# --------------------------------------------------------------------------- #
# Main flow
# --------------------------------------------------------------------------- #
def run(headless: bool = False, port: int = 0, debug: bool = False) -> int:
    start_logging()
    port = free_port(port)

    print(f'[MiroFish] version {VERSION}')
    print(f'[MiroFish] data directory: {data_root()}')

    prepare_runtime_env(port)
    install_shutdown_hooks()
    app = start_backend(port)

    server_thread = threading.Thread(target=run_server, args=(app, port), daemon=True,
                                     name='mirofish-http')
    server_thread.start()

    url = f'http://127.0.0.1:{port}/'
    ready = wait_until_ready(port)
    print(f'[MiroFish] local service {"ready" if ready else "startup timed out"}: {url}')

    if headless:
        print('[MiroFish] headless mode, press Ctrl+C to exit')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    def on_closed():
        print('[MiroFish] the window was closed, exiting...')
        os._exit(0)

    import webview

    window = webview.create_window(
        f'{APP_NAME} - swarm intelligence prediction engine',
        url,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        min_size=(1024, 700),
        background_color='#091426',
    )
    window.events.closed += on_closed

    icon = icon_path()
    webview.start(debug=debug, private_mode=False,
                  storage_path=os.path.join(data_root(), 'webview'),
                  icon=icon if os.path.exists(icon) else None)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    headless = '--headless' in argv
    debug = '--debug' in argv
    port = 0
    for i, arg in enumerate(argv):
        if arg == '--port' and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                pass

    if '--browser' in argv:
        # Open the system default browser (the fallback when WebView2 is unavailable)
        import webbrowser as _wb
        port = free_port(port)
        prepare_runtime_env(port)
        app = start_backend(port)
        threading.Thread(target=run_server, args=(app, port), daemon=True).start()
        wait_until_ready(port)
        _wb.open(f'http://127.0.0.1:{port}/')
        print('[MiroFish] opened in the browser; close this window to stop the service')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    try:
        return run(headless=headless, port=port, debug=debug)
    except Exception:
        import traceback
        traceback.print_exc()
        if not headless:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    'MiroFish failed to start.\n\nSee the log for details:\n' + log_file(),
                    'MiroFish', 0x10)
            except Exception:
                pass
        return 1


if __name__ == '__main__':
    sys.exit(main())
