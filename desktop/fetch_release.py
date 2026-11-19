"""
从 GitHub 直接取回桌面版安装包（不需要 git、不需要 GitHub CLI）

用法：
    python desktop/fetch_release.py                 # 下载到 ./downloads
    python desktop/fetch_release.py -o ~/Desktop    # 指定目录

原理：调用 GitHub 的公开 Contents API，并直接取 raw 内容；
      下载完成后自动校验 SHA-256。
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
    # 直接走 API 的 raw 内容（download_url 指向 raw.githubusercontent.com，
    # 部分网络环境对该域名支持不佳）
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
        print(f'[fetch] ! 大小不一致：期望 {expected_size}，实际 {written}')
        return None
    print(f'[fetch]   SHA-256: {digest.hexdigest()}')
    print(f'[fetch]   已保存: {dest}')
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
            print(f'[fetch] ! 下载失败 {path}: {exc}')
            print('        可手动打开: '
                  f'https://github.com/{REPO}/blob/{BRANCH}/{path}')

    if not ok:
        return 1
    print('\n[fetch] 完成。解压后双击 MiroFish.exe 即可运行。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
