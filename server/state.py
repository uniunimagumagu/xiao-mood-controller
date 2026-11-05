"""Core navigation state. Mirrors spec 4.x semantics.

- up/down   : change category only, per-category candidate position is retained
- left/right: move within the current category's candidates only
- navigation never triggers playback; play happens only on an explicit play action
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import AppConfig, Category
from .youtube_client import Candidate


@dataclass
class MoodState:
    app: AppConfig
    category_index: int = 0
    # per-category current candidate index (retained across category switches)
    positions: dict[str, int] = field(default_factory=dict)
    last_error: str = ""
    last_status: str = ""
    playing: Candidate | None = None
    override: Candidate | None = None   # recommendation-lane selection
    _candidates: dict[str, list[Candidate]] = field(default_factory=dict)

    # ---------- candidates ----------

    def set_candidates(self, cid: str, items: list[Candidate]) -> None:
        self._candidates[cid] = items
        pos = self.positions.get(cid, 0)
        if not items:
            self.positions[cid] = 0
        else:
            self.positions[cid] = min(pos, len(items) - 1)

    def candidates(self, cid: str | None = None) -> list[Candidate]:
        cid = cid if cid is not None else self.category.id
        return self._candidates.get(cid, [])

    def needs_refresh(self, cid: str) -> bool:
        return len(self._candidates.get(cid, [])) < self.app.cache.min_candidates

    # ---------- navigation ----------

    @property
    def category(self) -> Category:
        return self.app.categories[self.category_index]

    @property
    def position(self) -> int:
        return self.positions.get(self.category.id, 0)

    @property
    def current(self) -> Candidate | None:
        if self.override is not None:
            return self.override
        items = self.candidates()
        if not items:
            return None
        return items[min(self.position, len(items) - 1)]

    def next_category(self) -> None:
        self.category_index = (self.category_index + 1) % len(self.app.categories)

    def prev_category(self) -> None:
        self.category_index = (self.category_index - 1) % len(self.app.categories)

    def next_item(self) -> None:
        items = self.candidates()
        if not items:
            self.last_status = "No results"
            return
        self.positions[self.category.id] = (self.position + 1) % len(items)

    def prev_item(self) -> None:
        items = self.candidates()
        if not items:
            self.last_status = "No results"
            return
        self.positions[self.category.id] = (self.position - 1) % len(items)

    # ---------- event handling ( handset gestures / keyboard / HTTP ) ----------

    def handle(self, event: str) -> Candidate | None:
        """Apply a navigation/play event. Returns the candidate to play for 'TAP'."""
        event = event.strip().upper()
        self.last_status = ""
        if event == "UP":
            self.next_category()
        elif event == "DOWN":
            self.prev_category()
        elif event == "LEFT":
            self.prev_item()
        elif event == "RIGHT":
            self.next_item()
        elif event == "PICKUP":
            self.last_status = "Wake"
        elif event == "TAP":
            item = self.current
            if item is None:
                self.last_status = "No results"
            return item
        else:
            self.last_status = f"Unknown event: {event}"
        return None
