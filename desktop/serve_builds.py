"""
Local download server: exposes the installers in dist-builds/ on a web page for easy download.

Usage:
    python desktop/serve_builds.py --dir dist-builds --port 8080

open http://<host>:8080/ in a browser to see the installer list and download one
(in the Arena sandbox it shows up as a Live Preview).
"""

import argparse
import html
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, quote


def human(size: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.1f} {unit}' if unit != 'B' else f'{size} B'
        size /= 1024
    return f'{size:.1f} GB'


INDEX_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Download MiroFish installers</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; padding: 48px 20px;
    font-family: -apple-system, "Segoe UI", "Noto Sans SC", system-ui, sans-serif;
    background: radial-gradient(circle at 15% 0%, #16233d 0%, #080d17 60%);
    color: #e8eef8; display: flex; justify-content: center;
  }}
  .wrap {{ width: min(820px, 100%); }}
  header {{ display: flex; align-items: center; gap: 14px; margin-bottom: 10px; }}
  .logo {{
    width: 46px; height: 46px; border-radius: 12px; flex: none;
    background: linear-gradient(140deg, #0b1b33, #123c5c);
    display: grid; place-items: center; font-size: 22px;
  }}
  h1 {{ margin: 0; font-size: 24px; letter-spacing: .4px; }}
  .sub {{ color: #8fa2bd; font-size: 14px; line-height: 1.7; margin: 6px 0 28px; }}
  .card {{
    display: flex; align-items: center; gap: 16px; padding: 18px 20px;
    background: rgba(20, 30, 49, .88); border: 1px solid rgba(120, 160, 220, .2);
    border-radius: 14px; margin-bottom: 14px; transition: border-color .18s ease;
  }}
  .card:hover {{ border-color: #22d3ee; }}
  .icon {{
    width: 42px; height: 42px; border-radius: 10px; flex: none;
    background: rgba(34, 211, 238, .12); display: grid; place-items: center; font-size: 20px;
  }}
  .meta {{ flex: 1; min-width: 0; }}
  .name {{ font-size: 14px; font-weight: 600; word-break: break-all; }}
  .size {{ color: #7d8fa9; font-size: 12px; margin-top: 4px; }}
  .btn {{
    flex: none; padding: 10px 20px; border-radius: 10px; text-decoration: none;
    background: linear-gradient(135deg, #22d3ee, #3b82f6); color: #04101f;
    font-weight: 700; font-size: 13px;
  }}
  .btn:hover {{ filter: brightness(1.08); }}
  .empty {{ color: #7d8fa9; font-size: 14px; }}
  ol {{ color: #9fb0c9; font-size: 13px; line-height: 2; padding-left: 20px; margin-top: 28px; }}
  code {{ background: rgba(255,255,255,.07); padding: 2px 6px; border-radius: 5px; }}
  footer {{ margin-top: 34px; color: #64789a; font-size: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">🐟</div>
    <h1>Download MiroFish installers</h1>
  </header>
  <p class="sub">Windows x64 desktop build - click Download, then extract the archive and double-click <code>MiroFish.exe</code>.</p>
  {cards}
  <ol>
    <li>Extract it into a directory you can write to (not <code>C:\\Program Files</code>).</li>
    <li>Double-click <code>MiroFish.exe</code>; the first run downloads the runtime, just confirm the prompt.</li>
    <li>The settings page opens automatically on first launch: fill in <code>LLM_API_KEY</code> and <code>ZEP_API_KEY</code>, then save.</li>
    <li>If it fails to start, double-click <code>MiroFish-Console.bat</code> to see the full error log.</li>
  </ol>
  <footer>Served by desktop/serve_builds.py - the files come straight from the build output directory</footer>
</div>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    directory = '.'

    def log_message(self, fmt, *args):  # keep it quiet
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _files(self):
        items = []
        for name in sorted(os.listdir(self.directory)):
            path = os.path.join(self.directory, name)
            if os.path.isfile(path):
                items.append((name, os.path.getsize(path)))
        return items

    def _send_index(self):
        cards = []
        for name, size in self._files():
            lower = name.lower()
            icon = '🗜️' if lower.endswith('.zip') else '📄'
            cards.append(
                '<div class="card">'
                f'<div class="icon">{icon}</div>'
                f'<div class="meta"><div class="name">{html.escape(name)}</div>'
                f'<div class="size">{human(size)}</div></div>'
                f'<a class="btn" href="/download/{quote(name)}">Download</a>'
                '</div>'
            )
        if not cards:
            cards.append('<p class="empty">No installers in this directory yet; run the desktop packaging script first.</p>')

        body = INDEX_TEMPLATE.format(cards='\n  '.join(cards)).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _send_file(self, name):
        path = os.path.join(self.directory, name)
        if not os.path.isfile(path):
            self.send_error(404, 'Not Found')
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Length', str(size))
        self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.send_header('Accept-Ranges', 'none')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(1024 * 256)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self._send_index()
        elif self.path.startswith('/download/'):
            self._send_file(unquote(self.path[len('/download/'):]))
        else:
            self.send_error(404, 'Not Found')

    def do_HEAD(self):
        self.do_GET()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='dist-builds')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--host', default='0.0.0.0')
    args = ap.parse_args()

    directory = os.path.abspath(args.dir)
    if not os.path.isdir(directory):
        raise SystemExit(f'directory does not exist: {directory}')

    Handler.directory = directory
    files = [(n, os.path.getsize(os.path.join(directory, n)))
             for n in sorted(os.listdir(directory))
             if os.path.isfile(os.path.join(directory, n))]
    print(f'[serve] directory: {directory}')
    for name, size in files:
        print(f'[serve]   {name}  ({human(size)})')
    print(f'[serve] listening on http://{args.host}:{args.port}/')

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
