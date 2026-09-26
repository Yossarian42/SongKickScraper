"""Serve the concert browser locally and open it in the default browser.

Usage:
    python serve.py            # http://localhost:8000/site/
    python serve.py --port 8080
"""

import argparse
import functools
import http.server
import json
import os
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


FAVOURITES_PATH = ROOT / "favourites.json"
FAVOURITES_URL = "/api/favourites"


def read_favourites():
    try:
        data = json.loads(FAVOURITES_PATH.read_text(encoding="utf-8"))
        return [name for name in data if isinstance(name, str)]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def write_favourites(names):
    names = sorted({n.strip() for n in names if isinstance(n, str) and n.strip()}, key=str.casefold)
    tmp = FAVOURITES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(names, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FAVOURITES_PATH)  # atomic, so a crash never leaves a half-written file
    return names


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Always re-read files so a fresh scrape shows up on reload.

    Also serves GET/PUT /api/favourites, backed by favourites.json, which is kept
    separate from concerts.csv so favourites survive re-scrapes.
    """

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == FAVOURITES_URL:
            return self.send_json(read_favourites())
        return super().do_GET()

    def do_PUT(self):
        if self.path.split("?")[0] != FAVOURITES_URL:
            return self.send_error(404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            names = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(names, list):
                raise ValueError("expected a JSON list of artist names")
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, status=400)
        return self.send_json(write_favourites(names))

    def log_message(self, format, *args):
        pass  # keep the console quiet


def main():
    parser = argparse.ArgumentParser(description="Serve the concert browser locally.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    if not (ROOT / "concerts.csv").exists():
        sys.exit("concerts.csv not found - run songkick_scraper.py first.")

    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://localhost:{args.port}/site/"
    print(f"Serving at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
