#!/usr/bin/env python3
"""
Generate the menu bar app's icon set from the Clawd sprite sheets.

The sprite headers under firmware/main/assets/ are the shipped Clawd artwork:
RLE-compressed RGB565 with a transparent key colour. This decodes a chosen frame
from each and emits two sets of PNGs:

  icons/<state>.png        44x44 for the status bar
  icons/rows/<anim>.png    96x96 for the session rows in the popover

Both sets are full colour, NOT macOS template images. A template image is drawn
from its alpha channel alone, which flattens the crab to a featureless blob and
destroys exactly the thing the icon exists to convey: the red "!" of a session
waiting on you, the yellow stars of an error, the thought bubble of a session
thinking. Colour is the state signal here, so template mode is not an option.

Sprites are cropped to their non-transparent content before scaling — the source
bounding boxes carry symmetric padding to keep Clawd centred on the device, and
at 20pt in the menu bar that padding would shrink the crab to nothing.

Stdlib only — no PIL, no playwright. Run from the repo root:

    python3 tools/make_menubar_icons.py
    python3 tools/make_menubar_icons.py --only waiting   # regenerate one icon
"""

import argparse
import re
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPRITE_DIR = REPO / "firmware" / "main" / "assets"
ICON_DIR = REPO / "host" / "clawd_tank_menubar" / "icons"

MENUBAR_PX = 44
ROW_PX = 96

# Status bar icons: aggregate session state -> (sprite header, frame, opacity).
# Frames are chosen for legibility as a still, not for their place in the loop:
# most of these animations spend the majority of their cycle on a plain crab and
# only flash their distinguishing element (the "!", the stars) for a few frames.
MENUBAR_ICONS = {
    "crab-sleeping": ("sprite_sleeping", 18, 1.0),   # crab under a "Z"
    "crab-idle": ("sprite_idle", 0, 1.0),
    "crab-thinking": ("sprite_thinking", 8, 1.0),    # thought bubble up
    "crab-working": ("sprite_typing", 0, 1.0),       # crab at the laptop
    "crab-waiting": ("sprite_alert", 8, 1.0),        # red "!" raised
    "crab-error": ("sprite_dizzy", 16, 1.0),         # stars out
    # Daemon thread died. sprite_disconnected is a crab beside a Bluetooth glyph,
    # which says nothing once the device is gone — a ghosted idle crab reads as
    # "the app isn't running" without implying a radio.
    "crab-offline": ("sprite_idle", 0, 0.35),
}

# Popover row sprites, keyed by the animation names the UI maps sessions to.
ROW_SPRITES = {
    "idle": ("sprite_idle", 0, 1.0),
    "sleeping": ("sprite_sleeping", 18, 1.0),
    "thinking": ("sprite_thinking", 8, 1.0),
    "typing": ("sprite_typing", 0, 1.0),
    "building": ("sprite_building", 0, 1.0),
    "debugger": ("sprite_debugger", 0, 1.0),
    "wizard": ("sprite_wizard", 0, 1.0),
    "beacon": ("sprite_beacon", 0, 1.0),
    "conducting": ("sprite_conducting", 0, 1.0),
    "sweeping": ("sprite_sweeping", 0, 1.0),
    "alert": ("sprite_alert", 8, 1.0),
    "confused": ("sprite_confused", 24, 1.0),
    "dizzy": ("sprite_dizzy", 16, 1.0),
}


# --- Sprite header parsing -------------------------------------------------

def parse_sprite_header(path: Path) -> dict:
    """Extract dimensions, frame offsets and RLE payload from a sprite header."""
    text = path.read_text()

    def const(suffix: str) -> int:
        m = re.search(rf"#define\s+\w+_{suffix}\s+(0x[0-9A-Fa-f]+|\d+)", text)
        if not m:
            raise ValueError(f"{path.name}: no {suffix} constant")
        return int(m.group(1), 0)

    def array(suffix: str) -> list[int]:
        m = re.search(rf"\w+_{suffix}\[\w*\]\s*=\s*\{{(.*?)\}};", text, re.S)
        if not m:
            raise ValueError(f"{path.name}: no {suffix} array")
        return [int(tok, 0) for tok in re.findall(r"0x[0-9A-Fa-f]+|\d+", m.group(1))]

    return {
        "width": const("WIDTH"),
        "height": const("HEIGHT"),
        "frame_count": const("FRAME_COUNT"),
        "transparent": const("TRANSPARENT_KEY"),
        "offsets": array("frame_offsets"),
        "rle": array("rle_data"),
    }


def decode_frame(sprite: dict, index: int) -> bytearray:
    """Decode one frame to a width*height RGBA buffer."""
    w, h = sprite["width"], sprite["height"]
    if not 0 <= index < sprite["frame_count"]:
        raise IndexError(f"frame {index} out of range (0..{sprite['frame_count'] - 1})")

    start, end = sprite["offsets"][index], sprite["offsets"][index + 1]
    rle = sprite["rle"][start:end]
    key = sprite["transparent"]

    rgba = bytearray(w * h * 4)
    pos = 0
    for i in range(0, len(rle) - 1, 2):
        colour, run = rle[i], rle[i + 1]
        if colour == key:
            pos += run  # already zeroed → transparent
            continue
        r = ((colour >> 11) & 0x1F) * 255 // 31
        g = ((colour >> 5) & 0x3F) * 255 // 63
        b = (colour & 0x1F) * 255 // 31
        pixel = bytes((r, g, b, 255))
        for _ in range(run):
            if pos >= w * h:
                break
            rgba[pos * 4:pos * 4 + 4] = pixel
            pos += 1

    if pos != w * h:
        print(f"  warning: decoded {pos} of {w * h} pixels", file=sys.stderr)
    return rgba


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


# --- PNG output ------------------------------------------------------------

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


# --- Driver ----------------------------------------------------------------

def render(sprite_name: str, frame: int, opacity: float, size: int,
           out_path: Path) -> None:
    sprite = parse_sprite_header(SPRITE_DIR / f"{sprite_name}.h")
    pixels, cw, ch = crop_to_content(
        decode_frame(sprite, frame), sprite["width"], sprite["height"]
    )
    rgba = apply_opacity(fit_into_square(pixels, cw, ch, size), opacity)
    write_png(out_path, size, rgba)
    dim = f" @{opacity:.0%}" if opacity < 1.0 else ""
    print(f"  {out_path.relative_to(REPO)}  "
          f"({sprite['width']}x{sprite['height']} frame {frame} "
          f"-> crop {cw}x{ch} -> {size}x{size}{dim})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="Regenerate a single icon or row sprite by name")
    args = parser.parse_args()

    if not SPRITE_DIR.is_dir():
        print(f"error: sprite headers not found at {SPRITE_DIR}", file=sys.stderr)
        return 1

    print("Status bar icons:")
    for name, (sprite_name, frame, opacity) in MENUBAR_ICONS.items():
        if args.only and args.only not in (name, name.removeprefix("crab-")):
            continue
        render(sprite_name, frame, opacity, MENUBAR_PX, ICON_DIR / f"{name}.png")

    print("Popover row sprites:")
    for name, (sprite_name, frame, opacity) in ROW_SPRITES.items():
        if args.only and args.only != name:
            continue
        render(sprite_name, frame, opacity, ROW_PX, ICON_DIR / "rows" / f"{name}.png")

    return 0


if __name__ == "__main__":
    sys.exit(main())
