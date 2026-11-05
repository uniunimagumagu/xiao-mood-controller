#!/usr/bin/env python3
"""Cross-platform runner for the handset daemon (replaces the tmux loop).

Usage:
    python tools/supervisor.py             # run and restart the daemon forever
    python tools/supervisor.py --once      # run the daemon once (no restarts)

Requires: a .venv (created by tools/setup_env.sh / .ps1). The daemon writes its
own log to data/mood_daemon.log; this process writes to data/supervisor.log.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "supervisor.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="do not restart on exit")
    args = ap.parse_args()

    cmd = [str(ROOT / ".venv" / "bin" / "python"), "-u", "-m", "server.handset"]
    if sys.platform == "win32":
        cmd[0] = str(ROOT / ".venv" / "Scripts" / "python.exe")

    env_file = ROOT / ".env"
    log(f"supervisor start (cmd={cmd}) env={env_file.exists()}")

    while True:
        log("daemon: starting...")
        try:
            result = subprocess.run(cmd, cwd=str(ROOT))
            rc = result.returncode
        except KeyboardInterrupt:
            log("supervisor: interrupted")
            return 0
        log(f"daemon exited rc={rc}")
        if args.once or rc == 0:
            return rc
        time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())
