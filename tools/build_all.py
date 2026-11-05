#!/usr/bin/env python3
"""Cross-platform bootstrap for the XIAO Mood Controller.

Idempotent. Safe to run again on any machine.
    python tools/build_all.py               # venv + deps + bridge + compile
    python tools/build_all.py --upload      # …then flash the detected device
    python tools/build_all.py --port COM5   # choose the serial port (Windows)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / (".venv" if sys.platform != "win32" else ".venv")
FIRMWARE = ROOT / "firmware" / "xiao_mood_handset"
FQBN = "Seeeduino:mbed:xiaonRF52840Plus"
BOARDS_URL = "https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json"

REQUIRED_TOOLS = {"arduino-cli": {
    "brew": "brew install arduino-cli",
    "scoop": "scoop install arduino-cli",
    "choco": "choco install arduino-cli",
    "manual": "https://arduino.github.io/arduino-cli/latest/installation/",
}}


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> int:
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=str(cwd or ROOT), check=check).returncode


def python_venv_executable() -> Path:
    if sys.platform == "win32":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def ensure_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        print(f"'{name}' not found. Install hints:", flush=True)
        for k, v in REQUIRED_TOOLS[name].items():
            print(f"  [{k}] {v}", flush=True)
        sys.exit(1)
    return path


def venv_python() -> str:
    exe = python_venv_executable()
    if not exe.exists():
        print("creating .venv …", flush=True)
        py = "python" if sys.platform != "win32" else "python.exe"
        run([py, "-m", "venv", str(VENV)])
    if not exe.exists():
        sys.exit(".venv creation failed; is python3 on PATH?")
    return str(exe)


def pip_install(python: str) -> None:
    step("install python dependencies")
    run([python, "-m", "pip", "install", "--quiet", "-U", "pip"])
    run([python, "-m", "pip", "install", "--quiet", "-r", str(ROOT / "requirements.txt")])


def arduino_bootstrap(cli: str) -> None:
    step("arduino-cli: board package + libraries")
    run([cli, "config", "init", "--overwrite"])
    run([cli, "config", "add", "board_manager.additional_urls", BOARDS_URL])
    run([cli, "core", "update-index"])
    rc = run([cli, "core", "list"], check=False)
    if "Seeeduino:mbed" not in subprocess.run([cli, "core", "list"], capture_output=True, text=True).stdout:
        run([cli, "core", "install", "Seeeduino:mbed"])
    for lib in ("U8g2_for_Adafruit_GFX", "Adafruit GFX Library"):
        out = subprocess.run([cli, "lib", "list"], capture_output=True, text=True).stdout
        if lib.split()[0] not in out:
            run([cli, "lib", "install", lib])
    # Seeed_GFX2 comes via git + patch
    step("install/patch Seeed_GFX2 into the Arduino libraries folder")
    run([sys.executable, str(ROOT / "tools" / "patch_seeed_gfx.py")])


def compile_firmware(cli: str) -> int:
    step("compile firmware")
    return run([cli, "compile", "--fqbn", FQBN, str(FIRMWARE)])


def find_serial_port(cli: str) -> str | None:
    out = subprocess.run([cli, "board", "list"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "XIAO" in line and "usb" in line:
            return line.split()[0]
    # fallback: pyserial if installed in the venv
    try:
        from serial.tools import list_ports  # type: ignore
        for p in list_ports.comports():
            if "XIAO" in (p.description or "") or "nRF52840" in (p.description or ""):
                return p.device
    except Exception:
        pass
    return None


def upload_firmware(cli: str, port: str | None) -> int:
    step("upload firmware")
    port = port or find_serial_port(cli)
    if not port:
        print("no device detected. plug the XIAO nRF52840 Plus and check the port:", flush=True)
        run([cli, "board", "list"])
        sys.exit(1)
    print(f"  using port {port}", flush=True)
    return run([cli, "upload", "-p", port, "--fqbn", FQBN, str(FIRMWARE)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true", help="flash after compiling")
    ap.add_argument("--port", help="serial port override (Windows: COMx; macOS: /dev/cu.usbmodem*)")
    ap.add_argument("--skip-deps", action="store_true", help="skip pip install")
    args = ap.parse_args()

    python = venv_python()
    if not args.skip_deps:
        pip_install(python)
    cli = ensure_tool("arduino-cli")
    arduino_bootstrap(cli)
    rc = compile_firmware(cli)
    if rc != 0:
        sys.exit(rc)
    if args.upload:
        rc = upload_firmware(cli, args.port)
        if rc:
            sys.exit(rc)
        print("\nDone. run the daemon with:\n"
              "  mac/Linux:  tools/setup_env.sh            # once\n"
              "  base:       .venv/bin/python tools/supervisor.py   # daemon loop\n"
              "  Windows:    tools\\setup_env.bat           # once\n"
              "  base:       .venv\\Scripts\\python tools\\supervisor.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
