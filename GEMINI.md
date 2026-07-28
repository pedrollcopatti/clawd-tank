# GEMINI.md

This file provides guidance to Gemini CLI when working with code and assets in this repository.

## Project Overview

Clawd Tank is a macOS menu bar app that shows what your Claude Code sessions are
doing, using an animated pixel-art crab ("Clawd").

**Gemini's Primary Role:** You are the lead animator and technical artist for the
project. Your focus is creating and modifying the SVG animations that bring Clawd
to life, and turning them into the still frames the app ships.

> Clawd previously lived on an ESP32-C6 display, and the pipeline ended in
> RLE-compressed RGB565 C headers. That hardware is gone; the pipeline now ends
> in PNGs. Git history has the old tooling.

## Asset Directories

- `assets/svg-animations/`: The source of truth for all animations. Raw `.svg`
  files (e.g. `clawd-idle-living.svg`, `clawd-working-typing.svg`).
- `assets/clawd-frames/`: One still per animation, `<name>-<frame>.png`, cropped
  to content. These are what the icon generator consumes.
- `assets/captures/`: Rendered GIF previews of the animations for review.
- `host/clawd_tank_menubar/icons/`: Generated. Status bar icons (44x44) and
  popover row sprites (`rows/`, 96x96). Never hand-edit these.

## Sprite Pipeline

### 1. Render SVG to PNG frames

```bash
python tools/svg2frames.py assets/svg-animations/<animation>.svg /tmp/clawd_frames/ --fps <fps> --scale 6
```

Target framerates vary by animation (≈6 FPS for idle/sleeping, ≈10 FPS for
happy/alert). `--scale 6` is standard and keeps the pixel art crisp. Requires
playwright + chromium.

### 2. Pick the still and add it to `assets/clawd-frames/`

Choose the frame that shows the animation's *distinguishing element* — the red
"!" for alert, the stars for dizzy, the thought bubble for thinking — not frame
0. Most of these loops spend the bulk of their cycle on a plain crab. Name it
`<animation>-<frame index>.png`.

### 3. Regenerate the app icons

```bash
python3 tools/make_menubar_icons.py
python3 tools/make_menubar_icons.py --only waiting   # just one
```

Stdlib only. If you added a new animation, register it in `MENUBAR_ICONS` or
`ROW_SPRITES` in that script, and map some session state to it in
`host/clawd_tank_menubar/session_view_model.py`.

## Design & Animation Guidelines

1. **Reference Design:** `assets/svg-animations/clawd-static-base.svg` is the
   reference for the character. Base new work on it so dimensions, base colours
   (e.g. `#DE886D` body) and structure stay consistent.
2. **Legibility at 20pt.** The status bar draws these at 20 points. A detail that
   only reads at 96px is not worth adding. Distinguish states by silhouette and
   by one strongly coloured element, not by shading.
3. **Colour is the signal.** These are *not* macOS template images — the app
   deliberately ships them in colour, because a template is drawn from alpha
   alone and would flatten Clawd to a featureless blob.
4. **Palette:** Prefer solid colours and flat shading over gradients; the art
   still reads as pixel art and gradients muddy it at small sizes.
5. **Animation Complexity:** Keep SVGs lean — the renderer has to handle them
   reliably. Avoid complex SVG filters or external raster references. When
   designing CSS `@keyframes`, avoid overlapping percentage ranges (e.g.
   `0%, 35%`); to hold a state, define start and end explicitly with the same
   value to prevent unintended interpolation.
6. **Looping:** Most animations should loop seamlessly — first and last frames
   should match.
7. **Workflow:** When asked to create or update an animation, **first create or
   modify the SVG only and ask the user for visual feedback.** Do not run
   conversion scripts or regenerate icons until the SVG is approved.

## Testing

```bash
cd host && ./build.sh --install
```

Then open a Claude Code session and drive it through states — run a long `Bash`
command, trigger a permission prompt — and check the status bar icon and the
popover rows.

## TODO Tracking

If your animation work spans multiple sessions, update `TODO.md` to track the
progress of the asset pipeline.
