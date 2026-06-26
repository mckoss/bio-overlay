"""In-memory telemetry model and pub/sub hub.

The hub is the source of truth for a training session. It holds, per
participant, the latest state plus the BPM history needed to render the
overlay: a rolling window of recent samples (for the sparkline) and
whole-session min/max/avg aggregates. Because the collector runs in this
process regardless of whether any overlay is connected, history accrues
continuously, and a reloaded overlay (or OBS scene reload) is sent the full
history on connect — so the sparkline and stats survive client reloads.

History lives only in memory for the lifetime of the process (one training
session); nothing is written to disk.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .respiration import estimate_respiration

# If no fresh measurement arrives within this many seconds, the participant is
# marked stale even if the BLE link still claims to be connected.
DEFAULT_STALE_AFTER_S = 5.0

# How much recent BPM history to retain for the sparkline window.
DEFAULT_HISTORY_WINDOW_S = 5 * 60

# How much recent RR history to retain for the (experimental) respiration estimate.
DEFAULT_RESP_WINDOW_S = 60


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


@dataclass
class ParticipantState:
    """Latest known state plus session history for one monitored participant."""

    participant_id: str
    display_name: str
    device_id: str | None = None
    bpm: int | None = None
    rr_intervals_ms: list[float] = field(default_factory=list)
    connected: bool = False
    stale: bool = False
    sensor_contact: bool | None = None
    updated_at: str | None = None

    # Rolling sparkline window: (epoch_ms, bpm) pairs, oldest first.
    samples: deque = field(default_factory=deque)
    # Whole-session aggregates (valid, non-zero readings only).
    session_min: int | None = None
    session_max: int | None = None
    session_sum: int = 0
    session_count: int = 0
    # Rolling RR window for the experimental respiration estimate: (epoch_ms, rr_ms).
    rr_window: deque = field(default_factory=deque)
    respiration_brpm: float | None = None
    respiration_confidence: float | None = None

    def record(self, bpm: int, at_ms: int, window_ms: int) -> None:
        """Append a valid reading and update the rolling window + session stats."""
        self.samples.append((at_ms, bpm))
        cutoff = at_ms - window_ms
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()
        self.session_min = bpm if self.session_min is None else min(self.session_min, bpm)
        self.session_max = bpm if self.session_max is None else max(self.session_max, bpm)
        self.session_sum += bpm
        self.session_count += 1

    def record_rr(self, rr_ms: list[float], at_ms: int, window_ms: int) -> None:
        """Append RR intervals to the rolling window and refresh the respiration estimate."""
        for rr in rr_ms:
            self.rr_window.append((at_ms, rr))
        cutoff = at_ms - window_ms
        while self.rr_window and self.rr_window[0][0] < cutoff:
            self.rr_window.popleft()
        est = estimate_respiration([rr for _, rr in self.rr_window])
        if est is not None:
            self.respiration_brpm = est.breaths_per_min
            self.respiration_confidence = est.confidence

    def to_message(self) -> dict:
        """Serialize to the camelCase shape consumed by the overlay client."""
        avg = round(self.session_sum / self.session_count) if self.session_count else None
        return {
            "participantId": self.participant_id,
            "displayName": self.display_name,
            "bpm": self.bpm,
            "rrIntervalsMs": self.rr_intervals_ms,
            "connected": self.connected,
            "stale": self.stale,
            "sensorContact": self.sensor_contact,
            "updatedAt": self.updated_at,
            # Full session history so the overlay is a stateless renderer.
            "samples": [[t, b] for (t, b) in self.samples],
            "session": {
                "min": self.session_min,
                "max": self.session_max,
                "avg": avg,
                "count": self.session_count,
            },
            # Experimental: estimated breaths/min with a 0..1 confidence, or null.
            "respiration": (
                {
                    "breathsPerMin": self.respiration_brpm,
                    "confidence": self.respiration_confidence,
                }
                if self.respiration_brpm is not None
                else None
            ),
        }


Subscriber = Callable[[dict], Awaitable[None]]
# Called for each valid (non-zero) reading: (state, bpm, rr_intervals_ms, at).
Recorder = Callable[["ParticipantState", int, list, datetime], None]


class TelemetryHub:
    """Holds participant state and fans out snapshots to subscribers."""

    def __init__(
        self,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        history_window_s: float = DEFAULT_HISTORY_WINDOW_S,
        resp_window_s: float = DEFAULT_RESP_WINDOW_S,
    ) -> None:
        self._participants: dict[str, ParticipantState] = {}
        self._subscribers: set[Subscriber] = set()
        self._stale_after_s = stale_after_s
        self._history_window_ms = int(history_window_s * 1000)
        self._resp_window_ms = int(resp_window_s * 1000)
        self._watchdog_task: asyncio.Task | None = None
        self._recorder: Recorder | None = None

    # -- registration -----------------------------------------------------

    def register_participant(
        self, participant_id: str, display_name: str, device_id: str | None = None
    ) -> None:
        self._participants.setdefault(
            participant_id,
            ParticipantState(
                participant_id=participant_id,
                display_name=display_name,
                device_id=device_id,
            ),
        )

    def set_recorder(self, recorder: Recorder | None) -> None:
        """Register a sink called for each valid reading (e.g. a history file)."""
        self._recorder = recorder

    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.add(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        self._subscribers.discard(callback)

    # -- snapshots --------------------------------------------------------

    def snapshot(self) -> dict:
        """Full state for all participants, in registration order."""
        return {
            "type": "state",
            "participants": [p.to_message() for p in self._participants.values()],
        }

    # -- updates ----------------------------------------------------------

    async def update_measurement(
        self,
        participant_id: str,
        *,
        bpm: int,
        rr_intervals_ms: list[float] | None = None,
        sensor_contact: bool | None = None,
    ) -> None:
        state = self._participants[participant_id]
        now = _now()
        state.bpm = bpm
        state.rr_intervals_ms = rr_intervals_ms or []
        state.sensor_contact = sensor_contact
        state.connected = True
        state.stale = False
        state.updated_at = now.isoformat(timespec="milliseconds")
        # bpm == 0 is the H10 reporting "no heartbeat detected" (loose contact),
        # not a real reading — keep it out of the sparkline and session stats.
        if bpm > 0:
            now_ms = int(now.timestamp() * 1000)
            state.record(bpm, now_ms, self._history_window_ms)
            if state.rr_intervals_ms:
                state.record_rr(state.rr_intervals_ms, now_ms, self._resp_window_ms)
            if self._recorder is not None:
                self._recorder(state, bpm, state.rr_intervals_ms, now)
        await self._broadcast()

    async def set_connected(self, participant_id: str, connected: bool) -> None:
        state = self._participants[participant_id]
        state.connected = connected
        if not connected:
            state.stale = True
        state.updated_at = _now().isoformat(timespec="milliseconds")
        await self._broadcast()

    # -- watchdog ---------------------------------------------------------

    def start_watchdog(self) -> None:
        if self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop_watchdog(self) -> None:
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
            self._watchdog_task = None

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(self._stale_after_s / 2)
            now = _now()
            changed = False
            for state in self._participants.values():
                if state.stale or state.updated_at is None:
                    continue
                last = datetime.fromisoformat(state.updated_at)
                if (now - last).total_seconds() > self._stale_after_s:
                    state.stale = True
                    changed = True
            if changed:
                await self._broadcast()

    # -- internal ---------------------------------------------------------

    async def _broadcast(self) -> None:
        if not self._subscribers:
            return
        message = self.snapshot()
        results = await asyncio.gather(
            *(cb(message) for cb in list(self._subscribers)),
            return_exceptions=True,
        )
        # Drop subscribers that errored (e.g. closed sockets).
        for cb, result in zip(list(self._subscribers), results):
            if isinstance(result, Exception):
                self._subscribers.discard(cb)


__all__ = ["ParticipantState", "TelemetryHub"]
