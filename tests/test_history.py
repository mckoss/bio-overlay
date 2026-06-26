"""Tests for the daily JSON Lines history writer (session header + offsets)."""

import json
from datetime import datetime, timedelta, timezone

from bio_overlay.config import ParticipantConfig
from bio_overlay.history import DailyHistoryWriter, read_records
from bio_overlay.telemetry import ParticipantState


def _state(pid="mike-koss"):
    return ParticipantState(participant_id=pid, display_name="Mike", device_id="16CD9E3C")


def _participants(*ids):
    return [ParticipantConfig(id=i, display_name=i.title(), device_id=i.upper()) for i in ids]


def _at(second=3, ms=123000):
    return datetime(2026, 6, 26, 10, 15, second, ms, tzinfo=timezone.utc)


def _lines(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


async def test_session_header_then_offset_lines(tmp_path):
    w = DailyHistoryWriter(tmp_path)
    w.start_session(_participants("mike-koss"))
    w.record(_state(), 78, [776.4], _at(second=3))
    w.record(_state(), 80, [751.0], _at(second=9))  # +6s -> new line
    await w.close()

    recs = _lines(tmp_path / "2026-06-26.jsonl")
    assert recs[0]["session"] == "2026-06-26T10:15:03.123+00:00"
    assert recs[0]["participants"] == [
        {"id": "mike-koss", "name": "Mike-Koss", "deviceId": "MIKE-KOSS"}
    ]
    # Data lines use seconds-offset + participant index, not repeated metadata.
    assert recs[1] == {"s": 0, "p": 0, "bpm": 78, "rr": [776.4]}
    assert recs[2] == {"s": 6, "p": 0, "bpm": 80, "rr": [751.0]}


async def test_read_records_resolves_to_absolute(tmp_path):
    w = DailyHistoryWriter(tmp_path)
    w.start_session(_participants("alice", "bob"))
    w.record(_state("alice"), 70, [800.0], _at(second=0, ms=0))
    w.record(_state("bob"), 90, [600.0], _at(second=0, ms=0))
    await w.close()

    recs = read_records(tmp_path, "2026-06-26")
    by_p = {r["p"]: r for r in recs}
    assert set(by_p) == {"alice", "bob"}
    assert by_p["alice"]["bpm"] == 70
    assert by_p["bob"]["bpm"] == 90
    assert by_p["alice"]["t"].startswith("2026-06-26T10:15:00")


async def test_throttle_batches_rr(tmp_path):
    w = DailyHistoryWriter(tmp_path, min_interval_s=5.0)
    w.start_session(_participants("mike-koss"))
    base = _at(second=0, ms=0)
    for i in range(12):  # 12 readings over 11s, 1/s
        w.record(_state(), 70 + i, [800.0], base + timedelta(seconds=i))
    await w.close()

    data = [r for r in _lines(tmp_path / "2026-06-26.jsonl") if "s" in r]
    assert len(data) <= 4  # ~1 line per 5s, plus close() remainder
    # No RR dropped: 12 readings -> 12 batched rr values across the lines.
    assert sum(len(r.get("rr", [])) for r in data) == 12


async def test_rollover_writes_new_header(tmp_path):
    w = DailyHistoryWriter(tmp_path)
    w.start_session(_participants("mike-koss"))
    w.record(_state(), 80, [], datetime(2026, 6, 26, 23, 59, 59, tzinfo=timezone.utc))
    w.record(_state(), 81, [], datetime(2026, 6, 27, 0, 0, 1, tzinfo=timezone.utc))
    await w.close()

    d1 = _lines(tmp_path / "2026-06-26.jsonl")
    d2 = _lines(tmp_path / "2026-06-27.jsonl")
    assert "session" in d1[0] and "session" in d2[0]  # each file self-describes
    assert d1[1]["bpm"] == 80 and d2[1]["bpm"] == 81


def test_read_records_missing_returns_empty(tmp_path):
    assert read_records(tmp_path, "2026-01-01") == []


# -- session listing / detail -------------------------------------------------


async def _two_session_file(tmp_path):
    """Write a file with two sessions for the same day."""
    w = DailyHistoryWriter(tmp_path)
    w.start_session(_participants("mike-koss", "debbie-koss"))
    w.record(_state("mike-koss"), 60, [1000.0], _at(second=0, ms=0))
    w.record(_state("mike-koss"), 70, [900.0], _at(second=30, ms=0))
    w.record(_state("debbie-koss"), 80, [], _at(second=30, ms=0))
    await w.close()
    # Second session same day (e.g. a restart).
    w2 = DailyHistoryWriter(tmp_path)
    w2.start_session(_participants("mike-koss"))
    w2.record(_state("mike-koss"), 66, [], datetime(2026, 6, 26, 11, 0, 0, tzinfo=timezone.utc))
    await w2.close()


async def test_list_sessions(tmp_path):
    from bio_overlay.history import list_sessions

    await _two_session_file(tmp_path)
    sessions = list_sessions(tmp_path)
    assert len(sessions) == 2
    # Newest first.
    assert sessions[0]["startedAt"] > sessions[1]["startedAt"]
    first = next(s for s in sessions if s["id"] == "2026-06-26__0")
    assert first["durationS"] == 30
    assert set(first["participants"]) == {"Mike-Koss", "Debbie-Koss"}


async def test_load_session_detail(tmp_path):
    from bio_overlay.history import load_session

    await _two_session_file(tmp_path)
    detail = load_session(tmp_path, "2026-06-26__0")
    assert detail is not None
    assert detail["durationS"] == 30
    by_name = {p["name"]: p for p in detail["participants"]}
    mike = by_name["Mike-Koss"]
    assert mike["stats"] == {"min": 60, "max": 70, "avg": 65, "count": 2}
    assert mike["points"] == [[0, 60], [30, 70]]


def test_load_session_bad_id(tmp_path):
    from bio_overlay.history import load_session

    assert load_session(tmp_path, "nope") is None
    assert load_session(tmp_path, "2026-06-26__9") is None
