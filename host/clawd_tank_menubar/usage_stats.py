# host/clawd_tank_menubar/usage_stats.py
"""Today's Claude Code usage, read from the session transcripts.

Claude Code appends every turn to ~/.claude/projects/<project>/<session>.jsonl,
and assistant lines carry the model's own token accounting. Nothing else on disk
records usage, so this is the source.

Scanning is cheap because it's doubly filtered: only files touched since the
cutoff are opened at all, and within them only lines containing "usage" are
parsed. A typical day is a few megabytes and tens of milliseconds — fast enough
to run on the main thread when the popover opens.

No AppKit, so this is testable without a window server.
"""

import datetime
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clawd-tank.usage")

TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"

# Claude Code caches the account's rate-limit utilisation here after it asks the
# API for it. It does NOT refresh on a timer — the field can sit untouched for
# days while the rest of the file is rewritten constantly — so anything read
# from it has to be checked against its own reset time before being believed.
LIMITS_PATH = Path.home() / ".claude.json"

# Which windows to surface, and what to call them. `limits[]` in the same blob
# carries per-model scoped windows too; those answer a different question.
_LIMIT_WINDOWS = (("five_hour", "SESSION"), ("seven_day", "WEEK"))

# Past this, a reading is shown as a floor (">=") rather than a exact figure.
STALE_AFTER_SECS = 1800.0

# Cheap prefilter before json.loads. Transcripts are mostly user turns and
# attachments, which are the bulk of the bytes and carry no accounting.
_USAGE_MARKER = '"usage"'


@dataclass(frozen=True)
class UsageStats:
    sessions: int = 0
    projects: int = 0
    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        """Everything the model read, cached or not.

        Cache reads dominate by two orders of magnitude, so reporting only
        `input_tokens` would suggest almost nothing was sent.
        """
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def is_empty(self) -> bool:
        return self.messages == 0


@dataclass(frozen=True)
class LimitWindow:
    """One rate-limit window: how much is used and when it resets."""
    key: str
    label: str
    percent: int
    resets_at: float

    def seconds_until_reset(self, now: Optional[float] = None) -> float:
        return self.resets_at - (now if now is not None else _now())

    def is_live(self, now: Optional[float] = None) -> bool:
        """False once the window has rolled over.

        A percentage from a window that already reset says nothing about the
        current one, so it must not be shown as if it did.
        """
        return self.seconds_until_reset(now) > 0


@dataclass(frozen=True)
class UsageLimits:
    windows: tuple[LimitWindow, ...] = ()
    fetched_at: Optional[float] = None

    def live_windows(self, now: Optional[float] = None) -> list[LimitWindow]:
        return [w for w in self.windows if w.is_live(now)]

    def is_usable(self, now: Optional[float] = None) -> bool:
        return bool(self.live_windows(now))

    def is_stale(self, now: Optional[float] = None) -> bool:
        """True when the reading predates the window it describes by enough
        that usage has probably moved. Inside a live window usage only climbs,
        so a stale percentage is a floor rather than a wrong number."""
        if self.fetched_at is None:
            return False
        return (now if now is not None else _now()) - self.fetched_at > STALE_AFTER_SECS


def _now() -> float:
    import time
    return time.time()


def read_usage_limits(path: Path = LIMITS_PATH) -> UsageLimits:
    """Read the cached rate-limit utilisation. Never raises."""
    try:
        blob = json.loads(path.read_text())
    except (OSError, ValueError):
        return UsageLimits()

    cached = blob.get("cachedUsageUtilization") or {}
    utilization = cached.get("utilization") or {}

    fetched_ms = cached.get("fetchedAtMs")
    fetched_at = fetched_ms / 1000 if isinstance(fetched_ms, (int, float)) else None

    windows = []
    for key, label in _LIMIT_WINDOWS:
        window = utilization.get(key)
        if not isinstance(window, dict):
            continue
        percent = window.get("utilization")
        resets_raw = window.get("resets_at")
        if not isinstance(percent, (int, float)) or not resets_raw:
            continue
        try:
            resets_at = datetime.datetime.fromisoformat(
                str(resets_raw).replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            continue
        windows.append(
            LimitWindow(key=key, label=label, percent=int(percent), resets_at=resets_at)
        )

    return UsageLimits(windows=tuple(windows), fetched_at=fetched_at)


def format_countdown(seconds: float) -> str:
    """How long until a window resets: 12m, 2h 14m, 3d 4h."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "under a minute"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def format_age(seconds: float) -> str:
    """How long ago the limits were fetched: 4m, 3h, 4d."""
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def limits_caption(limits: UsageLimits, now: Optional[float] = None) -> str:
    """The line under the bars, explaining how much to trust them."""
    now = now if now is not None else _now()
    if not limits.windows:
        return "Run /usage in Claude Code to see your limits"
    if not limits.is_usable(now):
        return "Limits expired — run /usage in Claude Code to refresh"
    if not limits.is_stale(now) or limits.fetched_at is None:
        return ""
    return f"Measured {format_age(now - limits.fetched_at)} ago"


def start_of_today() -> float:
    """Local midnight, as an epoch timestamp."""
    now = datetime.datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _entry_timestamp(entry: dict) -> Optional[float]:
    raw = entry.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        ).timestamp()
    except (ValueError, AttributeError):
        return None


def collect_usage_stats(
    root: Path = TRANSCRIPT_ROOT,
    since: Optional[float] = None,
) -> UsageStats:
    """Sum usage across every transcript with activity since `since`.

    Never raises: a transcript being appended to while we read it, or one with a
    truncated last line, must not take down the popover.
    """
    if since is None:
        since = start_of_today()

    try:
        candidates = [
            path for path in root.rglob("*.jsonl")
            if path.stat().st_mtime >= since
        ]
    except OSError:
        return UsageStats()

    messages = 0
    tokens = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    sessions: set[str] = set()
    projects: set[str] = set()

    for path in candidates:
        contributed = False
        try:
            with path.open(errors="replace") as handle:
                for line in handle:
                    if _USAGE_MARKER not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except (ValueError, TypeError):
                        continue  # a line still being written
                    if entry.get("type") != "assistant":
                        continue
                    when = _entry_timestamp(entry)
                    if when is None or when < since:
                        continue

                    usage = entry.get("message", {}).get("usage") or {}
                    tokens["in"] += usage.get("input_tokens", 0) or 0
                    tokens["out"] += usage.get("output_tokens", 0) or 0
                    tokens["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
                    tokens["cache_write"] += (
                        usage.get("cache_creation_input_tokens", 0) or 0
                    )
                    messages += 1
                    contributed = True
        except OSError:
            continue

        if contributed:
            # One file is one session. Projects come from the transcript's
            # directory rather than each entry's cwd: cwd follows the shell
            # around, so a single session that cd'd into two subdirectories
            # would otherwise count as two projects.
            sessions.add(str(path))
            projects.add(path.parent.name)

    return UsageStats(
        sessions=len(sessions),
        projects=len(projects),
        messages=messages,
        input_tokens=tokens["in"],
        output_tokens=tokens["out"],
        cache_read_tokens=tokens["cache_read"],
        cache_write_tokens=tokens["cache_write"],
    )


def format_count(value: int) -> str:
    """Compact number for a stat tile: 788, 9.1k, 911k, 3.3M, 214M."""
    if value < 1_000:
        return str(value)
    if value < 10_000:
        return f"{value / 1_000:.1f}k"
    if value < 1_000_000:
        return f"{value / 1_000:.0f}k"
    if value < 10_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{value / 1_000_000:.0f}M"


def stat_tiles(stats: UsageStats) -> list[tuple[str, str]]:
    """(value, label) pairs for the popover's stats strip."""
    return [
        (format_count(stats.sessions), "sessions"),
        (format_count(stats.messages), "messages"),
        (format_count(stats.output_tokens), "tokens out"),
    ]


def stats_caption(stats: UsageStats) -> str:
    """One line of context under the tiles."""
    if stats.is_empty:
        return "No activity yet today"
    projects = f"{stats.projects} project" + ("s" if stats.projects != 1 else "")
    return f"{projects} · {format_count(stats.total_input_tokens)} tokens in"


def today_summary(stats: UsageStats, prefix: bool = False) -> str:
    """The compact today line under the limit bars.

    `prefix` adds the "Today ·" label. It's dropped when the line shares its row
    with the staleness note, where the width doesn't stretch to it.
    """
    head = "Today · " if prefix else ""
    if stats.is_empty:
        return f"{head}nothing yet"
    sessions = f"{stats.sessions} session" + ("s" if stats.sessions != 1 else "")
    return (
        f"{head}{sessions} · {format_count(stats.messages)} msgs"
        f" · {format_count(stats.output_tokens)} out"
    )
