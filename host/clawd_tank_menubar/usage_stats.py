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
