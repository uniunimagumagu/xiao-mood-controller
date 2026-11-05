"""FastAPI HTTP API (spec 7.1). Optional component: the serial handset daemon is
the primary interface; this API covers remote / management use.

Run:  uvicorn server.http_api:app --host 127.0.0.1 --port 8765
Auth: set MOOD_TOKEN env to require 'X-Mood-Token' on every request.
By default binds are localhost-only; do not expose to the LAN without a token.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException

from .brave import PlayError, open_in_brave
from .config import load_config
from .state import MoodState
from .youtube_client import Candidate, YouTubeClient, valid_video_id

app = FastAPI(title="XIAO Mood Controller", version="1.0")

_config = load_config()
_yt = YouTubeClient(os.environ.get("YOUTUBE_API_KEY"))
_state = MoodState(app=_config)
for _cat in _config.categories:
    if not _state.candidates(_cat.id):
        _state.set_candidates(_cat.id, _yt.refresh(
            _cat, cap=_config.cache.max_candidates,
            cooldown=_config.cache.refresh_cooldown_sec))
_TOKEN = os.environ.get("MOOD_TOKEN", "")


def _auth(token: str | None) -> None:
    if _TOKEN and token != _TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


def _cid_or_404(cid: str):
    cat = _config.category_by_id(cid)
    if cat is None:
        raise HTTPException(status_code=404, detail="unknown category id")
    return cat


@app.get("/health")
def health(token: str | None = Header(None)) -> dict:
    _auth(token)
    return {"status": "ok", "categories": len(_config.categories)}


@app.get("/categories")
def categories(token: str | None = Header(None)) -> dict:
    _auth(token)
    return {"categories": [{"id": c.id, "label": c.label} for c in _config.categories]}


@app.get("/categories/{cid}/candidates")
def candidates(cid: str, token: str | None = Header(None)) -> dict:
    _auth(token)
    _cid_or_404(cid)
    items = _state.candidates(cid)
    return {"category_id": cid, "count": len(items),
            "items": [i.__dict__ for i in items]}


@app.post("/play")
def play(body: dict, token: str | None = Header(None)) -> dict:
    _auth(token)
    vid = body.get("video_id", "")
    if not valid_video_id(vid):
        raise HTTPException(status_code=400, detail="video_id must be an 11-char YouTube id")
    try:
        url = open_in_brave(vid)
    except PlayError as e:
        raise HTTPException(status_code=502, detail=str(e))
    item = next((c for c in _state.candidates() if c.video_id == vid), None)
    if item:
        _state.playing = item
    return {"status": "ok", "url": url}


@app.post("/refresh/{cid}")
def refresh(cid: str, token: str | None = Header(None)) -> dict:
    _auth(token)
    cat = _cid_or_404(cid)
    items = _yt.refresh(cat, force=True, cap=_config.cache.max_candidates)
    _state.set_candidates(cat.id, items)
    return {"category_id": cid, "count": len(items)}
