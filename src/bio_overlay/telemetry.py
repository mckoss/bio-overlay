"""In-memory telemetry model and pub/sub hub.

The hub holds the latest state for each configured participant and notifies
subscribers (e.g. WebSocket clients) whenever state changes. It also runs a
staleness watchdog so the overlay can show a clear "disconnected/stale" state
instead of freezing a misleading BPM on screen.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

# If no fresh measurement arrives within this many seconds, the participant is
# marked stale even if the BLE link still claims to be connected.
DEFAULT_STALE_AFTER_S = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


@dataclass
class ParticipantState:
    """Latest known state for a single monitored participant."""

    participant_id: str
    display_name: str
    bpm: int | None = None
    rr_intervals_ms: list[float] = field(default_factory=list)
    connected: bool = False
    stale: bool = False
    sensor_contact: bool | None = None
    updated_at: str | None = None

    def to_message(self) -> dict:
        """Serialize to the camelCase shape consumed by the overlay client."""
        return {
            "participantId": self.participant_id,
            "displayName": self.display_name,
            "bpm": self.bpm,
            "rrIntervalsMs": self.rr_intervals_ms,
            "connected": self.connected,
            "stale": self.stale,
            "sensorContact": self.sensor_contact,
            "updatedAt": self.updated_at,
        }


Subscriber = Callable[[dict], Awaitable[None]]


class TelemetryHub:
    """Holds participant state and fans out snapshots to subscribers."""

    def __init__(self, stale_after_s: float = DEFAULT_STALE_AFTER_S) -> None:
        self._participants: dict[str, ParticipantState] = {}
        self._subscribers: set[Subscriber] = set()
        self._stale_after_s = stale_after_s
        self._watchdog_task: asyncio.Task | None = None

    # -- registration -----------------------------------------------------

    def register_participant(self, participant_id: str, display_name: str) -> None:
        self._participants.setdefault(
            participant_id,
            ParticipantState(participant_id=participant_id, display_name=display_name),
        )

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
        state.bpm = bpm
        state.rr_intervals_ms = rr_intervals_ms or []
        state.sensor_contact = sensor_contact
        state.connected = True
        state.stale = False
        state.updated_at = _now_iso()
        await self._broadcast()

    async def set_connected(self, participant_id: str, connected: bool) -> None:
        state = self._participants[participant_id]
        state.connected = connected
        if not connected:
            state.stale = True
        state.updated_at = _now_iso()
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
            now = datetime.now(timezone.utc).astimezone()
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


__all__ = ["ParticipantState", "TelemetryHub", "asdict"]
