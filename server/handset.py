"""Handset daemon: reads gesture events from the XIAO over USB serial,
updates MoodState, renders a console UI, and plays the selection in Brave.

Serial protocol (XIAO -> PC), one line per event:
    EVT:LEFT|RIGHT|UP|DOWN|TAP|PICKUP
    DBG:<anything>   (diagnostic output, logged not acted on)
PC -> XIAO:
    ACK:OK / ACK:ERR / STAT:PLAY_OK / STAT:PLAY_ERR / STAT:NORESULT
Keyboard fallback (device-free testing): arrows / space / q
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time
from pathlib import Path

import threading

import serial  # type: ignore

from .env_loader import load_env

from .brave import PlayError, is_playable, open_in_brave
from .config import load_config
from .state import MoodState
from .youtube_client import YouTubeClient

EVENT_RE = re.compile(r"^EVT:(LEFT|RIGHT|UP|DOWN|TAP|PICKUP)\s*$")
KEYMAP = {
    "\x1b[A": "UP", "\x1b[B": "DOWN", "\x1b[C": "RIGHT", "\x1b[D": "LEFT",
    " ": "TAP", "j": "DOWN", "k": "UP", "h": "LEFT", "l": "RIGHT", "\r": "TAP",
}

def log(msg: str) -> None:
    """Verbose always-on logging: file + stderr."""
    ts = time.strftime("%H:%M:%S")
    line = f"{ts} {msg}"
    print(line, file=sys.stderr, flush=True)
    logf = getattr(run, "logf", None)
    if logf:
        logf.write(line + "\n")
        logf.flush()


def auto_detect_port() -> str | None:
    try:
        from serial.tools import list_ports  # type: ignore

        ports = list(list_ports.comports())  # type: ignore
    except Exception:
        ports = []
    # prefer ports whose description mentions the handset family
    for p in ports:
        desc = (p.description or "").lower()
        if any(k in desc for k in ("xiao", "nrf52", "nrf52840", "seeed", "usb modem", "usb serial")):
            return p.device
    # fall back to any CDC-ish port; on unix use the device globs too
    if ports:
        return ports[0].device
    patterns = ["/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/cu.wchusb*"]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    return None


class ConsoleUI:
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def render(self, state: MoodState) -> None:
        if self.quiet:
            return
        item = state.current
        n = len(state.candidates())
        lines = [
            "\x1b[2J\x1b[H\x1b[1m=== XIAO Mood Controller ===\x1b[0m",
            f"Category : {state.category.label} ({state.category_index + 1}/{len(state.app.categories)})",
        ]
        if item:
            lines.append(f"Candidate: [{state.position + 1}/{n}] {item.title[:70]}")
            lines.append(f"Channel  : {item.channel[:70]}")
            lines.append(f"URL      : {item.url}  (source: {item.source})")
        else:
            lines.append("Candidate: No results")
        if state.playing:
            lines.append(f"Playing  : {state.playing.title[:70]}")
        if state.last_status:
            lines.append(f"Status   : {state.last_status}")
        if state.last_error:
            lines.append(f"\x1b[31mError    : {state.last_error}\x1b[0m")
        lines.append("gestures: tilt L/R=item, tilt F/B=category, tap=play, pickup=wake")
        print("\n".join(lines))


class Handset:
    def __init__(self, state: MoodState, yt: YouTubeClient, ui: ConsoleUI,
                 writer=None) -> None:
        self.state = state
        self.yt = yt
        self.ui = ui
        self.writer = writer  # serial writer for ACK/STAT lines (optional)
        self.playable_cache: dict[str, bool] = {}
        self.play_lock = threading.Lock()
        self.rec_lane: dict[str, list] = getattr(self, "rec_lane", {})
        self.rec_lane_state: dict[str, str] = getattr(self, "rec_lane_state", {})
        self.rec_pos: dict[str, int] = getattr(self, "rec_pos", {})
        self.anchor = getattr(self, "anchor", "")
        self._channel_ids: dict[str, str] = getattr(self, "_channel_ids", {})
        self._pending = None
        self._pending_cv = threading.Condition()
        self._worker_started = False
        self.mode = "music"
        self._gpt_thread = None

    def _send(self, line: str) -> None:
        if self.writer:
            try:
                self.writer.write((line + "\n").encode("utf-8"))
                self.writer.flush()
            except OSError:
                pass

    @staticmethod
    def _clean(s: str, n: int = 60) -> str:
        return " ".join(s.split())[:n] or "-"

    def send_ui(self) -> None:
        """Push current state to the device display (tab-separated text +
        optional binary RGB565 thumbnail frame 0xFE<w><h><pixels>)."""
        st = self.state
        n = len(st.app.categories)
        self._send(f"UI:CAT\t{self._clean(st.category.label, 20)}\t{st.category_index + 1}/{n}")

        # NOW = what is actually streaming (truth); falls back to selection
        now = st.playing or st.current
        if now:
            pos = st.position + 1 if st.playing is None else self._posof(st, st.playing)
            self._send(f"UI:NOW\t{self._clean(now.title)}\t{self._clean(now.channel, 30)}\t{pos}/{len(st.candidates(st.category.id)) if now is st.current and st.playing is None else max(0, self._posof(st, st.playing)) if st.playing else 0}")
        else:
            self._send("UI:NOW\t-No results-\t\t0/0")

        # PREP = 4 pre-rendered destinations so the device flips screens locally:
        #   pool next / pool prev / neighbor category up / neighbor category down
        pool = st.candidates(st.category.id)
        pos = st.position
        npc = len(pool)
        if npc > 0:
            nxt = pool[(pos + 1) % npc]
            prv = pool[(pos - 1 + npc) % npc]
            if not st.override:
                self._send(f"PREP:ITEM\t{pos + 2}/{npc}\t{self._clean(nxt.title)}\t{self._clean(nxt.channel, 30)}")
                self._send(f"PREP:PREV\t{(pos - 1 + npc) % npc + 1}/{npc}\t{self._clean(prv.title)}\t{self._clean(prv.channel, 30)}")
        cats_n = len(st.app.categories)
        if cats_n > 1:
            for tag, idx in (("UPCAT", (st.category_index + 1) % cats_n),
                             ("DOWNCAT", (st.category_index - 1 + cats_n) % cats_n)):
                cat = st.app.categories[idx]
                items = st.candidates(cat.id)
                first = items[0] if items else None
                if first:
                    self._send(f"PREP:{tag}\t{cat.label}\t{idx + 1}/{cats_n}\t{self._clean(first.title)}\t{self._clean(first.channel, 30)}\t1/{len(items)}")
        self._send(f"UI:STAT\t{self._clean(st.last_status or st.last_error or 'Ready', 30)}")
        if st.playing:
            self._send(f"UI:PLAYED\t{st.playing.video_id}")

    @staticmethod
    def _posof(st, item) -> int:
        pool = st.candidates(st.category.id)
        for i, c in enumerate(pool):
            if c.video_id == item.video_id:
                return i + 1
        return 0

        log(f"UI pushed: cat={st.category.id} pos={st.position + 1}/{len(st.candidates(st.category.id))} "
            f"status={st.last_status!r} playing={(st.playing.title[:20] if st.playing else None)!r}")


    def prime(self) -> None:
        """Screen first: load cache/fallback only (no network in the boot path).
        Feeds / API search run in the background and push the UI when done."""
        for cat in self.state.app.categories:
            if not self.state.candidates(cat.id):
                items = self.yt.cached(cat.id) or self.yt.parse_fallback(cat)
                self.state.set_candidates(cat.id, items)
        self.state.last_status = "Ready"
        self.ui.render(self.state)
        self.send_ui()
        def roller():
            for cat in self.state.app.categories:
                try:
                    items = self.yt.refresh(cat, cap=self.state.app.cache.max_candidates,
                                            cooldown=self.state.app.cache.refresh_cooldown_sec)
                    self.state.set_candidates(cat.id, items)
                except Exception as e:
                    log(f"roller {cat.id}: {e}")
            self.ui.render(self.state); self.send_ui()
            log("roller done: pools fresh")
        threading.Thread(target=roller, daemon=True).start()
        threading.Thread(target=self._build_channel_id_map, daemon=True).start()
        # background pre-validation of candidates (oEmbed, no API key) so that
        # later swipes can play instantly without a network wait
        def roller():
            for cat in self.state.app.categories:
                for item in self.state.candidates(cat.id):
                    if item.video_id not in self.playable_cache:
                        self.playable_cache[item.video_id] = is_playable(item.video_id)
                        time.sleep(0.25)  # be gentle on quota-free endpoint
        threading.Thread(target=roller, daemon=True).start()

    def _play_flow(self, max_attempts: int = 4) -> None:
        """Every gesture ends here: play the current candidate in Brave,
        skipping candidates known to be unavailable."""
        st = self.state
        tried: list[str] = []
        for _ in range(max_attempts):
            item = st.current
            if item is None:
                st.last_status = "No results"
                self._send("STAT:NORESULT")
                return
            if item.video_id in tried:
                st.next_item()
                continue
            tried.append(item.video_id)
            known = self.playable_cache.get(item.video_id)
            if known is False:
                st.last_status = f"skip: {item.title[:18]}"
                st.next_item()
                self.ui.render(st)
                self.send_ui()
                continue
            try:
                url = open_in_brave(item.video_id)
                st.playing = item
                st.last_status = "Play OK"
                self.anchor = item.channel
                self._send("STAT:PLAY_OK")
                log(f"PLAY: opened {url}")
                self.ui.render(st)
                self.send_ui()
                return
            except PlayError as e:
                log(f"PLAY: failed on {item.video_id}: {e}")
                self.playable_cache[item.video_id] = False
                st.last_error = ""
                st.last_status = f"skip: {str(e)[:14]}"
                st.next_item()
                self.ui.render(st)
                self.send_ui()
                continue
        st.last_status = "no playable candidate"
        self._send("STAT:PLAY_ERR")
        log("PLAY: gave up after trying all candidates")

    def dispatch(self, event: str) -> None:
        t0 = time.time()
        log(f"EVT received: {event} (cat={self.state.category.id} pos={self.state.position})")
        if event in ("SEEKFWD", "SEEKBACK"):
            if self.mode != "music":
                return   # no seeking in chat mode
            secs = 60 if event == "SEEKFWD" else -60
            from server.brave import seek_by
            res = seek_by(secs)
            log(f"SEEK {secs:+d}s -> {res}")
            self.state.last_status = f"{secs:+d}秒シーク {res}"
            self.ui.render(self.state); self.send_ui()
            return
        if event == "CHAT":
            self.toggle_chat()
            return
        to_play = self.state.handle(event)
        self.state.last_error = ""

        # lanes: LEFT = our candidate pool; RIGHT = recommendation lane
        # (newest uploads of the currently playing channel, i.e. YouTube's flow)
        if event == "RIGHT" and self.rec_lane.get(self.anchor):
            item = None
            lane = self.rec_lane.get(self.anchor)
            if lane:
                pos = (self.rec_pos.get(self.anchor, -1) + 1) % len(lane)
                self.rec_pos[self.anchor] = pos
                item = lane[pos]
            if item is not None:
                self.state.override = item
                self.state.last_status = "Loading"
                self.ui.render(self.state)
                self.send_ui()
                log(f"UI pushed immediately (dt={time.time()-t0:.3f}s) [{item.video_id}]")
                threading.Thread(target=lambda: (self.ui.render(self.state),
                                                 self._play_flow()),
                                 daemon=True).start()
                return
        if event in ("LEFT", "UP", "DOWN"):
            self.state.override = None
        if event != "TAP":
            cat = self.state.category
            if self.state.needs_refresh(cat.id):
                def _topup(cat=cat):
                    try:
                        items = self.yt.refresh(cat, cap=self.state.app.cache.max_candidates,
                                                cooldown=self.state.app.cache.refresh_cooldown_sec)
                        self.state.set_candidates(cat.id, items)
                    except Exception as e:
                        log(f"topup {cat.id}: {e}")
                threading.Thread(target=_topup, daemon=True).start()
        self.state.last_status = "Loading"
        self.ui.render(self.state)
        self.send_ui()
        log(f"UI pushed immediately (dt={time.time()-t0:.3f}s) -> playback in background")

        # single worker: superseded gestures are coalesced; the newest wins
        with self._pending_cv:
            self._pending = event
            self._pending_cv.notify()
            if not self._worker_started:
                self._worker_started = True
                threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        """Serialized player worker; consumes the LATEST pending gesture only."""
        while True:
            with self._pending_cv:
                while self._pending is None:
                    self._pending_cv.wait()
                self._pending = None
                gen = None  # events coalesced above
            try:
                self.ui.render(self.state)
                self._play_flow()
            except Exception as e:
                import traceback
                log(f"WORKER error (daemon survives): {e!r}")
                log(traceback.format_exc(limit=4))
            threading.Thread(target=self._prefetch, daemon=True).start()

    def toggle_chat(self) -> None:
        """Device long-press toggles ChatGPT mode; PC drives it from the terminal."""
        if self.mode == "music":
            self.mode = "chat"
            self.state.last_status = "ChatGPTモード"
            self.ui.render(self.state); self.send_ui()
            log("MODE: chat (answer via tmux stdin)")
            if self._gpt_thread is None:
                self._gpt_thread = threading.Thread(target=self._gpt_repl, daemon=True)
                self._gpt_thread.start()
        else:
            self.mode = "music"
            self.state.last_status = "Ready"
            self._send("UI:STAT\tMusic mode")
            self.ui.render(self.state); self.send_ui()
            log("MODE: music")

    def _gpt_repl(self) -> None:
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            self._send("CHAT:Q\tOPENAI_API_KEY 未設定")
            self._send("CHAT:A\tPC側で .env にキーを設定してから再起動")
            log("chat: no OPENAI_API_KEY")
            return
        log("chat REPL ready (type questions in the tmux session)")
        while self.mode == "chat":
            try:
                sys.stdout.write("mood-chat> "); sys.stdout.flush()
                line = sys.stdin.readline()
            except (OSError, ValueError):
                return
            if not line:
                time.sleep(0.5); continue
            q = line.strip()
            if not q: continue
            self._send(f"CHAT:Q\t{self._clean(q, 120)}")
            answers = self._openai(api_key, q)
            for chunk in answers:
                self._send(f"CHAT:A\t{self._clean(chunk, 400)}")
                time.sleep(0.05)
            self.state.last_status = "回答済み"

    def _openai(self, api_key: str, question: str):
        import urllib.request as ur, json as js
        body = js.dumps({
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "あなたは作業BGMを聴く人の会話相手です。簡潔に日本語で答えてください。"},
                {"role": "user", "content": question},
            ],
            "temperature": 0.7,
        }).encode("utf-8")
        req = ur.Request("https://api.openai.com/v1/chat/completions",
                         data=body, method="POST",
                         headers={"Content-Type": "application/json",
                                   "Authorization": f"Bearer {api_key}"})
        try:
            with ur.urlopen(req, timeout=40) as r:
                data = js.loads(r.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            lines = content.split("\n")
            for i in range(0, len(lines), 3):
                yield "\n".join(lines[i:i+3])
        except Exception as e:
            yield f"API Error: {e}"

    def _prefetch(self) -> None:
        """After a successful play: populate the recommendation lane for the
        current playing channel and pre-cue its next track (instant switches)."""
        playing = self.state.playing
        if not playing:
            return
        chan_key = playing.channel
        with self.play_lock:
            lane = self.rec_lane.get(chan_key)
        if lane is None:
            self.rec_lane_state[chan_key] = "loading"
            try:
                cid = self._channel_id_of(playing)
                if not cid:
                    self.rec_lane_state[chan_key] = "empty"
                    return
                feed = self.yt._channel_feed(cid)[:10]
                items = [c for c in feed if c.video_id != playing.video_id]
                with self.play_lock:
                    self.rec_lane[chan_key] = items
                    self.rec_lane_state[chan_key] = "ready" if items else "empty"
                log(f"REC lane ready: {chan_key[:18]} items={len(items)}")
                self._prefetch_cue()
            except Exception as e:
                log(f"REC lane load failed: {e}")
                self.rec_lane_state[chan_key] = "empty"
        else:
            self._prefetch_cue()

    def _channel_id_of(self, item) -> str:
        # favorite_channels labels are matched by feed title -> id lookup cache
        return self._channel_ids.get(item.channel.lower()[:20], "")

    def _prefetch_cue(self) -> None:
        """Cue the next of BOTH lanes so swipes never wait:
        LEFT lane next -> hidden slot B, RIGHT lane next -> hidden slot C."""
        try:
            from server.brave import preload_in_brave
            st = self.state
            pool = st.candidates()
            pool_next = pool[(st.position + 1) % len(pool)] if pool else None
            rec_lane = self.rec_lane.get(self.anchor, [])
            rec_next = rec_lane[self.rec_pos.get(self.anchor, -1) % len(rec_lane)] if rec_lane else None
            if pool_next:
                preload_in_brave(pool_next.video_id, slot="B")
                log(f"PRE-CUE left lane: {pool_next.video_id}")
            if rec_next:
                preload_in_brave(rec_next.video_id, slot="C")
                log(f"PRE-CUE right lane: {rec_next.video_id}")
        except Exception as e:
            log(f"preload skipped: {e}")

    def _build_channel_id_map(self) -> None:
        ids = {}
        for cat in self.state.app.categories:
            for ch in cat.favorite_channels:
                try:
                    feed = self.yt._channel_feed(ch, max_results=1)
                    if feed:
                        ids[feed[0].channel.lower()[:20]] = ch
                except Exception:
                    pass
        self._channel_ids = ids
        log(f"channel map: {ids}")


def parse_line(line: str) -> str | None:
    m = EVENT_RE.match(line.strip())
    return m.group(1) if m else None


def run(port: str | None, config_path: str | None, quiet: bool, no_serial: bool) -> int:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    run.logf = open(data_dir / "mood_daemon.log", "a", encoding="utf-8")
    env_loaded = load_env()
    log(f"=== daemon start (pid={os.getpid()}) env={sorted(env_loaded)} ===")
    app = load_config(config_path)
    yt = YouTubeClient(__import__("os").environ.get("YOUTUBE_API_KEY"))
    state = MoodState(app=app)
    ui = ConsoleUI(quiet=quiet)
    hs = Handset(state, yt, ui)
    hs.prime()
    ui.render(state)

    log("STAGE: pre-serial")
    ser = None
    if not no_serial:
        port = port or auto_detect_port()
        if not port:
            print("No serial device found. Use --port or --no-serial (keyboard mode).",
                  file=sys.stderr)
            return 2
        try:
            log(f"STAGE: serial open {port}")
            ser = serial.Serial(port, 115200, timeout=0.1)
            hs.writer = ser
            print(f"connected: {port}")
            time.sleep(2)  # let the device finish booting before first UI push
            hs.send_ui()
        except serial.SerialException as e:
            print(f"serial open failed ({port}): {e}", file=sys.stderr)
            return 2

    log("STAGE: entering serial loop")
    if ser is None:
        return _keyboard_loop(hs)
    try:
        while True:
            raw = ser.readline().decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            if raw.startswith("DBG:"):
                log(f"[xiao] {raw[4:]}")
                continue
            ev = parse_line(raw)
            if ev:
                try:
                    hs.dispatch(ev)
                except Exception as e:
                    import traceback
                    log(f"DISPATCH ERROR (daemon survives): {e!r}")
                    log(traceback.format_exc(limit=4))
            else:
                log(f"[xiao-unparsed] {raw!r}")
    except KeyboardInterrupt:
        return 0
    finally:
        ser.close()


def _keyboard_loop_windows(hs: Handset) -> int:
    """Windows keyboard fallback (no termios): arrows, space/enter, q."""
    import msvcrt  # type: ignore

    print("keyboard mode (Windows): arrows move, space/enter = play, q = quit")
    VK = {0x48: "UP", 0x50: "DOWN", 0x4B: "LEFT", 0x4D: "RIGHT"}
    try:
        while True:
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            ch = msvcrt.getch()
            if ch == b"q" or ch == b"\x03":
                return 0
            if ch == b"\x00" or ch == b"\xe0":  # extended key: second byte
                code = msvcrt.getch()[0]
                ev = VK.get(code)
            else:
                ev = "TAP" if ch in (b" ", b"\r", b"\n") else None
            if ev:
                hs.dispatch(ev)
    except KeyboardInterrupt:
        return 0


def _keyboard_loop(hs: Handset) -> int:
    if sys.platform == "win32":
        return _keyboard_loop_windows(hs)
    import tty
    import termios

    print("keyboard mode: arrows=category/item, space/Enter=play, q=quit")
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        while True:
            ch = sys.stdin.read(1)
            if ch == "q":
                return 0
            if ch == "\x1b":
                seq = ch + sys.stdin.read(2)
                ev = KEYMAP.get(seq)
            else:
                ev = KEYMAP.get(ch)
            if ev:
                hs.dispatch(ev)
    except KeyboardInterrupt:
        return 0
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def main() -> int:
    p = argparse.ArgumentParser(description="XIAO Mood Controller handset daemon")
    p.add_argument("--port", help="serial port (auto-detected if omitted)")
    p.add_argument("--config", help="path to categories.yaml")
    p.add_argument("--quiet", action="store_true", help="no console UI redraw")
    p.add_argument("--no-serial", action="store_true", help="keyboard-only mode (testing)")
    p.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    args = p.parse_args()
    if args.list_ports:
        from serial.tools import list_ports  # type: ignore

        for p_ in list_ports.comports():
            print(f"{p_.device}  {p_.description}")
        return 0
    return run(args.port, args.config, args.quiet, args.no_serial)


if __name__ == "__main__":
    raise SystemExit(main())
