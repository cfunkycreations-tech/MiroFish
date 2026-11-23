"""
Build the Windows installer (single .exe) from the packaged desktop build.

Input  : dist/MiroFish-<version>-windows-x64-setup/     (desktop/build.py --mode setup)
Output : dist/MiroFish-<version>-windows-x64-setup.exe  (NSIS installer, per-user)

Usage:
    python desktop/build_installer.py            # package + compile
    python desktop/build_installer.py --no-build # compile only

Requirements:
    * NSIS 3 (makensis) -- set NSISDIR to a portable NSIS tree, or install it normally
    * the payload tree, or a machine that can run desktop/build.py

Notes:
    The installer is a 32-bit PE on purpose: Windows runs 32-bit setup programs on
    x64 just fine (the payload itself is x64), which keeps one installer working on
    every Windows 10/11 machine. The launcher and the app inside stay x86_64.
"""

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP_DIR = os.path.join(REPO_ROOT, 'desktop')


def read_version() -> str:
    with open(os.path.join(DESKTOP_DIR, 'version.txt'), encoding='utf-8') as f:
        return f.read().strip() or '0.1.0'


def log(msg: str) -> None:
    print(f'[installer] {msg}', flush=True)


def find_makensis() -> str:
    """Locate makensis: NSISDIR first, then PATH, then the usual Windows location."""
    nsis_dir = os.environ.get('NSISDIR')
    candidates = []
    if nsis_dir:
        candidates += [os.path.join(nsis_dir, 'makensis'), os.path.join(nsis_dir, 'makensis.exe'),
                       os.path.join(nsis_dir, 'Bin', 'makensis.exe')]
    for name in ('makensis', 'makensis.exe'):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates += [r'C:\Program Files (x86)\NSIS\makensis.exe', r'C:\Program Files\NSIS\makensis.exe']
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise SystemExit('makensis was not found. Install NSIS 3 (https://nsis.sourceforge.io/) '
                     'or point the NSISDIR environment variable at a portable NSIS tree.')


def build_payload(out_dir: str) -> None:
    """Package the desktop build (frontend + backend + launcher + uv.exe)."""
    log('Packaging the desktop build ...')
    subprocess.check_call([sys.executable, os.path.join(DESKTOP_DIR, 'build.py'),
                           '--mode', 'setup', '--out', out_dir], cwd=REPO_ROOT)


def force_gui_subsystem(path: str) -> None:
    """Mark the installer as a GUI program.

    A console subsystem would flash a console window on double-click. The NSIS
    stub is linked for the console here, so the field is patched in place; the
    offset comes straight from the PE optional header.
    """
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise SystemExit(f'{path} is not a PE file')
    opt = pe + 24
    magic = struct.unpack_from('<H', data, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise SystemExit(f'{path}: unexpected optional header magic 0x{magic:04x}')
    offset = opt + 68          # Subsystem: same offset in PE32 and PE32+
    current = struct.unpack_from('<H', data, offset)[0]
    if current == 3:           # IMAGE_SUBSYSTEM_WINDOWS_CUI
        struct.pack_into('<H', data, offset, 2)   # IMAGE_SUBSYSTEM_WINDOWS_GUI
        with open(path, 'wb') as f:
            f.write(data)
        log('Subsystem: console -> GUI (no console window on double-click)')


def summarize(path: str) -> None:
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        data = f.read()
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    machine = struct.unpack_from('<H', data, pe + 4)[0]
    subsystem = struct.unpack_from('<H', data, pe + 24 + 68)[0]
    digest = hashlib.sha256(data).hexdigest()
    arch = {0x014C: 'x86', 0x8664: 'x64'}.get(machine, hex(machine))
    log(f'Output : {path}')
    log(f'Size   : {size:,} bytes ({size / 1024 / 1024:.1f} MB)')
    log(f'Arch   : {arch} (setup stub; payload is x64)')
    log(f'Subsys : {subsystem} (2 = GUI)')
    log(f'SHA-256: {digest}')


def main() -> int:
    version = read_version()
    ap = argparse.ArgumentParser(description='Build the MiroFish Windows installer')
    ap.add_argument('--out', default=os.path.join(REPO_ROOT, 'dist'))
    ap.add_argument('--no-build', action='store_true', help='skip packaging the payload tree')
    args = ap.parse_args()

    name = f'MiroFish-{version}-windows-x64-setup'
    payload = os.path.join(args.out, name)
    if not args.no_build or not os.path.isdir(payload):
        build_payload(args.out)
    if not os.path.isdir(payload):
        raise SystemExit(f'the payload tree is missing: {payload}')

    makensis = find_makensis()
    log(f'makensis: {makensis}')
    env = dict(os.environ)
    script = os.path.join(DESKTOP_DIR, 'installer.nsi')
    # installer.nsi uses paths relative to desktop/, so compile from there
    subprocess.check_call([makensis, '-V2', '-DAPP_VERSION=' + version,
                           os.path.basename(script)], cwd=DESKTOP_DIR, env=env)

    exe = os.path.join(args.out, name + '.exe')
    if not os.path.isfile(exe):
        raise SystemExit(f'the installer was not produced: {exe}')
    force_gui_subsystem(exe)
    summarize(exe)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
