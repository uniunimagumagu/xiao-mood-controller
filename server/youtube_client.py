"""Candidate collection for the Mood Controller.

Two modes, decided by the presence of YOUTUBE_API_KEY:
- API mode (key set): YouTube Data API v3 search.list with per-category query
  rotation, order/publishedAfter filters, videos.list metadata, channel
  searches for favorite_channels. Quota-aware; on any error the existing
  cache / fixed fallback is used.
- Keyless mode (no key): official channel RSS feeds
  (youtube.com/feeds/videos.xml?channel_id=) for favorite_channels give the
  newest uploads with real titles; plus fixed fallback entries from the
  config. No third-party scraping.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
API_BASE = "https://www.googleapis.com/youtube/v3"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_PATH = DATA_DIR / "candidates_cache.json"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


@dataclass
class Candidate:
    video_id: str
    title: str
    channel: str
    category_id: str
    fetched_at: float = 0.0
    source: str = "search"  # search | channel | channel_feed | fallback

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def valid_video_id(vid) -> bool:
    return isinstance(vid, str) and bool(VIDEO_ID_RE.match(vid))


class YouTubeError(Exception):
    pass


class YouTubeClient:
    def __init__(self, api_key: str | None, cache_path: Path | None = None) -> None:
        self.api_key = api_key
        self.cache_path = cache_path or CACHE_PATH
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotation: dict[str, int] = {}
        self._last_refresh: dict[str, float] = {}
        self._cache = self._load_cache()

    # ---------- cache ----------

    def _load_cache(self) -> dict[str, list[Candidate]]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return {cid: [Candidate(**c) for c in items] for cid, items in raw.items()}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_cache(self) -> None:
        data = {cid: [asdict(c) for c in items] for cid, items in self._cache.items()}
        self.cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def cached(self, cid: str) -> list[Candidate]:
        return list(self._cache.get(cid, []))

    def _append_unique(self, cid: str, new: list[Candidate], cap: int) -> None:
        seen = {c.video_id for c in self._cache.get(cid, [])}
        items = [c for c in self._cache.get(cid, []) if c.category_id == cid]  # purge cross-category stale rows
        for c in new:
            if c.video_id not in seen and (not c.category_id or c.category_id == cid):
                c.category_id = cid
                items.append(c)
                seen.add(c.video_id)
        self._cache[cid] = items[-cap:]

    # ---------- fallback (offline mode) ----------

    @staticmethod
    def parse_fallback(category) -> list[Candidate]:
        out: list[Candidate] = []
        for line in category.fallback:
            parts = line.split("|")
            if len(parts) >= 2 and valid_video_id(parts[0]):
                out.append(
                    Candidate(
                        video_id=parts[0],
                        title=parts[1],
                        channel=parts[2] if len(parts) > 2 else "",
                        category_id=category.id,
                        source="fallback",
                    )
                )
        return out

    # ---------- HTTP ----------

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise YouTubeError("no API key configured")
        qs = urllib.parse.urlencode({**params, "key": self.api_key})
        req = urllib.request.Request(f"{API_BASE}/{path}?{qs}", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise YouTubeError(str(e)) from e

    # ---------- keyless: official channel RSS ----------

    def _channel_feed(self, channel_id: str, max_results: int = 5) -> list[Candidate]:
        req = urllib.request.Request(FEED_URL.format(channel_id), headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                root = ET.fromstring(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as e:
            raise YouTubeError(f"feed {channel_id[:12]}: {e}") from e
        t_el = root.find("a:title", ATOM_NS)
        chan = t_el.text.strip() if t_el is not None and t_el.text else channel_id
        out: list[Candidate] = []
        for entry in root.findall("a:entry", ATOM_NS)[:max_results]:
            v_el = entry.find("yt:videoId", ATOM_NS)
            vid = (v_el.text or "") if v_el is not None else ""
            if not valid_video_id(vid):
                continue
            title_el = entry.find("a:title", ATOM_NS)
            out.append(Candidate(
                video_id=vid,
                title=(title_el.text or "").strip() if title_el is not None else vid,
                channel=chan,
                category_id="", fetched_at=time.time(), source="channel_feed",
            ))
        return out

    # ---------- API mode ----------

    def _search_ids(self, query: str, max_results: int = 8, channel_id: str = "",
                    order: str = "", published_after: str = "") -> list[str]:
        params: dict = {"part": "id", "type": "video", "maxResults": max_results,
                        "regionCode": "JP", "relevanceLanguage": "ja"}
        if channel_id:
            params["channelId"] = channel_id
            params["order"] = order or "date"
        else:
            params["q"] = query
            if order:
                params["order"] = order
            if published_after:
                params["publishedAfter"] = published_after
        data = self._get("search", params)
        ids = []
        for item in data.get("items", []):
            vid = (item.get("id") or {}).get("videoId", "")
            if valid_video_id(vid):
                ids.append(vid)
        return ids

    def _video_meta(self, ids: list[str]) -> dict[str, tuple[str, str]]:
        if not ids:
            return {}
        data = self._get("videos", {"part": "snippet", "id": ",".join(ids), "maxResults": len(ids)})
        out = {}
        for item in data.get("items", []):
            sn = item.get("snippet", {})
            out[item.get("id", "")] = (sn.get("title", ""), sn.get("channelTitle", ""))
        return out

    # ---------- refresh (fills the category pool) ----------

    def refresh(self, category, force: bool = False, cap: int = 20, cooldown: int = 600) -> list[Candidate]:
        """Fill the category pool. Returns the current pool. Falls back on any failure."""
        cid = category.id
        now = time.time()
        if not force and now - self._last_refresh.get(cid, 0.0) < cooldown:
            return self.cached(cid)
        self._last_refresh[cid] = now

        if not self.api_key:
            # keyless mode: newest uploads from favorite channels + fixed fallback
            new: list[Candidate] = []
            for ch in category.favorite_channels[:3]:
                try:
                    for c in self._channel_feed(ch)[:5]:
                        c.category_id = cid
                        new.append(c)
                except YouTubeError:
                    continue   # offline/feed error: fall through to fallback
            if not new:
                new = self.parse_fallback(category)
            self._append_unique(cid, new, cap=cap)
            self._save_cache()
            return self.cached(cid)

        try:
            new = []
            idx = self._rotation.get(cid, 0) % len(category.queries)
            self._rotation[cid] = idx + 1
            order = getattr(category, "order", "") or ""
            after = getattr(category, "published_after_days", 0) or 0
            published_after = ""
            if after:
                published_after = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - after * 86400))
            ids = self._search_ids(category.queries[idx], order=order,
                                   published_after=published_after)
            for ch in category.favorite_channels[:3]:
                ids.extend(self._search_ids("", channel_id=ch, order=order)[:5])
            meta = self._video_meta(ids)
            for v in ids:
                title, chan = meta.get(v, ("", ""))
                new.append(Candidate(video_id=v, title=title or "(untitled)",
                                     channel=chan, category_id=cid, fetched_at=now,
                                     source="channel" if not chan else "search"))
        except YouTubeError:
            if not self.cached(cid):
                for c in self.parse_fallback(category):
                    self._append_unique(cid, [c], cap=cap)
        self._append_unique(cid, new, cap=cap)
        self._save_cache()
        return self.cached(cid)
