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
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .respiration import estimate_respiration
from .zones import N_ZONE_BUCKETS, zone_index, zones_for

logger = logging.getLogger(__name__)

# If no fresh measurement arrives within this many seconds, the participant is
# marked stale even if the BLE link still claims to be connected.
DEFAULT_STALE_AFTER_S = 5.0

# How much recent BPM history to retain for the sparkline window.
DEFAULT_HISTORY_WINDOW_S = 5 * 60

# How much recent RR history to retain for the (experimental) respiration estimate.
DEFAULT_RESP_WINDOW_S = 60

# After this long without any reading, the session auto-closes: stats clear,
# panels hide, and the next reading (whenever it comes) starts a new session.
DEFAULT_IDLE_CLOSE_S = 30 * 60

# Zone-time accounting ignores gaps between readings longer than this
# (dropouts). Must comfortably exceed the history writer's ~5s cadence so
# seeding a restored session from file doesn't discard its intervals.
ZONE_MAX_GAP_S = 15.0


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


@dataclass
class ParticipantState:
    """Latest known state plus session history for one monitored participant."""

    participant_id: str
    display_name: str
    device_id: str | None = None
    # Workout-intensity zones (Tanaka HRmax from birth year, or explicit max).
    birth_year: int | None = None
    max_hr: int | None = None
    bpm: int | None = None
    rr_intervals_ms: list[float] = field(default_factory=list)
    connected: bool = False
    stale: bool = False
    sensor_contact: bool | None = None
    updated_at: str | None = None
    # True once a source (collector/simulator) has handled this participant.
    # Unconfigured participants are never touched, so the overlay hides them.
    active: bool = False

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
    # Whole-session time per intensity bucket (rest, Z1..Z5, over max), in ms.
    zone_ms: list = field(default_factory=lambda: [0] * N_ZONE_BUCKETS)
    # Timestamp of the previous zone-accounted reading, for interval attribution.
    zone_last_ms: int | None = None

    def record_zone_time(self, bpm: int, at_ms: int, max_gap_ms: int) -> None:
        """Attribute the time since the previous reading to this reading's zone.

        Gaps longer than max_gap_ms (signal dropouts) are not attributed to
        any bucket, so the bar only shows time actually tracked.
        """
        zones = zones_for(self.birth_year, self.max_hr)
        if self.zone_last_ms is not None:
            dt = at_ms - self.zone_last_ms
            if 0 < dt <= max_gap_ms:
                self.zone_ms[zone_index(bpm, zones["divisors"])] += dt
        self.zone_last_ms = at_ms

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

    def to_message(self, include_respiration: bool = True) -> dict:
        """Serialize to the camelCase shape consumed by the overlay client.

        Respiration is experimental and only included when explicitly enabled
        (the ``--respire-experiment`` CLI flag); otherwise it is omitted so the
        overlay never shows the estimate.
        """
        avg = round(self.session_sum / self.session_count) if self.session_count else None
        zones = zones_for(self.birth_year, self.max_hr)
        return {
            "participantId": self.participant_id,
            "displayName": self.display_name,
            "bpm": self.bpm,
            "rrIntervalsMs": self.rr_intervals_ms,
            "connected": self.connected,
            "stale": self.stale,
            "active": self.active,
            "sensorContact": self.sensor_contact,
            "updatedAt": self.updated_at,
            # Intensity zones — assumes DEFAULT_AGE (flagged via "assumedAge")
            # when no birth year / max HR is configured.
            "zones": zones,
            # Whole-session ms per bucket (rest, Z1..Z5, over).
            "zoneTimesMs": list(self.zone_ms),
            # Full session history so the overlay is a stateless renderer.
            "samples": [[t, b] for (t, b) in self.samples],
            "session": {
                "min": self.session_min,
                "max": self.session_max,
                "avg": avg,
                "count": self.session_count,
            },
            # Experimental: estimated breaths/min with a 0..1 confidence, or null.
            # Only present when respiration is enabled (off by default).
            "respiration": (
                {
                    "breathsPerMin": self.respiration_brpm,
                    "confidence": self.respiration_confidence,
                }
                if include_respiration and self.respiration_brpm is not None
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
        enable_respiration: bool = False,
        idle_close_s: float = DEFAULT_IDLE_CLOSE_S,
    ) -> None:
        self._participants: dict[str, ParticipantState] = {}
        self._subscribers: set[Subscriber] = set()
        # When the current session began: stamped by the first reading (live or
        # seeded) after startup, an idle auto-close, or a manual reset. None
        # while no session is open.
        self._session_started_at: datetime | None = None
        # When the last reading arrived, for the idle auto-close.
        self._last_data_at: datetime | None = None
        self._stale_after_s = stale_after_s
        self._history_window_ms = int(history_window_s * 1000)
        self._resp_window_ms = int(resp_window_s * 1000)
        self._idle_close_s = idle_close_s
        # Experimental respiration estimate is hidden unless enabled.
        self._enable_respiration = enable_respiration
        self._watchdog_task: asyncio.Task | None = None
        self._recorder: Recorder | None = None
        # Called (sync) when the session auto-closes, e.g. so the history
        # writer marks a reset boundary that seeding won't cross.
        self._on_session_close: Callable[[], None] | None = None

    # -- registration -----------------------------------------------------

    def register_participant(
        self,
        participant_id: str,
        display_name: str,
        device_id: str | None = None,
        birth_year: int | None = None,
        max_hr: int | None = None,
    ) -> None:
        self._participants.setdefault(
            participant_id,
            ParticipantState(
                participant_id=participant_id,
                display_name=display_name,
                device_id=device_id,
                birth_year=birth_year,
                max_hr=max_hr,
            ),
        )

    async def reconcile_participants(self, participants) -> None:
        """Add/update/remove/reorder participant states to match a new config.

        Existing participants keep their session history; renames just update the
        label. Used to apply config edits live without a restart. `participants`
        is a list of objects with ``id``, ``display_name``, ``device_id``.
        """
        ordered: dict[str, ParticipantState] = {}
        for p in participants:
            state = self._participants.get(p.id)
            if state is None:
                state = ParticipantState(
                    participant_id=p.id,
                    display_name=p.display_name,
                    device_id=p.device_id,
                    birth_year=getattr(p, "birth_year", None),
                    max_hr=getattr(p, "max_hr", None),
                )
            else:
                state.display_name = p.display_name
                state.device_id = p.device_id
                state.birth_year = getattr(p, "birth_year", None)
                state.max_hr = getattr(p, "max_hr", None)
            ordered[p.id] = state
        self._participants = ordered
        await self._broadcast()

    def set_recorder(self, recorder: Recorder | None) -> None:
        """Register a sink called for each valid reading (e.g. a history file)."""
        self._recorder = recorder

    def set_session_close_callback(self, callback: Callable[[], None] | None) -> None:
        """Register a hook run when the session auto-closes after idling."""
        self._on_session_close = callback

    def seed_history(self, records: list[dict]) -> None:
        """Rebuild session stats and rolling windows from prior records.

        Used at startup to restore an in-progress session after a server
        restart (records come from today's history file). Records must be in
        chronological order; unknown participants and zero/invalid readings are
        skipped. Does not broadcast or re-record — call before set_recorder().
        """
        now_ms = int(_now().timestamp() * 1000)
        spark_cutoff = now_ms - self._history_window_ms
        rr_cutoff = now_ms - self._resp_window_ms
        session_start: datetime | None = None
        for rec in records:
            # Compact JSONL keys ("p"/"rr"); tolerate the older verbose keys too.
            pid = rec.get("p") or rec.get("participantId")
            state = self._participants.get(pid)
            bpm = rec.get("bpm")
            ts = rec.get("t")
            if state is None or not bpm or bpm <= 0 or not ts:
                continue
            try:
                at = datetime.fromisoformat(ts)
            except ValueError:
                continue
            at_ms = int(at.timestamp() * 1000)
            # Records are chronological: the first restored reading marks when
            # the (still in-progress) session started, the last one feeds the
            # idle auto-close timer.
            if session_start is None:
                session_start = at
                self._session_started_at = at
            self._last_data_at = at
            # Today had data for this participant, so show it on restart.
            state.active = True
            # Whole-session aggregates use every reading from the file.
            state.session_min = bpm if state.session_min is None else min(state.session_min, bpm)
            state.session_max = bpm if state.session_max is None else max(state.session_max, bpm)
            state.session_sum += bpm
            state.session_count += 1
            state.record_zone_time(bpm, at_ms, int(ZONE_MAX_GAP_S * 1000))
            # Rolling windows only keep the recent tail.
            if at_ms >= spark_cutoff:
                state.samples.append((at_ms, bpm))
            if at_ms >= rr_cutoff:
                for rr in rec.get("rr") or rec.get("rrIntervalsMs") or []:
                    state.rr_window.append((at_ms, rr))
        # Restore the respiration estimate from the rebuilt RR window.
        for state in self._participants.values():
            if state.rr_window:
                est = estimate_respiration([rr for _, rr in state.rr_window])
                if est is not None:
                    state.respiration_brpm = est.breaths_per_min
                    state.respiration_confidence = est.confidence

    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.add(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        self._subscribers.discard(callback)

    # -- snapshots --------------------------------------------------------

    def snapshot(self) -> dict:
        """Full state for all participants, in registration order."""
        started = self._session_started_at
        return {
            "type": "state",
            "sessionStartedAt": started.isoformat(timespec="seconds") if started else None,
            "participants": [
                p.to_message(include_respiration=self._enable_respiration)
                for p in self._participants.values()
            ],
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
        # A reading may arrive for a participant just removed by a live config
        # change; ignore it rather than crash.
        state = self._participants.get(participant_id)
        if state is None:
            return
        state.active = True
        now = _now()
        # Any reading opens a session (if none is open) and feeds the idle timer.
        if self._session_started_at is None:
            self._session_started_at = now
        self._last_data_at = now
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
            state.record_zone_time(bpm, now_ms, int(ZONE_MAX_GAP_S * 1000))
            if state.rr_intervals_ms:
                state.record_rr(state.rr_intervals_ms, now_ms, self._resp_window_ms)
            if self._recorder is not None:
                self._recorder(state, bpm, state.rr_intervals_ms, now)
        await self._broadcast()

    async def reset_session(self, deactivate: bool = False) -> None:
        """Close the session: drop sparklines and session aggregates.

        The next reading starts (and timestamps) a new session. For a manual
        reset, connection state and the latest reading are kept — the straps
        are still on the participants. An idle auto-close passes
        ``deactivate=True`` so the panels disappear from the overlay too.
        """
        self._session_started_at = None
        for state in self._participants.values():
            state.samples.clear()
            state.session_min = None
            state.session_max = None
            state.session_sum = 0
            state.session_count = 0
            state.rr_window.clear()
            state.respiration_brpm = None
            state.respiration_confidence = None
            state.zone_ms = [0] * N_ZONE_BUCKETS
            state.zone_last_ms = None
            if deactivate:
                state.active = False
                state.bpm = None
                state.rr_intervals_ms = []
        await self._broadcast()

    async def set_connected(self, participant_id: str, connected: bool) -> None:
        state = self._participants.get(participant_id)
        if state is None:
            return
        state.active = True
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
            await self._maybe_close_idle_session(now)

    async def _maybe_close_idle_session(self, now: datetime) -> None:
        """Auto-close the session after idle_close_s without any reading, so a
        stale session is never blended into (or re-used by) the next one."""
        if self._session_started_at is None or self._last_data_at is None:
            return
        idle_s = (now - self._last_data_at).total_seconds()
        if idle_s <= self._idle_close_s:
            return
        logger.info("closing session after %.0f minutes without data", idle_s / 60)
        await self.reset_session(deactivate=True)
        if self._on_session_close is not None:
            self._on_session_close()

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
