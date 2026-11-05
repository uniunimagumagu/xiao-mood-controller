"""Thumbnail pipeline: fetch YouTube thumbs, convert to RGB565 for the device.

- mirror saved: data/thumbs/<vid>.jpg
- output: 150x84 RGB565 little-endian (device sprite-friendly) for the layout
- live streams / dead ids: no thumbnail -> placeholder kept on device
- LRU memory cache + disk cache; prefetcher warms upcoming candidates
"""
from __future__ import annotations

import io
import threading
from pathlib import Path

import urllib.request

from PIL import Image

from .youtube_client import valid_video_id

THUMB_DIR = Path(__file__).resolve().parent.parent / "data" / "thumbs"
THUMB_W, THUMB_H = 150, 84
_UA = {"User-Agent": "Mozilla/5.0"}

_mem: dict[str, bytes | None] = {}
_mem_lock = threading.Lock()


def _fetch_source(video_id: str) -> Image.Image | None:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / f"{video_id}.jpg"
    if dest.exists():
        try:
            return Image.open(dest).convert("RGB")
        except OSError:
            dest.unlink(missing_ok=True)
    for res in ("mqdefault", "hqdefault"):
        try:
            req = urllib.request.Request(
                f"https://i.ytimg.com/vi/{video_id}/{res}.jpg", headers=_UA)
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            if len(data) > 400:  # non-empty image
                dest.write_bytes(data)
                return Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            continue
    return None


def get_rgb565(video_id: str) -> bytes | None:
    """150x84 RGB565 bytes (little-endian) or None if not available."""
    if not valid_video_id(video_id):
        return None
    with _mem_lock:
        if video_id in _mem:
            return _mem[video_id]
    src = _fetch_source(video_id)
    if src is None:
        with _mem_lock:
            _mem[video_id] = None
        return None
    img = src.resize((THUMB_W, THUMB_H))
    px = img.tobytes()
    out = bytearray(THUMB_W * THUMB_H * 2)
    r5 = extract565(px)
    out[:len(r5)] = r5
    data = bytes(out)
    with _mem_lock:
        _mem[video_id] = data
        if len(_mem) > 60:
            for k in list(_mem)[:5]:
                if _mem.get(k) is not None:
                    _mem.pop(k, None)
    return data


def extract565(rgb: bytes) -> bytes:
    """RGB24 -> RGB565 little-endian."""
    n = len(rgb) // 3
    out = bytearray(n * 2)
    for i in range(n):
        r = rgb[i * 3]; g = rgb[i * 3 + 1]; b = rgb[i * 3 + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[i * 2] = v & 0xFF
        out[i * 2 + 1] = (v >> 8) & 0xFF
    return bytes(out)
