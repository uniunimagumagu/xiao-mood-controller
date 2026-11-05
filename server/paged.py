"""Serves the mood panel page on 127.0.0.1 only.

The YouTube embed player refuses file:// origins (error 153), so the panel
runs on a localhost HTTP origin instead. Nothing else is served.
"""
from __future__ import annotations

import os
import time
import http.server
import threading
from functools import partial
from pathlib import Path

PAGE_PORT = int(__import__("os").environ.get("MOOD_PAGE_PORT", "8642"))
PAGE_DIR = Path(__file__).resolve().parent.parent / "data"

_server = None
_lock = threading.Lock()


def start_page_server() -> str:
    global _server
    with _lock:
        if _server is None:
            handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(PAGE_DIR))
            import socket
            try:
                s_ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s_.bind(("127.0.0.1", PAGE_PORT))
                s_.close()
                httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PAGE_PORT), handler)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                _server = httpd
            except OSError:
                pass  # another daemon process already serves the page
    return f"http://127.0.0.1:{PAGE_PORT}/ytplayer.html"


if __name__ == "__main__":
    import sys
    url = start_page_server()
    print(f"mood page server: {url}", flush=True)
    #stay alive while the parent keeps us running
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
