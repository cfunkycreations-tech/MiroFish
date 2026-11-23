"""
Desktop-only endpoints

Enabled only in the local desktop build (the launcher sets MIROFISH_DESKTOP=1):
  GET  /api/desktop/config        read the current configuration (keys masked)
  POST /api/desktop/config        save the configuration to the .env in the user data directory
  POST /api/desktop/config/test   test LLM / Zep connectivity
  GET  /api/desktop/status        runtime information
"""

import os
import re

from flask import jsonify, request

from . import desktop_bp
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.desktop')

MANAGED_KEYS = [
    'LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL_NAME',
    'LLM_BOOST_API_KEY', 'LLM_BOOST_BASE_URL', 'LLM_BOOST_MODEL_NAME',
    'ZEP_API_KEY', 'OASIS_DEFAULT_MAX_ROUNDS',
]

# The keys this endpoint may write, so arbitrary environment variables cannot be injected
_KEY_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')


def env_path() -> str:
    return os.environ.get('MIROFISH_ENV_FILE') or os.path.join(
        os.path.dirname(__file__), '../../../.env')


# Providers that run locally serve without authentication: the OpenAI SDK still
# insists on a non-empty key, so fall back to a harmless placeholder.
LOCAL_HOSTS = ('127.0.0.1', 'localhost', '0.0.0.0', '[::1]', 'host.docker.internal')


def needs_key(base_url: str) -> bool:
    """Whether this endpoint is a remote provider that authenticates with an API key."""
    host = (base_url or '').split('//', 1)[-1].split('/', 1)[0].rsplit(':', 1)[0].lower()
    return host not in LOCAL_HOSTS


def is_configured(env: dict) -> bool:
    """Enough settings for the app to start: an LLM (or a keyless local one) plus Zep."""
    llm_key = env.get('LLM_API_KEY') or os.environ.get('LLM_API_KEY', '')
    llm_url = env.get('LLM_BASE_URL') or os.environ.get('LLM_BASE_URL', '')
    zep_key = env.get('ZEP_API_KEY') or os.environ.get('ZEP_API_KEY', '')
    return bool(zep_key) and bool(llm_key or not needs_key(llm_url))


def mask(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 8:
        return '*' * len(value)
    return f'{value[:4]}****...{value[-4:]}'


def read_env() -> dict:
    data = {}
    path = env_path()
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                data[key.strip()] = value.strip()
    return data


def write_env(updates: dict) -> None:
    path = env_path()
    lines = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()

    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key = stripped.partition('=')[0].strip()
            if key in remaining:
                out.append(f'{key}={remaining.pop(key)}')
                continue
        out.append(line)

    for key, value in remaining.items():
        out.append(f'{key}={value}')

    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(out).rstrip('\n') + '\n')


def apply_to_runtime(updates: dict) -> None:
    """Make the change take effect immediately (without restarting the process)."""
    for key, value in updates.items():
        os.environ[key] = value
        if hasattr(Config, key):
            setattr(Config, key, value)
        # The in-process LLM clients are usually cached at module level, so only the environment and Config are refreshed here;
        # newly created clients pick the new values up.


@desktop_bp.route('/config', methods=['GET'])
def get_config():
    env = read_env()
    payload = {}
    for key in MANAGED_KEYS:
        value = env.get(key) or os.environ.get(key, '')
        payload[key] = {'value': value, 'masked': mask(value)}
    payload['env_file'] = env_path()
    payload['configured'] = is_configured({k: v['value'] for k, v in payload.items()
                                           if isinstance(v, dict)})
    return jsonify({'success': True, 'data': payload})


@desktop_bp.route('/config', methods=['POST'])
def save_config():
    body = request.get_json(silent=True) or {}
    updates = {}
    for key, value in body.items():
        if key not in MANAGED_KEYS or not _KEY_RE.match(key):
            continue
        value = '' if value is None else str(value).strip()
        # Skip values the user submitted unchanged (still masked)
        if value and '*' in value and '...' in value:
            continue
        updates[key] = value

    if not updates:
        return jsonify({'success': False, 'error': 'there is nothing to save'}), 400

    try:
        write_env(updates)
        apply_to_runtime(updates)
        logger.info(f'Desktop configuration updated: {sorted(updates)}')
        return jsonify({'success': True, 'data': {'env_file': env_path(), 'updated': sorted(updates)}})
    except Exception as exc:  # noqa: BLE001
        logger.error(f'Failed to save the configuration: {exc}')
        return jsonify({'success': False, 'error': str(exc)}), 500


def _test_llm(base_url: str, api_key: str, model: str) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=0)
    resp = client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': 'Reply with two words: it works'}],
        max_tokens=16,
    )
    text = (resp.choices[0].message.content or '').strip()
    return {'ok': True, 'detail': f'the model replied: {text[:40]}'}


def _test_zep(api_key: str) -> dict:
    from zep_cloud.client import Zep

    client = Zep(api_key=api_key)
    # Listing a single user in time order counts as valid credentials (the method name differs between versions, so try each)
    last_error = None
    for call in (
        lambda: client.user.list_ordered(page_number=1, page_size=1),
        lambda: client.user.list_ordered(),
    ):
        try:
            call()
            return {'ok': True, 'detail': 'the Zep credentials are valid'}
        except AttributeError as exc:
            last_error = exc
            continue
        except TypeError as exc:
            last_error = exc
            continue
    raise last_error or RuntimeError('the Zep client is unavailable')


@desktop_bp.route('/config/test', methods=['POST'])
def test_config():
    body = request.get_json(silent=True) or {}
    env = read_env()

    def pick(key, default=''):
        return (body.get(key) or env.get(key) or os.environ.get(key) or default).strip()

    results = {}

    base_url = pick('LLM_BASE_URL', 'https://api.openai.com/v1')
    model = pick('LLM_MODEL_NAME', 'gpt-4o-mini')
    llm_key = pick('LLM_API_KEY')
    if not llm_key and needs_key(base_url):
        results['llm'] = {'ok': False, 'detail': 'LLM_API_KEY is not set'}
    else:
        try:
            results['llm'] = _test_llm(base_url, llm_key or 'local', model)
        except Exception as exc:  # noqa: BLE001
            results['llm'] = {'ok': False, 'detail': f'{type(exc).__name__}: {exc}'[:300]}

    zep_key = pick('ZEP_API_KEY')
    if not zep_key:
        results['zep'] = {'ok': False, 'detail': 'ZEP_API_KEY is not set'}
    else:
        try:
            results['zep'] = _test_zep(zep_key)
        except Exception as exc:  # noqa: BLE001
            results['zep'] = {'ok': False, 'detail': f'{type(exc).__name__}: {exc}'[:300]}

    return jsonify({'success': True, 'data': results})


@desktop_bp.route('/update-check', methods=['GET'])
def update_check():
    """Check for a new version (fails silently: offline or a private repository must not break anything)."""
    import urllib.request
    import json

    repo = os.environ.get('MIROFISH_REPO', 'cfunkycreations-tech/MiroFish')
    current = os.environ.get('MIROFISH_VERSION', '0.1.0')
    url = f'https://api.github.com/repos/{repo}/releases/latest'

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MiroFish-Desktop'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.load(resp)
        latest = (data.get('tag_name') or '').lstrip('v')
        assets = [a.get('name', '') for a in data.get('assets', [])]
        return jsonify({
            'success': True,
            'data': {
                'current': current,
                'latest': latest,
                'update_available': bool(latest) and latest != current,
                'url': data.get('html_url', ''),
                'assets': assets,
            },
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({'success': True, 'data': {'current': current, 'update_available': False,
                                                  'error': str(exc)[:200]}})


@desktop_bp.route('/status', methods=['GET'])
def status():
    import sys
    data_dir = Config.UPLOAD_FOLDER
    return jsonify({
        'success': True,
        'data': {
            'desktop': os.environ.get('MIROFISH_DESKTOP') == '1',
            'python': sys.version.split()[0],
            'data_dir': os.path.abspath(data_dir),
            'env_file': env_path(),
            'configured': bool(Config.ZEP_API_KEY) and bool(
                Config.LLM_API_KEY or not needs_key(Config.LLM_BASE_URL)),
        },
    })
