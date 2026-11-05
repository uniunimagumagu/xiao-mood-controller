"""Configuration loading for XIAO Mood Controller."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = os.environ.get(
    "MOOD_CONFIG", str(Path(__file__).resolve().parent.parent / "config" / "categories.yaml")
)

REQUIRED_KEYS = {"id", "label", "queries"}


class ConfigError(Exception):
    pass


@dataclass
class Category:
    id: str
    label: str
    queries: list[str]
    favorite_channels: list[str] = field(default_factory=list)
    fallback: list[str] = field(default_factory=list)
    order: str = ""                 # search order (relevance|viewCount|date), API mode
    published_after_days: int = 0   # only recent videos (API mode)


@dataclass
class CachePolicy:
    min_candidates: int = 3
    max_candidates: int = 20
    refresh_cooldown_sec: int = 600


@dataclass
class AppConfig:
    categories: list[Category]
    cache: CachePolicy = field(default_factory=CachePolicy)

    def category_by_id(self, cid: str) -> Category | None:
        return next((c for c in self.categories if c.id == cid), None)


def _parse_category(raw: dict, index: int) -> Category:
    missing = REQUIRED_KEYS - set(raw)
    if missing:
        raise ConfigError(f"category[{index}]: missing keys {sorted(missing)}")
    if not isinstance(raw["queries"], list) or not raw["queries"]:
        raise ConfigError(f"category[{index}] ({raw['id']}): 'queries' must be a non-empty list")
    return Category(
        id=str(raw["id"]),
        label=str(raw["label"]),
        queries=[str(q) for q in raw["queries"]],
        favorite_channels=[str(c) for c in raw.get("favorite_channels", [])],
        fallback=[str(f) for f in raw.get("fallback", [])],
        order=str(raw.get("order", "")),
        published_after_days=int(raw.get("published_after_days", 0)),
    )


def load_config(path: str | None = None) -> AppConfig:
    path = path or DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cats_raw = raw.get("categories")
    if not isinstance(cats_raw, list) or not cats_raw:
        raise ConfigError(f"{path}: 'categories' must be a non-empty list")
    categories = [_parse_category(c, i) for i, c in enumerate(cats_raw)]
    ids = [c.id for c in categories]
    if len(set(ids)) != len(ids):
        raise ConfigError("duplicate category ids")
    cache_raw = raw.get("cache", {}) or {}
    cache = CachePolicy(
        min_candidates=int(cache_raw.get("min_candidates", 3)),
        max_candidates=int(cache_raw.get("max_candidates", 20)),
        refresh_cooldown_sec=int(cache_raw.get("refresh_cooldown_sec", 600)),
    )
    return AppConfig(categories=categories, cache=cache)
