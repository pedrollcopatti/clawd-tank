#!/usr/bin/env python3
"""
Generate the menu bar app's icon set from the Clawd frame stills.

Source art lives in assets/clawd-frames/ as <animation>-<frame>.png: one still
per animation, picked for legibility rather than for its place in the loop —
most of these animations spend the bulk of their cycle on a plain crab and only
flash their distinguishing element (the red "!", the error stars) for a few
frames. They were exported from the device sprite sheets before the firmware was
removed; assets/svg-animations/ holds the original animated SVGs.

Emits two sets of PNGs:

  icons/<state>.png        44x44 for the status bar
  icons/rows/<anim>.png    96x96 for the session rows in the popover

Both are full colour, NOT macOS template images. A template image is drawn from
its alpha channel alone, which flattens the crab to a featureless blob and
destroys exactly what the icon exists to convey. Colour is the state signal
here, so template mode is not an option.

44x44 because rumps forces setSize_((20, 20)) on the status item image, so a
Retina display asks for 40 physical pixels.

Stdlib only — no PIL, no playwright. Run from the repo root:

    python3 tools/make_menubar_icons.py
    python3 tools/make_menubar_icons.py --only waiting   # regenerate one icon
"""

import argparse
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRAME_DIR = REPO / "assets" / "clawd-frames"
ICON_DIR = REPO / "host" / "clawd_tank_menubar" / "icons"

MENUBAR_PX = 44
ROW_PX = 96

# Status bar icon -> (frame file stem, opacity).
MENUBAR_ICONS = {
    "crab-sleeping": ("sleeping-18", 1.0),   # crab under a "Z"
    "crab-idle": ("idle-0", 1.0),
    "crab-thinking": ("thinking-8", 1.0),    # thought bubble up
    "crab-working": ("typing-0", 1.0),       # crab at the laptop
    "crab-waiting": ("alert-8", 1.0),        # red "!" raised
    "crab-error": ("dizzy-16", 1.0),         # stars out
    # Daemon thread died. A ghosted idle crab reads as "the app isn't running"
    # without implying anything about a connection.
    "crab-offline": ("idle-0", 0.35),
}

# Popover row sprites, keyed by the animation names session_view_model.py picks.
ROW_SPRITES = {
    "idle": ("idle-0", 1.0),
    "sleeping": ("sleeping-18", 1.0),
    "thinking": ("thinking-8", 1.0),
    "typing": ("typing-0", 1.0),
    "building": ("building-0", 1.0),
    "debugger": ("debugger-0", 1.0),
    "wizard": ("wizard-0", 1.0),
    "beacon": ("beacon-0", 1.0),
    "conducting": ("conducting-0", 1.0),
    "sweeping": ("sweeping-0", 1.0),
    "alert": ("alert-8", 1.0),
    "confused": ("confused-24", 1.0),
    "dizzy": ("dizzy-16", 1.0),
}


# --- PNG I/O ---------------------------------------------------------------

def read_png(path: Path) -> tuple[bytearray, int, int]:
    """Read an 8-bit RGBA PNG. Returns (pixels, width, height).

    Only handles what write_png() below emits — a single IDAT of filter-0 rows.
    These files are ours; a general decoder would be dead weight.
    """
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name}: not a PNG")

    pos, width, height, idat = 8, 0, 0, bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", payload[:10])
            if (depth, colour) != (8, 6):
                raise ValueError(f"{path.name}: expected 8-bit RGBA")
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
        pos += 12 + length

    raw = zlib.decompress(bytes(idat))
    stride = width * 4
    pixels = bytearray(width * height * 4)
    for y in range(height):
        start = y * (stride + 1)
        if raw[start] != 0:
            raise ValueError(f"{path.name}: unsupported row filter")
        pixels[y * stride:(y + 1) * stride] = raw[start + 1:start + 1 + stride]
    return pixels, width, height


def write_png(path: Path, size: int, rgba: bytearray) -> None:
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0 (None)
        raw += rgba[y * size * 4:(y + 1) * size * 4]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


# --- Resampling ------------------------------------------------------------

def crop_to_content(rgba: bytearray, w: int, h: int) -> tuple[bytearray, int, int]:
    """Trim fully transparent margins. Returns (pixels, width, height)."""
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = y * w
        for x in range(w):
            if rgba[(row + x) * 4 + 3]:
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
    if x1 < 0:
        return rgba, w, h  # fully transparent — nothing to crop

    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    out = bytearray(cw * ch * 4)
    for y in range(ch):
        src = ((y + y0) * w + x0) * 4
        out[y * cw * 4:(y + 1) * cw * 4] = rgba[src:src + cw * 4]
    return out, cw, ch


def fit_into_square(rgba: bytearray, w: int, h: int, size: int) -> bytearray:
    """Scale to fit a size x size canvas, preserving aspect, centred.

    Box filter with premultiplied alpha — averaging straight RGBA would pull the
    colour of fully transparent pixels (0,0,0,0) into the edges and leave a dark
    halo around the sprite.
    """
    scale = min(size / w, size / h)
    dw, dh = max(1, round(w * scale)), max(1, round(h * scale))
    off_x, off_y = (size - dw) // 2, (size - dh) // 2

    out = bytearray(size * size * 4)
    for dy in range(dh):
        y0, y1 = dy * h // dh, max(dy * h // dh + 1, (dy + 1) * h // dh)
        for dx in range(dw):
            x0, x1 = dx * w // dw, max(dx * w // dw + 1, (dx + 1) * w // dw)
            r = g = b = a = n = 0
            for sy in range(y0, y1):
                row = sy * w
                for sx in range(x0, x1):
                    p = (row + sx) * 4
                    pa = rgba[p + 3]
                    r += rgba[p] * pa
                    g += rgba[p + 1] * pa
                    b += rgba[p + 2] * pa
                    a += pa
                    n += 1
            if n == 0 or a == 0:
                continue
            o = ((dy + off_y) * size + dx + off_x) * 4
            out[o] = min(255, r // a)
            out[o + 1] = min(255, g // a)
            out[o + 2] = min(255, b // a)
            out[o + 3] = a // n
    return out


def apply_opacity(rgba: bytearray, factor: float) -> bytearray:
    if factor >= 1.0:
        return rgba
    for i in range(3, len(rgba), 4):
        rgba[i] = int(rgba[i] * factor)
    return rgba


# --- Driver ----------------------------------------------------------------

def render(frame: str, opacity: float, size: int, out_path: Path) -> None:
    pixels, w, h = read_png(FRAME_DIR / f"{frame}.png")
    pixels, w, h = crop_to_content(pixels, w, h)
    rgba = apply_opacity(fit_into_square(pixels, w, h, size), opacity)
    write_png(out_path, size, rgba)
    dim = f" @{opacity:.0%}" if opacity < 1.0 else ""
    print(f"  {out_path.relative_to(REPO)}  ({frame} {w}x{h} -> {size}x{size}{dim})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", help="Regenerate a single icon or row sprite by name")
    args = parser.parse_args()

    if not FRAME_DIR.is_dir():
        print(f"error: frame stills not found at {FRAME_DIR}", file=sys.stderr)
        return 1

    print("Status bar icons:")
    for name, (frame, opacity) in MENUBAR_ICONS.items():
        if args.only and args.only not in (name, name.removeprefix("crab-")):
            continue
        render(frame, opacity, MENUBAR_PX, ICON_DIR / f"{name}.png")

    print("Popover row sprites:")
    for name, (frame, opacity) in ROW_SPRITES.items():
        if args.only and args.only != name:
            continue
        render(frame, opacity, ROW_PX, ICON_DIR / "rows" / f"{name}.png")

    return 0


if __name__ == "__main__":
    sys.exit(main())
