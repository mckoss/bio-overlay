"""Tests for the daily on-disk history writer."""

import json
from datetime import datetime, timezone

from bio_overlay.history import DailyHistoryWriter
from bio_overlay.telemetry import ParticipantState


def _state():
    return ParticipantState(
        participant_id="mike-koss", display_name="Mike", device_id="16CD9E3C"
    )


def _at(day="2026-06-26", hour=10, minute=15, second=3):
    return datetime(2026, 6, 26, hour, minute, second, 123000, tzinfo=timezone.utc)


def test_writes_daily_file_named_by_date(tmp_path):
    w = DailyHistoryWriter(tmp_path)
    w.record(_state(), 78, [776.4], _at())
    w.flush()

    path = tmp_path / "2026-06-26.json"
    assert path.exists()
    records = json.loads(path.read_text())
    assert len(records) == 1
    rec = records[0]
    assert rec["participantId"] == "mike-koss"
    assert rec["deviceId"] == "16CD9E3C"
    assert rec["bpm"] == 78
    assert rec["rrIntervalsMs"] == [776.4]
    assert rec["t"].startswith("2026-06-26T10:15:03")


def test_flush_is_noop_when_not_dirty(tmp_path):
    w = DailyHistoryWriter(tmp_path)
    w.flush()  # nothing recorded yet
    assert list(tmp_path.iterdir()) == []


def test_appends_to_existing_file_for_same_day(tmp_path):
    # First writer session.
    w1 = DailyHistoryWriter(tmp_path)
    w1.record(_state(), 70, [], _at(second=1))
    w1.flush()

    # Second session same day (e.g. server restart) should append, not clobber.
    w2 = DailyHistoryWriter(tmp_path)
    w2.record(_state(), 72, [], _at(second=2))
    w2.flush()

    records = json.loads((tmp_path / "2026-06-26.json").read_text())
    assert [r["bpm"] for r in records] == [70, 72]


def test_rolls_over_to_new_file_at_midnight(tmp_path):
    w = DailyHistoryWriter(tmp_path)
    w.record(_state(), 80, [], datetime(2026, 6, 26, 23, 59, 59, tzinfo=timezone.utc))
    w.record(_state(), 81, [], datetime(2026, 6, 27, 0, 0, 1, tzinfo=timezone.utc))
    w.flush()

    day1 = json.loads((tmp_path / "2026-06-26.json").read_text())
    day2 = json.loads((tmp_path / "2026-06-27.json").read_text())
    assert [r["bpm"] for r in day1] == [80]
    assert [r["bpm"] for r in day2] == [81]
