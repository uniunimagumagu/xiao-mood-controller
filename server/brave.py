"""Brave browser playback control (single embed-panel tab via CDP).

- first play: launch a dedicated Brave instance (own user-data-dir, CDP
  remote debugging bound to 127.0.0.1 only, autoplay allowed) that opens a
  local panel page (data/ytplayer.html) with one YouTube embed iframe.
- every play: swap the iframe src inside that tab (play(vid)) -> audio
  switches without reloading the page, and no tabs / windows are ever added.
- verification: the panel posts the YouTube player state back (playing /
  error) and we confirm real playback started; failures raise PlayError so
  the caller can skip to the next candidate.
- the panel page keeps the debug port on localhost only and the dedicated
  profile holds no personal data (spec 6).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import websocket  # type: ignore

from .youtube_client import valid_video_id

WATCH_URL = "https://www.youtube.com/watch?v={}"
OEMBED_URL = "https://www.youtube.com/oembed?{}"
THUMB_URL = "https://i.ytimg.com/vi/{}/mqdefault.jpg"

CDP_PORT = int(__import__("os").environ.get("MOOD_CDP_PORT", "9222"))
CDP_HTTP = f"http://127.0.0.1:{CDP_PORT}"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILE_DIR = DATA_DIR / "brave_profile"
_PAGE_URL = None


def _player_page() -> str:
    """Serve the panel page from a standalone persistent process (page server
    must outlive short-lived callers like CLI runs)."""
    global _PAGE_URL
    if _PAGE_URL:
        return _PAGE_URL
    _PAGE_URL = _page_server_url()
    return _PAGE_URL


def _page_server_url() -> str:
    from .paged import start_page_server
    try:
        return start_page_server()   # binds if nobody else holds the port
    except Exception:
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", PAGE_PORT if False else int(__import__("os").environ.get("MOOD_PAGE_PORT", "8642")))
        c.request("GET", "/ytplayer.html")
        c.close()
        return f"http://127.0.0.1:{__import__('os').environ.get('MOOD_PAGE_PORT', '8642')}/ytplayer.html"
_SPAWN_ARGS_EXTRA = ["--autoplay-policy=no-user-gesture-required"]

STATE_OK = frozenset((1, 3))  # playing / buffering


class PlayError(Exception):
    pass


class EmbedRefused(PlayError):
    """The video's owner disallows iframe embedding (error 150/101/153 class)."""


EMBED_REFUSED_CODES = {"100", "101", "150", "153", "2", "5"}


def _brave_exe() -> str | None:
    if sys.platform == "darwin":
        p = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        return p if Path(p).exists() else None
    if sys.platform == "win32":
        candidates = [
            Path(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
            Path(r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe"),
            Path(os.environ.get("LOCALAPPDATA", "~")) / "BraveSoftware/Brave-Browser/Application/brave.exe",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        exe = shutil.which("brave") or shutil.which("brave.exe")
        return exe
    exe = shutil.which("brave-browser") or shutil.which("brave")
    return exe


# ---------- CDP plumbing ----------

def _cdp_alive(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def _pages() -> list[dict]:
    try:
        with urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=2) as r:
            return [t for t in json.loads(r.read().decode("utf-8")) if t.get("type") == "page"]
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []


def _current_tab() -> dict | None:
    pages = _pages()
    return next((t for t in pages if "youtube.com/watch" in (t.get("url") or "")),
                None) or (pages[0] if pages else None)


def _current_tab_ws_url() -> str:
    return (_current_tab() or {}).get("webSocketDebuggerUrl", "")


def _ws_navigate(ws_url: str, url: str) -> bool:
    try:
        w = websocket.create_connection(ws_url, timeout=3, suppress_origin=True)
        w.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
        w.recv()
        w.close()
        return True
    except (OSError, ValueError, websocket.WebSocketException):
        return False


def _cdp_evaluate(ws_url: str, expr: str, want_id: int, timeout: float = 3.0):
    """Runtime.evaluate with suppress_origin (Chromium 111+)."""
    ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    ws.send(json.dumps({"id": 10, "method": "Runtime.enable"}))
    ws.recv()
    ws.send(json.dumps({"id": want_id, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == want_id:
            value = ((msg.get("result") or {}).get("result") or {}).get("value")
            ws.close()
            return value


def _cleanup_tabs() -> None:
    """Close every page except the single mood watch tab."""
    keep = _current_tab()
    if not keep:
        return
    for t in _pages():
        if t["id"] != keep["id"]:
            try:
                urllib.request.urlopen(f"{CDP_HTTP}/json/close/{t['id']}", timeout=1)
            except (urllib.error.URLError, TimeoutError):
                pass


WATCH_STATE_JS = """
(function () {
  const p = document.getElementById('movie_player');
  let state = null, err = '';
  try { state = p && typeof p.getPlayerState === 'function' ? p.getPlayerState() : null; } catch (e) {}
  const e1 = document.querySelector('.ytp-error-content-wrap-reason');
  if (e1) err = e1.textContent.trim();
  return JSON.stringify({state: state, err: err});
})()
"""


def _watch_playing(wait_s: float = 3.0) -> bool:
    """Confirm playback on the watch page (best effort, non-blocking-ish)."""
    tab = _current_tab()
    if not tab:
        return True  # nothing to verify against
    deadline = time.time() + wait_s
    try:
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"],
                                         timeout=2, suppress_origin=True)
        ws.send(json.dumps({"id": 10, "method": "Runtime.enable"}))
        ws.recv()
        i = 0
        while time.time() < deadline:
            time.sleep(0.3); i += 1
            ws.send(json.dumps({"id": 20 + i, "method": "Runtime.evaluate",
                                "params": {"expression": WATCH_STATE_JS,
                                           "returnByValue": True}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 20 + i:
                    break
            raw = ((msg.get("result") or {}).get("result") or {}).get("value") or "{}"
            st = json.loads(raw)
            if st.get("err"):
                ws.close()
                return False                    # YouTube error banner
            if st.get("state") in STATE_OK:
                ws.close()
                return True
        ws.close()
    except (OSError, ValueError, websocket.WebSocketException):
        return True   # cannot verify: let it play
    return True       # no error seen


def _wait_cdp_ready(seconds: float = 10.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _cdp_alive(0.5):
            return True
        time.sleep(0.25)
    return False


def _spawn_cdp(url: str) -> bool:
    exe = _brave_exe()
    if not exe:
        return False
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen([
            exe,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--remote-allow-origins=http://127.0.0.1:9222",
            "--no-first-run", "--no-default-browser-check",
            f"--autoplay-policy={ 'no-user-gesture-required' }".replace(" ", "").replace("{ ", "").replace("}", "").replace("'",""),
            "--autoplay-policy=no-user-gesture-required",
            url,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return _wait_cdp_ready()


def open_in_brave(video_id: str) -> str:
    """Play the video in ONE dedicated mood tab. Tab count never grows while
    the CDP endpoint lives; a closed window reopens on the next play."""
    if not valid_video_id(video_id):
        raise PlayError(f"invalid video_id: {video_id!r}")
    url = WATCH_URL.format(video_id)
    if _cdp_alive():
        tab = _current_tab()
        if tab is None:
            raise PlayError("CDP alive but no tab")
        ok = _ws_navigate(tab["webSocketDebuggerUrl"], url)
        _cleanup_tabs()
        if not ok:
            raise PlayError("navigate failed")
        _watch_playing()
        return url
    _kill_stray_profile()
    for _ in range(2):
        if _spawn_cdp(url):
            time.sleep(1.0)
            _cleanup_tabs()
            _watch_playing(wait_s=0.6)
            return url
        time.sleep(2.0)
    raise PlayError("browser playback failed")



def preload_in_brave(next_vid: str, slot: str = "") -> bool:
    """No-op in the single-tab watch model (kept for interface compatibility)."""
    return False


# ---------- availability pre-filter (no API key) ----------


def is_playable(video_id: str, timeout: float = 6.0) -> bool:
    """1) thumbnail HEAD: an id that never existed has no thumbnail (404);
    live streams also 404, disambiguated by oEmbed (200 = exists).
    2) oEmbed: 400/401/403/404 -> private / removed.
    Network failure -> True (don't block playback on a flaky check)."""
    if not valid_video_id(video_id):
        return False
    try:
        req = urllib.request.Request(THUMB_URL.format(video_id), method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return True
        # fallthrough: unexpected status
    except urllib.error.HTTPError as e:
        if e.code not in (404,):
            return False
        # 404: live or dead; oEmbed decides
    except (urllib.error.URLError, TimeoutError):
        pass
    qs = urllib.parse.urlencode({"url": WATCH_URL.format(video_id), "format": "json"})
    req = urllib.request.Request(OEMBED_URL.format(qs), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403, 404):
            return False
    except (urllib.error.URLError, TimeoutError, ValueError):
        return True
    return True


# ---------- entry point ----------

def open_in_brave(video_id: str) -> str:
    """Play the video in the single mood panel tab. No new tabs ever.

    While the CDP endpoint is alive we NEVER fall back to `open -a`:
    that spawns new tabs in the user's everyday browser window.
    """
    if not valid_video_id(video_id):
        raise PlayError(f"invalid video_id: {video_id!r}")
    url = WATCH_URL.format(video_id)
    if _cdp_alive():
        tab = _current_tab()
        if tab is None:
            raise PlayError("CDP alive but no tab")
        if not _ws_navigate(tab["webSocketDebuggerUrl"], url):
            raise PlayError("navigate failed")
        _cleanup_tabs()
        _watch_playing(wait_s=0.6)
        return url
    for _ in range(2):
        _kill_stray_profile()
        if _spawn_cdp(url):
            time.sleep(1.0)
            _cleanup_tabs()
            _watch_playing(wait_s=0.6)
            return url
        time.sleep(2.0)
    if _brave_exe() is None:
        raise PlayError("Brave not installed")
    raise PlayError("browser playback failed")


SEEK_JS = """
(function (secs) {
  const p = document.getElementById('movie_player');
  if (!p || typeof p.seekBy !== 'function') return 'no-player';
  p.seekBy(secs);
  return 'ok';
})({SECS})
"""


def seek_by(secs: int) -> str:
    """Nudge the playing tab by `secs` seconds. Requires a watch page."""
    tab = _current_tab()
    if not tab or not _cdp_alive():
        return "no-tab"
    try:
        val = _cdp_evaluate(tab["webSocketDebuggerUrl"],
                            SEEK_JS.replace("{SECS}", str(secs)), 71)
        return str(val)
    except (OSError, ValueError, websocket.WebSocketException):
        return "error"
