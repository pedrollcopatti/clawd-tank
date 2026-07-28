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


class ClickableRowView(AppKit.NSView):
    """A row that highlights under the pointer and reports clicks.

    An NSButton spanning the row would swallow the label subviews' own drawing
    and fight their colours; a plain view with mouseUp_ keeps the layout exactly
    as it is and adds only the behaviour.
    """

    # Deliberately NOT flipped: the row's own subviews are laid out bottom-up
    # like any ordinary NSView. Only the container that stacks rows is flipped,
    # so they read top to bottom.

    def acceptsFirstMouse_(self, event):
        # The popover isn't key when it first appears, so without this the first
        # click after opening would only activate it and be thrown away.
        return True

    def hitTest_(self, point):
        # The labels and sprite sit on top of the row; without this a click on
        # the project name would land on an NSTextField and go nowhere.
        inside = AppKit.NSPointInRect(
            self.convertPoint_fromView_(point, self.superview()), self.bounds()
        )
        return self if inside else None

    def resetCursorRects(self):
        if getattr(self, "_on_click", None) is not None:
            self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.pointingHandCursor())

    def updateTrackingAreas(self):
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        options = (
            AppKit.NSTrackingMouseEnteredAndExited
            | AppKit.NSTrackingActiveInActiveApp
            | AppKit.NSTrackingInVisibleRect
        )
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), options, self, None
            )
        )

    def mouseEntered_(self, event):
        if getattr(self, "_on_click", None) is None:
            return
        self.setHovered_(True)

    def mouseExited_(self, event):
        self.setHovered_(False)

    def setHovered_(self, hovered):
        self._hovered = bool(hovered)
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        if not getattr(self, "_hovered", False):
            return
        AppKit.NSColor.controlAccentColor().colorWithAlphaComponent_(0.14).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(self.bounds(), 6, 2), 6, 6
        ).fill()

    def mouseUp_(self, event):
        handler = getattr(self, "_on_click", None)
        if handler is None:
            return
        # Only if the pointer is still inside — dragging out of a row and
        # releasing should cancel, as it does everywhere else on the system.
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        if AppKit.NSPointInRect(point, self.bounds()):
            handler()


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


def build_session_row(model, on_click=None) -> SessionRowView:
    """Build one row and populate it from `model`.

    `on_click` is called with no arguments when the row is clicked. Pass None
    for a session that can't be focused (no PID yet, or no GUI app hosting it) —
    the row then draws no hover state and shows no pointing-hand cursor, so it
    never looks clickable when it isn't.
    """
    view = ClickableRowView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, ROW_W, ROW_H)
    )
    view._on_click = on_click
    view._hovered = False
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


STATS_H = 64.0


def build_stats_block(stats) -> AppKit.NSView:
    """Today's usage: a header line and three stat tiles.

    Takes a UsageStats rather than pre-formatted text so the number formatting
    stays in usage_stats.py, where it can be tested without a window server.
    """
    from .usage_stats import stat_tiles, stats_caption

    view = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, ROW_W, STATS_H)
    )
    view.setAutoresizingMask_(AppKit.NSViewWidthSizable)

    heading = _label(10, AppKit.NSColor.tertiaryLabelColor(), AppKit.NSFontWeightSemibold)
    heading.setStringValue_("TODAY")
    heading.setFrame_(AppKit.NSMakeRect(14, 43, 80, 13))
    view.addSubview_(heading)

    caption = _label(10, AppKit.NSColor.tertiaryLabelColor())
    caption.setStringValue_(stats_caption(stats))
    caption.setAlignment_(AppKit.NSTextAlignmentRight)
    caption.setFrame_(AppKit.NSMakeRect(ROW_W - 14 - 210, 43, 210, 13))
    caption.setAutoresizingMask_(AppKit.NSViewMinXMargin)
    view.addSubview_(caption)

    tiles = stat_tiles(stats)
    column = (ROW_W - 28) / len(tiles)
    for index, (value, label) in enumerate(tiles):
        x = 14 + index * column

        number = _label(16, AppKit.NSColor.labelColor(), AppKit.NSFontWeightMedium)
        number.setStringValue_(value)
        number.setAlignment_(AppKit.NSTextAlignmentCenter)
        # Monospaced digits so the tiles don't shift as the counts grow.
        number.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
            16, AppKit.NSFontWeightMedium
        ))
        number.setFrame_(AppKit.NSMakeRect(x, 20, column, 20))
        view.addSubview_(number)

        caption_label = _label(10, AppKit.NSColor.tertiaryLabelColor())
        caption_label.setStringValue_(label)
        caption_label.setAlignment_(AppKit.NSTextAlignmentCenter)
        caption_label.setFrame_(AppKit.NSMakeRect(x, 7, column, 13))
        view.addSubview_(caption_label)

    return view


def build_separator(width: float = ROW_W) -> AppKit.NSView:
    box = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, width, 1))
    box.setBoxType_(AppKit.NSBoxSeparator)
    box.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    return box
