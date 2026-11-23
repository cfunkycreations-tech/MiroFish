"""
Unit tests for the desktop launcher (no Windows / pywebview needed)

Run:
    python -m pytest desktop/tests -q
"""

import os
import sys

import pytest

DESKTOP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(os.path.dirname(DESKTOP_DIR), 'backend')

sys.path.insert(0, DESKTOP_DIR)
sys.path.insert(0, BACKEND_DIR)


DESKTOP_APP_PATH = os.path.join(DESKTOP_DIR, 'app.py')


def _load_desktop_module():
    """Load desktop/app.py by file path (so it does not clash with the backend app package)."""
    import importlib.util
    import uuid

    name = f'mirofish_desktop_{uuid.uuid4().hex[:8]}'
    spec = importlib.util.spec_from_file_location(name, DESKTOP_APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def desktop_app(tmp_path, monkeypatch):
    """Load desktop/app.py and point the data directory at a temporary directory."""
    monkeypatch.setenv('MIROFISH_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('MIROFISH_DATA_DIR', raising=False)
    monkeypatch.delenv('MIROFISH_ENV_FILE', raising=False)
    yield _load_desktop_module()


def test_ensure_data_dir_creates_env_and_secret(desktop_app):
    root = desktop_app.ensure_data_dir()

    env_path = os.path.join(root, '.env')
    assert os.path.isfile(env_path)
    assert os.path.isdir(os.path.join(root, 'uploads'))

    content = open(env_path, encoding='utf-8').read()
    assert 'LLM_API_KEY=' in content
    assert 'ZEP_API_KEY=' in content
    assert 'SECRET_KEY=' in content

    # SECRET_KEY must be random and long enough
    secret = [line for line in content.splitlines() if line.startswith('SECRET_KEY=')][0]
    assert len(secret.split('=', 1)[1]) >= 32


def test_ensure_data_dir_is_idempotent(desktop_app):
    root = desktop_app.ensure_data_dir()
    env_path = os.path.join(root, '.env')

    with open(env_path, 'w', encoding='utf-8') as f:
        f.write('LLM_API_KEY=keep-me\n')

    desktop_app.ensure_data_dir()
    assert 'keep-me' in open(env_path, encoding='utf-8').read()


def test_prepare_runtime_env_sets_isolated_paths(desktop_app, tmp_path):
    port = desktop_app.free_port(0)
    assert 1024 < port < 65536

    desktop_app.prepare_runtime_env(port)

    root = desktop_app.data_root()
    assert os.environ['MIROFISH_DATA_DIR'] == os.path.join(root, 'uploads')
    assert os.environ['MIROFISH_ENV_FILE'] == os.path.join(root, '.env')
    assert os.environ['MIROFISH_DESKTOP'] == '1'
    assert os.environ['FLASK_HOST'] == '127.0.0.1'
    assert os.environ['FLASK_PORT'] == str(port)
    assert os.environ['FLASK_DEBUG'] == 'false'
    assert os.path.isdir(desktop_app.backend_source_dir())


def test_backend_and_frontend_resolution(desktop_app):
    assert os.path.isfile(os.path.join(desktop_app.backend_source_dir(), 'app', '__init__.py'))
    dist = desktop_app.frontend_dist_dir()
    assert dist == '' or os.path.isfile(os.path.join(dist, 'index.html'))


def test_free_port_is_actually_free(desktop_app):
    import socket

    port = desktop_app.free_port(0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', port))  # must not raise


def test_spa_serves_index_and_assets(desktop_app, tmp_path, monkeypatch):
    """The same port serves the frontend pages, /api and /health."""
    dist = tmp_path / 'dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'index.html').write_text('<html>mirofish-spa</html>', encoding='utf-8')
    (dist / 'assets' / 'app.js').write_text('console.log(1)', encoding='utf-8')

    monkeypatch.setattr(desktop_app, 'frontend_dist_dir', lambda: str(dist))
    desktop_app.prepare_runtime_env(desktop_app.free_port(0))
    client = desktop_app.start_backend(0).test_client()

    # The home page and the frontend deep links all fall back to index.html
    assert 'mirofish-spa' in client.get('/').get_data(as_text=True)
    assert 'mirofish-spa' in client.get('/simulation/abc/start').get_data(as_text=True)

    # Static assets are served normally
    asset = client.get('/assets/app.js')
    assert asset.status_code == 200
    assert 'console.log' in asset.get_data(as_text=True)

    # The API routes are not swallowed by the SPA fallback
    assert client.get('/health').get_json()['status'] == 'ok'
    assert client.get('/api/desktop/status').get_json()['data']['desktop'] is True
    assert client.get('/api/not-exists').status_code == 404


def test_config_api_roundtrip(desktop_app):
    desktop_app.prepare_runtime_env(desktop_app.free_port(0))
    from app import create_app  # the backend package (backend/app)

    client = create_app().test_client()

    status = client.get('/api/desktop/status').get_json()['data']
    assert status['desktop'] is True
    assert status['configured'] is False

    payload = {
        'LLM_API_KEY': 'sk-unit-test-0001',
        'LLM_BASE_URL': 'https://api.deepseek.com/v1',
        'LLM_MODEL_NAME': 'deepseek-chat',
        'ZEP_API_KEY': 'z_unit_test_0001',
    }
    saved = client.post('/api/desktop/config', json=payload).get_json()
    assert saved['success'] is True

    env_path = os.environ['MIROFISH_ENV_FILE']
    content = open(env_path, encoding='utf-8').read()
    assert 'LLM_API_KEY=sk-unit-test-0001' in content
    assert 'ZEP_API_KEY=z_unit_test_0001' in content

    # A known key is replaced in place rather than appended
    assert content.count('LLM_API_KEY=') == 1

    status = client.get('/api/desktop/status').get_json()['data']
    assert status['configured'] is True

    # An unchanged masked value must not overwrite the real one
    masked = client.get('/api/desktop/config').get_json()['data']['LLM_API_KEY']['masked']
    assert masked != 'sk-unit-test-0001'
    client.post('/api/desktop/config', json={'LLM_API_KEY': masked})
    assert 'LLM_API_KEY=sk-unit-test-0001' in open(env_path, encoding='utf-8').read()


def test_config_api_rejects_unknown_keys(desktop_app):
    desktop_app.prepare_runtime_env(desktop_app.free_port(0))
    from app import create_app

    client = create_app().test_client()
    res = client.post('/api/desktop/config', json={'PATH': '/evil', 'LLM_API_KEY': 'x'})
    assert res.status_code == 200
    content = open(os.environ['MIROFISH_ENV_FILE'], encoding='utf-8').read()
    assert 'PATH=/evil' not in content


def test_icon_module_produces_valid_png_and_ico():
    sys.path.insert(0, DESKTOP_DIR)
    import icon

    png = icon.png_bytes(32)
    assert png[:8] == b'\x89PNG\r\n\x1a\n'

    ico = icon.ico_bytes(sizes=(16, 32))
    assert ico[:4] == b'\x00\x00\x01\x00'
    count = int.from_bytes(ico[4:6], 'little')
    assert count == 2


def test_shipped_icon_is_windows_ready():
    ico_path = os.path.join(DESKTOP_DIR, 'icon.ico')
    if not os.path.exists(ico_path):
        pytest.skip('icon.ico has not been generated yet (run desktop/fetch_sources.py)')

    data = open(ico_path, 'rb').read()
    assert data[:4] == b'\x00\x00\x01\x00'
    count = int.from_bytes(data[4:6], 'little')
    sizes = [data[6 + i * 16] or 256 for i in range(count)]
    assert 16 in sizes and 256 in sizes


def test_local_providers_need_no_api_key():
    """Ollama / LM Studio / OmniRoute serve without authentication, so no key is required."""
    sys.path.insert(0, BACKEND_DIR)
    from app.utils.llm_client import is_local_endpoint, resolve_api_key

    assert is_local_endpoint('http://127.0.0.1:11434/v1')
    assert is_local_endpoint('http://localhost:20128/v1')
    assert is_local_endpoint('http://0.0.0.0:1234/v1')
    assert not is_local_endpoint('https://openrouter.ai/api/v1')
    assert not is_local_endpoint('https://api.groq.com/openai/v1')

    # A real key is always kept, a local endpoint gets a placeholder, a remote one stays empty
    assert resolve_api_key('sk-real', 'https://openrouter.ai/api/v1') == 'sk-real'
    assert resolve_api_key('', 'http://127.0.0.1:11434/v1') == 'local'
    assert resolve_api_key('', 'https://openrouter.ai/api/v1') == ''


def test_free_provider_endpoints_are_documented():
    """The free routes the user asked for must be discoverable from the shipped config."""
    for name in ('.env.example', os.path.join('desktop', 'env.example')):
        text = open(os.path.join(os.path.dirname(DESKTOP_DIR), name), encoding='utf-8').read()
        assert 'https://openrouter.ai/api/v1' in text
        assert '127.0.0.1:20128' in text          # OmniRoute
        assert '127.0.0.1:11434' in text          # Ollama
        assert 'api.groq.com' in text             # Groq free tier
        assert 'generativelanguage.googleapis.com' in text   # Gemini free tier


def test_local_llm_counts_as_configured(desktop_app):
    """With Ollama / OmniRoute an empty LLM key is fine, so the app must not block setup."""
    desktop_app.prepare_runtime_env(desktop_app.free_port(0))
    from app import create_app

    client = create_app().test_client()

    # A remote provider without a key: still unconfigured
    client.post('/api/desktop/config', json={
        'LLM_BASE_URL': 'https://openrouter.ai/api/v1',
        'LLM_API_KEY': '',
        'ZEP_API_KEY': 'z_test_key',
    })
    assert client.get('/api/desktop/status').get_json()['data']['configured'] is False

    # A local gateway needs no key, so this counts as configured
    client.post('/api/desktop/config', json={
        'LLM_BASE_URL': 'http://127.0.0.1:20128/v1',
        'LLM_MODEL_NAME': 'auto',
        'ZEP_API_KEY': 'z_test_key',
    })
    assert client.get('/api/desktop/status').get_json()['data']['configured'] is True


def test_launcher_binary_is_english_and_gui():
    """The committed launcher must not ship Chinese strings and must be a GUI app."""
    exe = os.path.join(DESKTOP_DIR, 'MiroFish.exe')
    if not os.path.exists(exe):
        pytest.skip('MiroFish.exe has not been built yet (run desktop/fetch_sources.py)')

    import struct

    data = open(exe, 'rb').read()
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    assert data[pe:pe + 4] == b'PE\0\0'
    assert struct.unpack_from('<H', data, pe + 4)[0] == 0x8664        # x86-64
    assert struct.unpack_from('<H', data, pe + 24 + 68)[0] == 2        # GUI subsystem

    # The diagnostic dialog was Chinese in an earlier build; it must stay English
    for chinese in ('初始化未完成', '找不到', '运行环境'):
        assert chinese.encode('utf-16-le') not in data
    assert 'MiroFish setup did not finish'.encode('utf-16-le') in data
    assert 'runtime\\setup.log'.encode('utf-16-le') in data


def test_installer_script_uninstalls_from_the_same_registry_view():
    """The uninstaller is 32-bit, so it has to be told to use the 64-bit view."""
    script = open(os.path.join(DESKTOP_DIR, 'installer.nsi'), encoding='utf-8').read()
    uninstall = script.split('Section "Uninstall"', 1)[1]
    assert 'SetRegView 64' in uninstall
    assert '/S' in script and '/D=' in script                     # documented switches
    assert 'WriteUninstaller' in script


def test_setup_finds_miniconda_and_anaconda():
    """Miniconda/Anaconda have python.exe but no py.exe and nothing on PATH."""
    script = open(os.path.join(DESKTOP_DIR, 'Setup.bat'), encoding='utf-8').read()
    assert 'miniconda3' in script and 'anaconda3' in script
    assert 'where conda' in script
    # the abort message must name conda as a way out
    assert 'Miniconda' in script


def test_setup_can_bootstrap_python_with_uv():
    """Without any system Python, uv has to be allowed to fetch one (no winget needed)."""
    script = open(os.path.join(DESKTOP_DIR, 'Setup.bat'), encoding='utf-8').read()
    assert 'UV_PYTHON_DOWNLOADS=automatic' in script
    assert 'UV_PYTHON_DOWNLOADS=never' not in script
    assert 'venv --python 3.12' in script
    # the private interpreter must stay inside the install directory
    assert 'UV_PYTHON_INSTALL_DIR=%ROOT%' in script


def test_launcher_keeps_the_setup_console_visible():
    """The GUI launcher has no console, so it must open one with visible output."""
    source = open(os.path.join(DESKTOP_DIR, 'launcher.c'), encoding='utf-8').read()
    assert 'CREATE_NEW_CONSOLE' in source
    assert 'cmd.exe /S /c' in source         # /S: the outer quote pair is stripped exactly
    assert ' 32768' in source                # long download paths are not truncated
    # The caret escaped the ampersands, so cmd printed the rest of the line instead of
    # running Setup.bat: the window opened and stayed empty. Never come back.
    assert '^&' not in source
    assert 'timeout /t' in source            # the window keeps the text readable


def test_setup_logs_to_a_file_only_when_asked():
    """The launcher passes --log; a double-clicked script must keep its output on screen."""
    script = open(os.path.join(DESKTOP_DIR, 'Setup.bat'), encoding='utf-8').read()
    assert '--log-inner' in script and '--log' in script
    # the logging re-run must be real CRLF batch, not escaped ampersands
    assert 'cmd.exe" /s /c ""%~f0" --log-inner' in script
    assert 'runtime\\setup.log' in script
