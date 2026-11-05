import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import AppConfig, CachePolicy, Category, load_config
from server.state import MoodState
from server.youtube_client import Candidate, YouTubeClient, valid_video_id

CONFIG = str(Path(__file__).resolve().parent.parent / "config" / "categories.yaml")


def make_app():
    return load_config(CONFIG)


def cand(cid, vid, title="t"):
    return Candidate(video_id=vid, title=title, channel="ch", category_id=cid)


class TestConfig:
    def test_load(self):
        app = make_app()
        assert len(app.categories) >= 5
        assert all(c.queries for c in app.categories)

    def test_duplicate_ids_rejected(self):
        from server.config import ConfigError
        import tempfile, textwrap
        bad = textwrap.dedent("""
            categories:
              - id: a
                label: A
                queries: ["q"]
              - id: a
                label: A2
                queries: ["q"]
        """)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(bad)
        with pytest.raises(ConfigError):
            load_config(f.name)

    def test_missing_keys_rejected(self):
        from server.config import ConfigError
        import tempfile, textwrap
        bad = "categories:\n  - id: a\n    label: A\n"
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(bad)
        with pytest.raises(ConfigError):
            load_config(f.name)


class TestStateNavigation:
    def test_vertical_changes_category_only(self):
        st = MoodState(app=make_app())
        for c in st.app.categories:
            st.set_candidates(c.id, [cand(c.id, f"vid{i:011d}") for i in range(3)])
        first_id = st.category.id
        st.handle("UP")
        assert st.category.id != first_id
        assert st.position == 0

    def test_horizontal_changes_item_only(self):
        st = MoodState(app=make_app())
        c = st.app.categories[0]
        st.set_candidates(c.id, [cand(c.id, f"vid{i:011d}") for i in range(3)])
        st.handle("RIGHT")
        st.handle("RIGHT")
        assert st.position == 2
        assert st.category.id == c.id

    def test_position_retained_across_categories(self):
        st = MoodState(app=make_app())
        a, b = st.app.categories[0], st.app.categories[1]
        st.set_candidates(a.id, [cand(a.id, f"a{i:011d}") for i in range(3)])
        st.set_candidates(b.id, [cand(b.id, f"b{i:011d}") for i in range(3)])
        st.handle("RIGHT"); st.handle("RIGHT")          # a -> index 2
        st.handle("UP")                                  # -> b
        st.handle("DOWN")                                # -> back to a
        assert st.category.id == a.id and st.position == 2

    def test_navigation_does_not_play(self):
        st = MoodState(app=make_app())
        c = st.app.categories[0]
        st.set_candidates(c.id, [cand(c.id, "vid000000001")])
        assert st.handle("RIGHT") is None
        assert st.handle("UP") is None
        assert st.playing is None

    def test_tap_returns_current(self):
        st = MoodState(app=make_app())
        c = st.app.categories[0]
        st.set_candidates(c.id, [cand(c.id, "vid000000001"), cand(c.id, "vid000000002")])
        st.handle("RIGHT")
        item = st.handle("TAP")
        assert item is not None and item.video_id == "vid000000002"

    def test_tap_empty_shows_no_results(self):
        st = MoodState(app=make_app())
        assert st.handle("TAP") is None
        assert st.last_status == "No results"

    def test_left_right_on_empty_is_noop_but_updown_works(self):
        st = MoodState(app=make_app())
        first = st.category.id
        st.handle("LEFT"); st.handle("RIGHT")
        assert st.category.id == first
        st.handle("DOWN")
        assert st.category.id != first

    def test_wraparound(self):
        st = MoodState(app=make_app())
        c = st.app.categories[0]
        st.set_candidates(c.id, [cand(c.id, "vid000000001"), cand(c.id, "vid000000002")])
        st.handle("LEFT")
        assert st.position == 1


class TestVideoIdValidation:
    def test_valid(self):
        assert valid_video_id("dQw4w9WgXcQ")

    def test_rejects(self):
        for bad in ["", "short", "toolongvideoid1", "bad id!!!!", None, "../../etc/passwd"]:
            assert not valid_video_id(bad)


class TestPlayValidation:
    def test_playable_ok(self, monkeypatch):
        from server import brave as B

        class R:
            status = 200
            def read(self): return b'{"title": "x"}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(B.urllib.request, "urlopen", lambda *a, **k: R())
        assert B.is_playable("dQw4w9WgXcQ") is True

    def test_playable_404(self, monkeypatch):
        import urllib.error
        from server import brave as B
        def boom(*a, **k):
            raise urllib.error.HTTPError(None, 404, "Not Found", None, None)
        monkeypatch.setattr(B.urllib.request, "urlopen", boom)
        assert B.is_playable("dead0000000") is False

    def test_playable_network_fail_assumes_ok(self, monkeypatch):
        import urllib.error
        from server import brave as B
        def boom(*a, **k):
            raise urllib.error.URLError("down")
        monkeypatch.setattr(B.urllib.request, "urlopen", boom)
        assert B.is_playable("dQw4w9WgXcQ") is True


class TestHandsetProtocol:
    def test_parse_line(self):
        from server.handset import parse_line
        assert parse_line("EVT:TAP") == "TAP"
        assert parse_line("EVT:LEFT\r") == "LEFT"
        assert parse_line("EVT:BOGUS") is None
        assert parse_line("DBG:pitch=1.2") is None
        assert parse_line("") is None


class TestYouTubeClient:
    def test_fallback_offline(self, tmp_path):
        yt = YouTubeClient(api_key=None, cache_path=tmp_path / "c.json")
        app = make_app()
        cat = app.categories[0]
        items = yt.refresh(cat)
        assert len(items) >= 1
        assert all(valid_video_id(i.video_id) for i in items)
        assert all(i.source in ("fallback", "channel_feed") for i in items)

    def test_cache_persisted(self, tmp_path):
        p = tmp_path / "c.json"
        yt = YouTubeClient(api_key=None, cache_path=p)
        yt.refresh(make_app().categories[0])
        assert p.exists()
        yt2 = YouTubeClient(api_key=None, cache_path=p)
        assert len(yt2.cached(make_app().categories[0].id)) >= 1

    def test_dedupe(self, tmp_path):
        from server.youtube_client import YouTubeClient as Y
        yt = Y(api_key=None, cache_path=tmp_path / "c.json")
        yt._append_unique("x", [cand("x", "vid000000001")], cap=10)
        yt._append_unique("x", [cand("x", "vid000000001"), cand("x", "vid000000002")], cap=10)
        assert len(yt.cached("x")) == 2
