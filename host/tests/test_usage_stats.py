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
