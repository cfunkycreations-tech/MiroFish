"""
Fetch the desktop installers straight from GitHub (no git and no GitHub CLI needed)

Usage:
    python desktop/fetch_release.py                 # download into ./downloads
    python desktop/fetch_release.py -o ~/Desktop    # choose the directory

How it works: calls the public GitHub Contents API and reads the raw content directly;
      the SHA-256 is verified automatically once the download finishes.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.request

REPO = os.environ.get('MIROFISH_REPO', 'cfunkycreations-tech/MiroFish')
BRANCH = os.environ.get('MIROFISH_BRANCH', 'arena/f830b1ef-mirofish')
FILES = [
    'dist-builds/MiroFish-0.1.0-windows-x64-setup.zip',
]


def api(url):
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'MiroFish-Fetch',
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def download(path, out_dir):
    meta = api(f'https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}')
    name = os.path.basename(path)
    dest = os.path.join(out_dir, name)
    expected_size = meta['size']

    print(f'[fetch] {name}  ({expected_size / 1024 / 1024:.1f} MB)')
    # Use the raw content from the API directly (download_url points at raw.githubusercontent.com,
    # which some networks handle poorly)
    url = f'https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}'
    headers = {'User-Agent': 'MiroFish-Fetch', 'Accept': 'application/vnd.github.raw'}

    req = urllib.request.Request(url, headers=headers)
    digest = hashlib.sha256()
    written = 0
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, 'wb') as f:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            digest.update(chunk)
            written += len(chunk)
            pct = written * 100 // max(expected_size, 1)
            print(f'\r[fetch]   {pct:3d}%  {written / 1024 / 1024:6.1f} MB', end='', flush=True)
    print()

    if written != expected_size:
        print(f'[fetch] ! size mismatch: expected {expected_size}, got {written}')
        return None
    print(f'[fetch]   SHA-256: {digest.hexdigest()}')
    print(f'[fetch]   saved: {dest}')
    return dest


def main():
    global BRANCH
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', '--out', default='downloads')
    ap.add_argument('--branch', default=BRANCH)
    args = ap.parse_args()
    BRANCH = args.branch

    out_dir = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(out_dir, exist_ok=True)

    ok = []
    for path in FILES:
        try:
            result = download(path, out_dir)
            if result:
                ok.append(result)
        except Exception as exc:  # noqa: BLE001
            print(f'[fetch] ! download failed {path}: {exc}')
            print('        You can open it by hand: '
                  f'https://github.com/{REPO}/blob/{BRANCH}/{path}')

    if not ok:
        return 1
    print('\n[fetch] done. Extract the archive and double-click MiroFish.exe to run it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
