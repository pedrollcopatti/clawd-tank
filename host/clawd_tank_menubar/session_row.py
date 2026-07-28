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


STATS_H = 96.0

BAR_H = 6.0
BAR_LEFT = 14.0
PERCENT_W = 40.0

# Bars turn amber then red as a window fills, so "nearly out" reads without
# having to parse the number.
BAR_WARN_AT = 75
BAR_CRITICAL_AT = 90


def _bar_color(percent: int):
    if percent >= BAR_CRITICAL_AT:
        return AppKit.NSColor.systemRedColor()
    if percent >= BAR_WARN_AT:
        return AppKit.NSColor.systemOrangeColor()
    return AppKit.NSColor.controlAccentColor()


def _build_meter(view, y: float, window, now: float, stale: bool = False) -> None:
    """One labelled progress bar for a rate-limit window."""
    from .usage_stats import format_countdown

    bar_w = ROW_W - BAR_LEFT * 2 - PERCENT_W - 8

    heading = _label(10, AppKit.NSColor.tertiaryLabelColor(), AppKit.NSFontWeightSemibold)
    heading.setStringValue_(window.label)
    heading.setFrame_(AppKit.NSMakeRect(BAR_LEFT, y + 15, 120, 13))
    view.addSubview_(heading)

    resets = _label(10, AppKit.NSColor.tertiaryLabelColor())
    resets.setStringValue_(f"resets in {format_countdown(window.seconds_until_reset(now))}")
    resets.setAlignment_(AppKit.NSTextAlignmentRight)
    resets.setFrame_(AppKit.NSMakeRect(ROW_W - BAR_LEFT - 180, y + 15, 180, 13))
    resets.setAutoresizingMask_(AppKit.NSViewMinXMargin)
    view.addSubview_(resets)

    track = AppKit.NSBox.alloc().initWithFrame_(
        AppKit.NSMakeRect(BAR_LEFT, y, bar_w, BAR_H)
    )
    track.setBoxType_(AppKit.NSBoxCustom)
    track.setBorderWidth_(0)
    track.setCornerRadius_(BAR_H / 2)
    track.setFillColor_(AppKit.NSColor.quaternaryLabelColor())
    view.addSubview_(track)

    filled = max(0, min(100, window.percent))
    if filled > 0:
        # Never thinner than the bar is tall, so 1% still reads as a mark
        # rather than vanishing into the rounded cap.
        width = max(BAR_H, bar_w * filled / 100.0)
        fill = AppKit.NSBox.alloc().initWithFrame_(
            AppKit.NSMakeRect(BAR_LEFT, y, width, BAR_H)
        )
        fill.setBoxType_(AppKit.NSBoxCustom)
        fill.setBorderWidth_(0)
        fill.setCornerRadius_(BAR_H / 2)
        fill.setFillColor_(_bar_color(filled))
        view.addSubview_(fill)

    percent = _label(11, AppKit.NSColor.labelColor(), AppKit.NSFontWeightMedium)
    # "≥" on a stale reading: inside a live window usage only ever climbs, so
    # the cached figure is a floor, and saying so is more useful than either
    # hiding the bar or pretending the number is current.
    percent.setStringValue_(f"{'≥' if stale else ''}{filled}%")
    percent.setAlignment_(AppKit.NSTextAlignmentRight)
    percent.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
        11, AppKit.NSFontWeightMedium
    ))
    percent.setFrame_(AppKit.NSMakeRect(ROW_W - BAR_LEFT - PERCENT_W, y - 3, PERCENT_W, 14))
    percent.setAutoresizingMask_(AppKit.NSViewMinXMargin)
    view.addSubview_(percent)


def build_stats_block(stats, limits, now: float) -> AppKit.NSView:
    """Rate-limit meters, with today's totals underneath.

    Takes the model objects rather than pre-formatted text so the number
    formatting stays in usage_stats.py, where it's testable without a window
    server.
    """
    from .usage_stats import limits_caption, today_summary

    view = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, ROW_W, STATS_H)
    )
    view.setAutoresizingMask_(AppKit.NSViewWidthSizable)

    live = limits.live_windows(now)
    stale = limits.is_stale(now)
    for index, window in enumerate(live[:2]):
        _build_meter(view, STATS_H - 34 - index * 32, window, now, stale)

    note = limits_caption(limits, now)

    if live:
        # Bottom line shared: the staleness note on the left, today's totals
        # right-aligned. Both truncate rather than overrun into each other.
        today = _label(10, AppKit.NSColor.tertiaryLabelColor())
        # The note, when there is one, owns the left of this row; today's line
        # gets whatever is left. With no note it gets the full width, and the
        # room pays for the "Today ·" label.
        today.setStringValue_(today_summary(stats, prefix=not note))
        today.setAlignment_(AppKit.NSTextAlignmentRight)
        indent = 100 if note else 0
        today.setFrame_(
            AppKit.NSMakeRect(BAR_LEFT + indent, 7, ROW_W - BAR_LEFT * 2 - indent, 13)
        )
        today.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        view.addSubview_(today)

        if note:
            caption = _label(10, AppKit.NSColor.tertiaryLabelColor())
            caption.setStringValue_(note)
            caption.setFrame_(AppKit.NSMakeRect(BAR_LEFT, 7, 96, 13))
            view.addSubview_(caption)
        return view

    # No usable window: say why, and still show today's totals, which are
    # computed here and always true.
    caption = _label(11, AppKit.NSColor.secondaryLabelColor())
    caption.setStringValue_(note or "Limits unavailable")
    caption.setFrame_(AppKit.NSMakeRect(BAR_LEFT, STATS_H / 2 - 4, ROW_W - BAR_LEFT * 2, 15))
    caption.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    view.addSubview_(caption)

    today = _label(10, AppKit.NSColor.tertiaryLabelColor())
    today.setStringValue_(today_summary(stats, prefix=True))
    today.setFrame_(AppKit.NSMakeRect(BAR_LEFT, STATS_H / 2 - 24, ROW_W - BAR_LEFT * 2, 13))
    today.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    view.addSubview_(today)

    return view


def build_separator(width: float = ROW_W) -> AppKit.NSView:
    box = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, width, 1))
    box.setBoxType_(AppKit.NSBoxSeparator)
    box.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    return box
