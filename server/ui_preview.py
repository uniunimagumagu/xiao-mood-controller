"""Renders a 320x172 preview of the panel UI on the PC (same layout math
as the firmware) so the design can be reviewed without flashing."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

W, H = 320, 172
BG = (8, 6, 20)
ACCENT = (124, 77, 255)      # purple
ACCENT2 = (100, 150, 255)    # blue
GREY = (120, 120, 135)
WHITE = (235, 235, 240)
GREEN = (80, 220, 130)
ORANGE = (255, 160, 60)

FONT_EN = "/System/Library/Fonts/Helvetica.ttc"
FONT_JP = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


JP = {16: _font(FONT_JP, 16), 14: _font(FONT_JP, 14), 12: _font(FONT_JP, 12), 10: _font(FONT_JP, 10), 8: _font(FONT_JP, 8)}


def preview(out_png: str, category="J-POP", cat_idx=(1, 6), title="YOASOBI「アイドル」Official Music Video",
            channel="YOASOBI", pos=(1, 10), playing=True, thumb: Path | None = None,
            link=True, status="Ready") -> None:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # flat bg, narrow accent line only
    d.rectangle((0, 0, W, 3), fill=ACCENT)

    # header: category + link dot
    d.text((10, 7), "♪", font=JP[14], fill=ACCENT)
    d.text((28, 8), category, font=JP[14], fill=WHITE)
    d.text((292, 11), f"{cat_idx[0]}/{cat_idx[1]}", font=JP[10], fill=ACCENT2)
    d.ellipse((308 - 8, 12, 308 + 2, 22), fill=(GREEN if link else (200, 60, 60)))



    rx = 10  # full-width info rows (no thumb)
    words = title.split("「")
    d.text((rx, 48), title[:24], font=JP[16], fill=WHITE)
    d.text((rx, 74), title[24:48], font=JP[16], fill=WHITE)
    d.text((rx, 100), channel[:22], font=JP[12], fill=ACCENT2 if channel else GREY)

    # candidate position dots
    total = max(pos[1], 1); cur = pos[0]
    shown = min(total, 9)
    x0 = rx
    for i in range(shown):
        fill = ACCENT if i == (cur - 1) % shown and total <= 9 else (70, 66, 96)
        d.ellipse((x0 + i * 10, 112, x0 + i * 10 + 6, 118), fill=fill)
    if total > 9:
        d.text((x0 + shown * 10 + 6, 110), f"{cur}/{total}", font=JP[10], fill=GREY)

    # play hint (vector triangle + text)
    d.polygon([(rx + 2, 146), (rx + 2, 160), (rx + 14, 153)], fill=GREEN)
    d.text((rx + 20, 145), "タップで再生", font=JP[12], fill=(180, 190, 205))

    # status bottom bar
    d.line([(0, H - 13), (W, H - 13)], fill=(38, 26, 70))
    d.text((8, H - 13), status, font=JP[8], fill=(210, 195, 255) if status else GREY)
    if playing:
        d.ellipse((W - 46, H - 10, W - 40, H - 4), fill=GREEN)
        d.text((W - 36, H - 12), "PLAYING", font=JP[8], fill=GREEN)
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    im.save(out_png)
    print("preview:", out_png)


if __name__ == "__main__":
    preview("docs/ui_preview.png", thumb=Path("data/thumbs/FHxUZlaKCTs.jpg"))
