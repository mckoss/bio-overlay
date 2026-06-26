"""Tests for the telemetry hub state model and broadcast fan-out."""

import asyncio

import pytest

from bio_overlay.telemetry import TelemetryHub


@pytest.fixture
def hub():
    h = TelemetryHub(stale_after_s=0.2)
    h.register_participant("participant-1", "Alice")
    h.register_participant("participant-2", "Bob")
    return h


def test_snapshot_has_both_participants(hub):
    snap = hub.snapshot()
    assert snap["type"] == "state"
    ids = [p["participantId"] for p in snap["participants"]]
    assert ids == ["participant-1", "participant-2"]
    # Initial state is disconnected with no bpm.
    assert snap["participants"][0]["bpm"] is None
    assert snap["participants"][0]["connected"] is False


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
