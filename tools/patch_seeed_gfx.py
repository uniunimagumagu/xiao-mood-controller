#!/usr/bin/env python3
"""Applies the mbed SPI fast-path patch to the Seeed_GFX2 library.

The Seeed_GFX2 library ships an nRF52 fast path that does not compile
against the Arduino mbed core (which the XIAO nRF52840 Plus uses).
Without this patch the reproduction falls back to a byte-at-a-time path
(~2.4 s per screen update). The patch adds a block-transfer branch for
ARDUINO_ARCH_MBED. It is idempotent: running it twice does nothing.

Behavior:
  - if the library folder is missing, clones the Seeed_GFX2 repository
    into the Arduino libraries directory (git required).
  - patches    libraries/Seeed_GFX2/src/bus/Bus_SPI.cpp    in place.

Usage:  python tools/patch_seeed_gfx.py [--lib-dir PATH]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
DEFAULT_DIR = (HOME / "Documents" / "Arduino" / "libraries").resolve()
REPO = "https://github.com/Seeed-Studio/Seeed_GFX2.git"

# patch: writePixels / writeRepeat / _writeNrfBytes — add ARDUINO_ARCH_MBED branches
PATCH_PAIRS = [
    (
        '#if (defined(ARDUINO_ARCH_NRF52840) || defined(ARDUINO_ARCH_NRF52)) && !defined(ARDUINO_ARCH_MBED)\n'
        '    while (len) {\n'
        '        const size_t pixelsThisBlock =\n'
        '            min(len, NRF_TX_SCRATCH_BYTES / (size_t)2);',
        '#if defined(ARDUINO_ARCH_MBED)\n'
        '    while (len) {\n'                      # replaced by mbed branch (see emit below)
        '#elif\n'  # placeholder, real content substituted explicitly below
        '#endif',
    ),
]


def find_lib_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    return DEFAULT_DIR


def clone_if_missing(lib_dir: Path) -> Path:
    target = lib_dir / "Seeed_GFX2"
    if target.exists():
        return target
    print(f"Seeed_GFX2 not found under {lib_dir} -> cloning from {REPO}")
    shutil.which("git") or sys.exit(
        "git not found; either install git or clone Seeed_GFX2 manually into "
        f"{target} and re-run")
    lib_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", REPO, str(target)], check=True)
    return target


def apply_mbed_patch(bus_file: Path) -> bool:
    src = bus_file.read_text(encoding="utf-8")
    if "ARDUINO_ARCH_MBED" in src:
        print("patch already applied — skip")
        return False
    marker = ("#if (defined(ARDUINO_ARCH_NRF52840) || defined(ARDUINO_ARCH_NRF52))"
              " && !defined(ARDUINO_ARCH_MBED)")
    if marker not in src:
        sys.exit("unexpected Seeed_GFX2 layout; the marker moved upstream. "
                 "Update this script to match the library version.")

    # insert the mbed fast path before each of the three sites
    fast = (
        "#elif defined(ARDUINO_ARCH_MBED)\n"
        "    // mbed SPI: two-argument in-place transfer with per-block restaging\n"
        "    while (len) {\n"
        "        const size_t pixelsThisBlock = min(len, (size_t)NRF_TX_SCRATCH_BYTES / 2);\n"
        "        for (size_t i = 0; i < pixelsThisBlock; ++i) {\n"
        "            _nrfTxScratch[i * 2] = hi;\n"
        "            _nrfTxScratch[i * 2 + 1] = lo;\n"
        "        }\n"
        "        _spi->transfer(_nrfTxScratch, pixelsThisBlock * 2);\n"
        "        len -= pixelsThisBlock;\n"
        "    }\n"
    )
    # The three sites share the pattern; we implement a generic Mbed bridge
    # for writeRepeat (needs hi/lo) and a separate one for writePixels.
    def repl_writepixels(m: "re.Match[str]") -> str:
        return ('#elif defined(ARDUINO_ARCH_MBED)\n'
                '    while (len) {\n'
                '        const size_t pixelsThisBlock = min(len, (size_t)NRF_TX_SCRATCH_BYTES / 2);\n'
                '        for (size_t i = 0; i < pixelsThisBlock; ++i) {\n'
                '            const uint16_t pixel = *data++;\n'
                '            _nrfTxScratch[i * 2] = (uint8_t)(pixel >> 8);\n'
                '            _nrfTxScratch[i * 2 + 1] = (uint8_t)pixel;\n'
                '        }\n'
                '        _spi->transfer(_nrfTxScratch, pixelsThisBlock * 2);\n'
                '        len -= pixelsThisBlock;\n'
                '    }\n') + m.group(0).split("#if ")[0].rstrip("#else\n")  # not used

    # simpler: surgical string replacements based on the exact upstream text
    replacements = [
        # ---- writePixels ----
        ('''#else
    for (size_t i = 0; i < len; ++i) SEEED_SPI_WRITE16(_spi, data[i]);
#endif''',
         '''#elif defined(ARDUINO_ARCH_MBED)
    while (len) {
        const size_t pixelsThisBlock = min(len, (size_t)NRF_TX_SCRATCH_BYTES / 2);
        for (size_t i = 0; i < pixelsThisBlock; ++i) {
            const uint16_t pixel = *data++;
            _nrfTxScratch[i * 2] = (uint8_t)(pixel >> 8);
            _nrfTxScratch[i * 2 + 1] = (uint8_t)pixel;
        }
        _spi->transfer(_nrfTxScratch, pixelsThisBlock * 2);
        len -= pixelsThisBlock;
    }
#else
    for (size_t i = 0; i < len; ++i) SEEED_SPI_WRITE16(_spi, data[i]);
#endif'''),
        # ---- writeRepeat ----
        ('''#else
    for (size_t i = 0; i < len; i++) {
        SEEED_SPI_WRITE_BYTE(_spi, hi);
        SEEED_SPI_WRITE_BYTE(_spi, lo);
    }
#endif''',
         '''#elif defined(ARDUINO_ARCH_MBED)
    while (len) {
        const size_t pixelsThisBlock = min(len, (size_t)NRF_TX_SCRATCH_BYTES / 2);
        for (size_t i = 0; i < pixelsThisBlock; ++i) {
            _nrfTxScratch[i * 2] = hi;
            _nrfTxScratch[i * 2 + 1] = lo;
        }
        _spi->transfer(_nrfTxScratch, pixelsThisBlock * 2);
        len -= pixelsThisBlock;
    }
#else
    for (size_t i = 0; i < len; i++) {
        SEEED_SPI_WRITE_BYTE(_spi, hi);
        SEEED_SPI_WRITE_BYTE(_spi, lo);
    }
#endif'''),
        # ---- _writeNrfBytes ----
        ('''#if (defined(ARDUINO_ARCH_NRF52840) || defined(ARDUINO_ARCH_NRF52)) && !defined(ARDUINO_ARCH_MBED)
void Bus_SPI::_writeNrfBytes(const uint8_t* data, size_t len) {
    while (len) {
        const size_t block = min(len, (size_t)NRF_TX_SCRATCH_BYTES);
        // The caller may point into flash (init tables/fonts), while nRF
        // EasyDMA accepts RAM only. Always stage the bytes before transfer.
        memcpy(_nrfTxScratch, data, block);
        _spi->transfer(_nrfTxScratch, nullptr, block);
        data += block;
        len -= block;
    }
}
#endif''',
         '''#if (defined(ARDUINO_ARCH_NRF52840) || defined(ARDUINO_ARCH_NRF52)) && !defined(ARDUINO_ARCH_MBED)
void Bus_SPI::_writeNrfBytes(const uint8_t* data, size_t len) {
    while (len) {
        const size_t block = min(len, (size_t)NRF_TX_SCRATCH_BYTES);
        // The caller may point into flash (init tables/fonts), while nRF
        // EasyDMA accepts RAM only. Always stage the bytes before transfer.
        memcpy(_nrfTxScratch, data, block);
        _spi->transfer(_nrfTxScratch, nullptr, block);
        data += block;
        len -= block;
    }
}
#elif defined(ARDUINO_ARCH_MBED)
void Bus_SPI::_writeNrfBytes(const uint8_t* data, size_t len) {
    while (len) {
        const size_t block = min(len, (size_t)NRF_TX_SCRATCH_BYTES);
        memcpy(_nrfTxScratch, data, block);   // stage RAM copy (flash-safe)
        _spi->transfer(_nrfTxScratch, block);
        data += block;
        len -= block;
    }
}
#endif'''),
    ]
    n_applied = 0
    for old, new in replacements:
        if old in src:
            src = src.replace(old, new)
            n_applied += 1
    if n_applied:
        bus_file.write_text(src, encoding="utf-8")
        print(f"patched {n_applied} site(s) in {bus_file}")
        return True
    print("no site needed patching (already mbed-capable or newer layout)")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib-dir", help="Arduino libraries directory (default: ~/Documents/Arduino/libraries)")
    args = ap.parse_args()
    lib_dir = find_lib_dir(args.lib_dir)
    target = clone_if_missing(lib_dir)
    bus_file = target / "src" / "bus" / "Bus_SPI.cpp"
    if not bus_file.exists():
        sys.exit(f"{bus_file} missing — is Seeed_GFX2 needed?")
    apply_mbed_patch(bus_file)
    print(f"Seeed_GFX2 ready at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
