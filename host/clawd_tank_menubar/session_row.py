# host/clawd_tank_menubar/session_row.py
"""AppKit views for one session row in the popover.

Deliberately popover-agnostic: build_session_row() returns a plain NSView, so
the same rows can be hosted in an NSPopover or, if that ever misbehaves, in a
custom-view NSMenuItem — the pattern already used for the brightness slider.

Layout uses explicit frames plus autoresizing masks rather than Auto Layout, to
match slider.py and avoid introducing a second layout system into a codebase
that has none.
"""

import functools
import importlib.resources

import AppKit

ROW_W = 320.0
ROW_H = 46.0

SPRITE_PX = 26.0
TEXT_LEFT = 48.0
RIGHT_COL_W = 62.0

ACCENT_COLORS = {
    "waiting": (1.00, 0.58, 0.00),   # amber — needs you
    "error": (0.90, 0.22, 0.21),     # red
}


@functools.lru_cache(maxsize=32)
def load_sprite(name: str):
    """NSImage for a popover row sprite, or None if it isn't bundled.

    Cached because reload() rebuilds rows on every push and re-reading the same
    PNG off disk each time is pure waste.
    """
    try:
        path = (importlib.resources.files("clawd_tank_menubar")
                / "icons" / "rows" / f"{name}.png")
        image = AppKit.NSImage.alloc().initByReferencingFile_(str(path))
    except Exception:
        return None
    if image is None or not image.isValid():
        return None
    image.setSize_(AppKit.NSMakeSize(SPRITE_PX, SPRITE_PX))
    return image


def _label(size, color, weight=None):
    field = AppKit.NSTextField.labelWithString_("")
    field.setFont_(
        AppKit.NSFont.systemFontOfSize_weight_(size, weight) if weight is not None
        else AppKit.NSFont.systemFontOfSize_(size)
    )
    field.setTextColor_(color)
    field.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
    return field


class SessionRowView:
    """Holds one row's views so a refresh can update text in place.

    Rebuilding NSViews for a tick that only moved the clock forward would be
    wasteful and would fight VoiceOver focus, so the elapsed label has its own
    cheap update path.
    """

    def __init__(self, view, accent, sprite, title, subtitle, elapsed, badge):
        self.view = view
        self._accent = accent
        self._sprite = sprite
        self._title = title
        self._subtitle = subtitle
        self._elapsed = elapsed
        self._badge = badge

    def apply(self, model):
        self._title.setStringValue_(model.project)
        self._subtitle.setStringValue_(model.detail)
        self._elapsed.setStringValue_(model.elapsed_text)

        image = load_sprite(model.sprite)
        if image is not None:
            self._sprite.setImage_(image)

        self._badge.setHidden_(model.subagents == 0)
        if model.subagents:
            self._badge.setStringValue_(f"⌁ {model.subagents}")

        rgb = ACCENT_COLORS.get(model.accent)
        self._accent.setHidden_(rgb is None)
        if rgb is not None:
            self._accent.setFillColor_(
                AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(*rgb, 1.0)
            )

    def apply_elapsed(self, text):
        """The once-a-second path: only the clock moved."""
        self._elapsed.setStringValue_(text)


def build_session_row(model) -> SessionRowView:
    """Build one row and populate it from `model`."""
    view = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, ROW_W, ROW_H)
    )
    view.setAutoresizingMask_(AppKit.NSViewWidthSizable)

    # Accent stripe — hidden unless the session wants something from you.
    accent = AppKit.NSBox.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 5, 3, ROW_H - 10)
    )
    accent.setBoxType_(AppKit.NSBoxCustom)
    accent.setBorderWidth_(0)
    accent.setCornerRadius_(1.5)
    view.addSubview_(accent)

    sprite = AppKit.NSImageView.alloc().initWithFrame_(
        AppKit.NSMakeRect(14, (ROW_H - SPRITE_PX) / 2, SPRITE_PX, SPRITE_PX)
    )
    sprite.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
    view.addSubview_(sprite)

    text_w = ROW_W - TEXT_LEFT - RIGHT_COL_W - 12

    title = _label(13, AppKit.NSColor.labelColor(), AppKit.NSFontWeightMedium)
    title.setFrame_(AppKit.NSMakeRect(TEXT_LEFT, 24, text_w, 17))
    title.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    view.addSubview_(title)

    subtitle = _label(11, AppKit.NSColor.secondaryLabelColor())
    subtitle.setFrame_(AppKit.NSMakeRect(TEXT_LEFT, 7, text_w, 15))
    subtitle.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    view.addSubview_(subtitle)

    # Monospaced digits so the row doesn't jitter as the clock ticks.
    elapsed = _label(11, AppKit.NSColor.tertiaryLabelColor())
    elapsed.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
        11, AppKit.NSFontWeightRegular
    ))
    elapsed.setAlignment_(AppKit.NSTextAlignmentRight)
    elapsed.setFrame_(AppKit.NSMakeRect(ROW_W - RIGHT_COL_W - 12, 24, RIGHT_COL_W, 15))
    elapsed.setAutoresizingMask_(AppKit.NSViewMinXMargin)
    view.addSubview_(elapsed)

    badge = _label(10, AppKit.NSColor.secondaryLabelColor())
    badge.setAlignment_(AppKit.NSTextAlignmentRight)
    badge.setFrame_(AppKit.NSMakeRect(ROW_W - RIGHT_COL_W - 12, 8, RIGHT_COL_W, 14))
    badge.setAutoresizingMask_(AppKit.NSViewMinXMargin)
    view.addSubview_(badge)

    row = SessionRowView(view, accent, sprite, title, subtitle, elapsed, badge)
    row.apply(model)
    return row


def build_empty_state(model) -> AppKit.NSView:
    """Centred placeholder shown when no session is running."""
    height = 108.0
    view = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, ROW_W, height)
    )
    view.setAutoresizingMask_(AppKit.NSViewWidthSizable)

    sprite_px = 44.0
    sprite = AppKit.NSImageView.alloc().initWithFrame_(
        AppKit.NSMakeRect((ROW_W - sprite_px) / 2, height - sprite_px - 8,
                          sprite_px, sprite_px)
    )
    sprite.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
    image = load_sprite(model.sprite)
    if image is not None:
        image = image.copy()
        image.setSize_(AppKit.NSMakeSize(sprite_px, sprite_px))
        sprite.setImage_(image)
    view.addSubview_(sprite)

    title = _label(13, AppKit.NSColor.labelColor(), AppKit.NSFontWeightMedium)
    title.setStringValue_(model.title)
    title.setAlignment_(AppKit.NSTextAlignmentCenter)
    title.setFrame_(AppKit.NSMakeRect(12, 30, ROW_W - 24, 17))
    title.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    view.addSubview_(title)

    detail = _label(11, AppKit.NSColor.secondaryLabelColor())
    detail.setStringValue_(model.detail)
    detail.setAlignment_(AppKit.NSTextAlignmentCenter)
    detail.setFrame_(AppKit.NSMakeRect(12, 12, ROW_W - 24, 15))
    detail.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    view.addSubview_(detail)

    return view


def build_separator(width: float = ROW_W) -> AppKit.NSView:
    box = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, width, 1))
    box.setBoxType_(AppKit.NSBoxSeparator)
    box.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    return box
