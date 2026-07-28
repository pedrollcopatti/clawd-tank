"""Tests for reading today's Claude Code usage out of the transcripts."""

import json

import pytest

from clawd_tank_menubar.usage_stats import (
    UsageStats,
    collect_usage_stats,
    format_count,
    stat_tiles,
    stats_caption,
)

CUTOFF = 1_700_000_000.0
TODAY = "2023-11-14T22:20:00.000Z"      # just after CUTOFF
YESTERDAY = "2023-11-13T22:20:00.000Z"  # well before it


def assistant(ts=TODAY, out=100, cache_read=1000, cache_write=50, inp=5):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        },
    }


@pytest.fixture
def transcripts(tmp_path):
    """Write transcript files under <root>/<project>/<session>.jsonl."""
    def write(project, session, entries):
        directory = tmp_path / project
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{session}.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        return path
    write.root = tmp_path
    return write


def collect(root):
    return collect_usage_stats(root=root, since=CUTOFF)


# --- Collection ---


def test_no_transcripts_at_all(tmp_path):
    assert collect(tmp_path) == UsageStats()


def test_missing_root_directory_is_not_an_error(tmp_path):
    assert collect(tmp_path / "nope") == UsageStats()


def test_sums_tokens_across_messages(transcripts):
    transcripts("proj", "s1", [assistant(out=100), assistant(out=250)])
    stats = collect(transcripts.root)

    assert stats.messages == 2
    assert stats.output_tokens == 350
    assert stats.cache_read_tokens == 2000
    assert stats.cache_write_tokens == 100
    assert stats.input_tokens == 10


def test_total_input_includes_cache(transcripts):
    """Cache reads dominate by orders of magnitude; reporting input_tokens
    alone would suggest almost nothing was sent."""
    transcripts("proj", "s1", [assistant(inp=5, cache_read=1000, cache_write=50)])
    assert collect(transcripts.root).total_input_tokens == 1055


def test_entries_from_before_the_cutoff_are_ignored(transcripts):
    transcripts("proj", "s1", [assistant(ts=YESTERDAY, out=999), assistant(out=10)])
    stats = collect(transcripts.root)
    assert stats.messages == 1
    assert stats.output_tokens == 10


def test_a_file_with_only_old_entries_counts_as_no_session(transcripts):
    """It gets opened because its mtime is fresh, but it contributed nothing."""
    transcripts("proj", "s1", [assistant(ts=YESTERDAY)])
    stats = collect(transcripts.root)
    assert stats.sessions == 0
    assert stats.projects == 0
    assert stats.is_empty


def test_user_and_other_line_types_are_ignored(transcripts):
    transcripts("proj", "s1", [
        {"type": "user", "timestamp": TODAY, "message": {"usage": {"output_tokens": 9}}},
        {"type": "attachment", "timestamp": TODAY},
        assistant(out=7),
    ])
    stats = collect(transcripts.root)
    assert stats.messages == 1
    assert stats.output_tokens == 7


def test_entry_without_a_timestamp_is_skipped(transcripts):
    entry = assistant()
    del entry["timestamp"]
    transcripts("proj", "s1", [entry, assistant(out=3)])
    assert collect(transcripts.root).messages == 1


def test_a_half_written_line_does_not_break_the_scan(transcripts, tmp_path):
    """The file being appended to is usually the session doing the reading."""
    path = transcripts("proj", "s1", [assistant(out=5)])
    with path.open("a") as handle:
        handle.write('{"type": "assistant", "usage": {"output_to')

    stats = collect(tmp_path)
    assert stats.messages == 1
    assert stats.output_tokens == 5


# --- Session and project counting ---


def test_each_file_is_a_session(transcripts):
    transcripts("proj", "s1", [assistant()])
    transcripts("proj", "s2", [assistant()])
    stats = collect(transcripts.root)
    assert stats.sessions == 2
    assert stats.projects == 1


def test_projects_come_from_the_directory_not_the_cwd(transcripts):
    """cwd follows the shell around, so a session that cd'd into two
    subdirectories would otherwise be counted as two projects."""
    a, b = assistant(), assistant()
    a["cwd"] = "/code/thing/host"
    b["cwd"] = "/code/thing/simulator"
    transcripts("thing", "s1", [a, b])

    assert collect(transcripts.root).projects == 1


def test_counts_across_several_projects(transcripts):
    transcripts("alpha", "s1", [assistant()])
    transcripts("beta", "s2", [assistant()])
    transcripts("beta", "s3", [assistant()])
    stats = collect(transcripts.root)
    assert (stats.projects, stats.sessions) == (2, 3)


# --- Formatting ---


@pytest.mark.parametrize("value,expected", [
    (0, "0"),
    (7, "7"),
    (788, "788"),
    (999, "999"),
    (1_000, "1.0k"),
    (9_100, "9.1k"),
    (10_000, "10k"),
    (911_737, "912k"),
    (999_999, "1000k"),
    (1_000_000, "1.0M"),
    (3_270_402, "3.3M"),
    (214_000_000, "214M"),
])
def test_format_count(value, expected):
    assert format_count(value) == expected


def test_stat_tiles_are_three_labelled_numbers():
    tiles = stat_tiles(UsageStats(sessions=6, messages=795, output_tokens=934_625))
    assert tiles == [("6", "sessions"), ("795", "messages"), ("935k", "tokens out")]


def test_caption_when_nothing_happened_yet():
    assert stats_caption(UsageStats()) == "No activity yet today"


@pytest.mark.parametrize("projects,expected", [
    (1, "1 project · 1.1k tokens in"),
    (3, "3 projects · 1.1k tokens in"),
])
def test_caption_pluralises_projects(projects, expected):
    stats = UsageStats(
        projects=projects, messages=1, input_tokens=50, cache_read_tokens=1_000,
        cache_write_tokens=25,
    )
    assert stats_caption(stats) == expected


# --- Rate-limit windows ---


def _limits_file(tmp_path, five_hour_pct, five_hour_reset, fetched_at, seven=None):
    import datetime as dt
    path = tmp_path / ".claude.json"
    util = {
        "five_hour": {
            "utilization": five_hour_pct,
            "resets_at": dt.datetime.fromtimestamp(
                five_hour_reset, dt.timezone.utc
            ).isoformat(),
        },
    }
    if seven is not None:
        pct, reset = seven
        util["seven_day"] = {
            "utilization": pct,
            "resets_at": dt.datetime.fromtimestamp(reset, dt.timezone.utc).isoformat(),
        }
    path.write_text(json.dumps({
        "cachedUsageUtilization": {
            "fetchedAtMs": fetched_at * 1000,
            "utilization": util,
        }
    }))
    return path


def test_no_limits_file(tmp_path):
    from clawd_tank_menubar.usage_stats import read_usage_limits
    limits = read_usage_limits(tmp_path / "nope.json")
    assert limits.windows == ()
    assert not limits.is_usable(CUTOFF)


def test_malformed_limits_file(tmp_path):
    from clawd_tank_menubar.usage_stats import read_usage_limits
    path = tmp_path / ".claude.json"
    path.write_text("{{{ not json")
    assert read_usage_limits(path).windows == ()


def test_reads_both_windows(tmp_path):
    from clawd_tank_menubar.usage_stats import read_usage_limits
    path = _limits_file(tmp_path, 63, CUTOFF + 3600, CUTOFF - 60,
                        seven=(32, CUTOFF + 86400))
    limits = read_usage_limits(path)

    assert [w.label for w in limits.windows] == ["SESSION", "WEEK"]
    assert [w.percent for w in limits.windows] == [63, 32]
    assert limits.is_usable(CUTOFF)


def test_expired_window_is_not_live(tmp_path):
    """A percentage from a window that already reset says nothing about the
    current one, so it must never be shown as if it did."""
    from clawd_tank_menubar.usage_stats import read_usage_limits
    path = _limits_file(tmp_path, 5, CUTOFF - 3600, CUTOFF - 7200)
    limits = read_usage_limits(path)

    assert limits.windows[0].percent == 5
    assert not limits.windows[0].is_live(CUTOFF)
    assert limits.live_windows(CUTOFF) == []
    assert not limits.is_usable(CUTOFF)


def test_staleness_is_about_the_fetch_not_the_window(tmp_path):
    from clawd_tank_menubar.usage_stats import read_usage_limits
    fresh = read_usage_limits(_limits_file(tmp_path, 63, CUTOFF + 3600, CUTOFF - 60))
    assert not fresh.is_stale(CUTOFF)

    old = read_usage_limits(
        _limits_file(tmp_path, 63, CUTOFF + 3600, CUTOFF - 7200)
    )
    assert old.is_stale(CUTOFF)
    assert old.is_usable(CUTOFF)  # still worth showing, as a floor


@pytest.mark.parametrize("seconds,expected", [
    (30, "under a minute"),
    (12 * 60, "12m"),
    (2 * 3600 + 14 * 60, "2h 14m"),
    (3 * 86400 + 4 * 3600, "3d 4h"),
    (-100, "under a minute"),
])
def test_format_countdown(seconds, expected):
    from clawd_tank_menubar.usage_stats import format_countdown
    assert format_countdown(seconds) == expected


def test_caption_when_there_are_no_limits_at_all():
    from clawd_tank_menubar.usage_stats import UsageLimits, limits_caption
    assert "Run /usage" in limits_caption(UsageLimits(), CUTOFF)


def test_caption_when_the_window_expired(tmp_path):
    from clawd_tank_menubar.usage_stats import limits_caption, read_usage_limits
    limits = read_usage_limits(_limits_file(tmp_path, 5, CUTOFF - 10, CUTOFF - 7200))
    assert "expired" in limits_caption(limits, CUTOFF)


def test_no_caption_when_the_reading_is_current(tmp_path):
    from clawd_tank_menubar.usage_stats import limits_caption, read_usage_limits
    limits = read_usage_limits(_limits_file(tmp_path, 63, CUTOFF + 3600, CUTOFF - 60))
    assert limits_caption(limits, CUTOFF) == ""
