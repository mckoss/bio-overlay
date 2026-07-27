"""Tests for the telemetry hub state model and broadcast fan-out."""

import asyncio
from datetime import datetime, timezone

import pytest

from bio_overlay.telemetry import TelemetryHub


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds")


@pytest.fixture
def hub():
    h = TelemetryHub(stale_after_s=0.2)
    h.register_participant("participant-1", "Alice")
    h.register_participant("participant-2", "Bob")
    return h


def test_snapshot_has_both_participants(hub):
    snap = hub.snapshot()
    assert snap["type"] == "state"
    # The session clock starts when the hub is created.
    assert datetime.fromisoformat(snap["sessionStartedAt"]) <= datetime.now().astimezone()
    ids = [p["participantId"] for p in snap["participants"]]
    assert ids == ["participant-1", "participant-2"]
    # Initial state is disconnected with no bpm.
    assert snap["participants"][0]["bpm"] is None
    assert snap["participants"][0]["connected"] is False


def test_snapshot_includes_zones_when_configured():
    h = TelemetryHub()
    h.register_participant("p1", "One", max_hr=160)
    h.register_participant("p2", "Two")
    states = {p["participantId"]: p for p in h.snapshot()["participants"]}
    assert states["p1"]["zones"] == {"maxHr": 160, "divisors": [80, 104, 128, 160]}
    assert states["p2"]["zones"] is None


def test_participants_start_inactive(hub):
    # Unconfigured/untouched participants are inactive so the overlay hides them.
    for p in hub.snapshot()["participants"]:
        assert p["active"] is False


async def test_set_connected_marks_active(hub):
    await hub.set_connected("participant-1", False)
    states = {p["participantId"]: p for p in hub.snapshot()["participants"]}
    assert states["participant-1"]["active"] is True
    # The untouched participant stays inactive (hidden).
    assert states["participant-2"]["active"] is False


async def test_update_measurement_broadcasts(hub):
    received = []

    async def sub(msg):
        received.append(msg)

    hub.subscribe(sub)
    await hub.update_measurement(
        "participant-1", bpm=132, rr_intervals_ms=[742.2], sensor_contact=True
    )

    assert received, "subscriber should have received a snapshot"
    p1 = received[-1]["participants"][0]
    assert p1["bpm"] == 132
    assert p1["rrIntervalsMs"] == [742.2]
    assert p1["connected"] is True
    assert p1["stale"] is False
    assert p1["updatedAt"] is not None


async def test_failing_subscriber_is_dropped(hub):
    async def bad(_msg):
        raise RuntimeError("socket closed")

    hub.subscribe(bad)
    await hub.update_measurement("participant-1", bpm=100)
    # Second update should not raise even though the subscriber errored.
    await hub.update_measurement("participant-1", bpm=101)
    assert bad not in hub._subscribers


def test_seed_history_restores_session_and_sparkline(hub):
    import time

    now_ms = int(time.time() * 1000)
    records = [
        # Old reading: counts toward session stats but is outside the 5-min spark window.
        {
            "t": _iso(now_ms - 10 * 60 * 1000),
            "participantId": "participant-1",
            "bpm": 90,
            "rrIntervalsMs": [666.0],
        },
        # Recent reading: in the sparkline window.
        {
            "t": _iso(now_ms - 30 * 1000),
            "participantId": "participant-1",
            "bpm": 150,
            "rrIntervalsMs": [400.0],
        },
        # Unknown participant is ignored.
        {"t": _iso(now_ms), "participantId": "ghost", "bpm": 200, "rrIntervalsMs": []},
        # bpm == 0 is ignored.
        {"t": _iso(now_ms), "participantId": "participant-1", "bpm": 0, "rrIntervalsMs": []},
    ]
    hub.seed_history(records)

    snap = hub.snapshot()
    # The restored session's clock starts at the first seeded reading.
    started = datetime.fromisoformat(snap["sessionStartedAt"])
    assert abs(started.timestamp() * 1000 - (now_ms - 10 * 60 * 1000)) < 1000

    p1 = snap["participants"][0]
    assert p1["session"]["min"] == 90
    assert p1["session"]["max"] == 150
    assert p1["session"]["avg"] == 120  # (90 + 150) / 2
    assert p1["session"]["count"] == 2
    # Only the recent reading is in the sparkline window.
    assert len(p1["samples"]) == 1
    assert p1["samples"][0][1] == 150


async def test_reset_session_clears_history_keeps_connection(hub):
    await hub.update_measurement("participant-1", bpm=120, rr_intervals_ms=[500.0])
    await hub.update_measurement("participant-1", bpm=130)

    received = []

    async def sub(msg):
        received.append(msg)

    hub.subscribe(sub)
    started_before = hub._session_started_at
    await hub.reset_session()

    assert received, "reset should broadcast the cleared state"
    # The session clock restarts on reset.
    assert hub._session_started_at > started_before
    p1 = received[-1]["participants"][0]
    # History is gone...
    assert p1["session"] == {"min": None, "max": None, "avg": None, "count": 0}
    assert p1["samples"] == []
    # ...but the live connection and latest reading survive.
    assert p1["connected"] is True
    assert p1["active"] is True
    assert p1["bpm"] == 130


async def test_reset_session_then_new_readings_accumulate(hub):
    await hub.update_measurement("participant-1", bpm=120)
    await hub.reset_session()
    await hub.update_measurement("participant-1", bpm=80)

    p1 = hub.snapshot()["participants"][0]
    assert p1["session"] == {"min": 80, "max": 80, "avg": 80, "count": 1}
    assert len(p1["samples"]) == 1


async def test_watchdog_marks_stale(hub):
    received = []

    async def sub(msg):
        received.append(msg)

    hub.subscribe(sub)
    await hub.update_measurement("participant-1", bpm=120)
    hub.start_watchdog()
    try:
        # Wait longer than stale_after_s for the watchdog to flip the flag.
        await asyncio.sleep(0.5)
    finally:
        await hub.stop_watchdog()

    p1 = hub.snapshot()["participants"][0]
    assert p1["stale"] is True
