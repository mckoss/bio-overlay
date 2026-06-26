"""Hardware-free telemetry source.

Generates plausible BPM and RR-interval data so the telemetry server and the
OBS overlay can be developed and verified end-to-end before any Polar H10 is
available. Each participant gets a slowly drifting heart rate with small
beat-to-beat variation, and occasional simulated dropouts to exercise the
stale/disconnected overlay states.
"""

from __future__ import annotations

import asyncio
import math

from .config import ParticipantConfig
from .telemetry import TelemetryHub

# Deterministic-ish pseudo wander without seeding global random state.
_BASE_BPM = 110
_AMPLITUDE = 25
_PERIOD_S = 40.0
_UPDATE_INTERVAL_S = 1.0


class SimulatedStrap:
    def __init__(
        self,
        participant: ParticipantConfig,
        hub: TelemetryHub,
        *,
        phase: float = 0.0,
        dropout_every: int | None = None,
    ) -> None:
        self.participant = participant
        self.hub = hub
        self._phase = phase
        self._dropout_every = dropout_every
        self._tick = 0
        self._stop = asyncio.Event()

    async def run(self) -> None:
        await self.hub.set_connected(self.participant.id, True)
        while not self._stop.is_set():
            self._tick += 1

            # Periodically simulate a brief dropout to exercise stale UI.
            if self._dropout_every and self._tick % self._dropout_every == 0:
                await self.hub.set_connected(self.participant.id, False)
                await self._sleep(4.0)
                await self.hub.set_connected(self.participant.id, True)
                continue

            t = self._tick * _UPDATE_INTERVAL_S
            bpm = int(
                _BASE_BPM + _AMPLITUDE * math.sin(2 * math.pi * t / _PERIOD_S + self._phase)
            )
            rr_ms = 60000.0 / bpm
            # Add a touch of beat-to-beat variation around the mean RR.
            jitter = 12.0 * math.sin(t * 1.7 + self._phase)
            rr_intervals = [round(rr_ms + jitter, 1), round(rr_ms - jitter, 1)]

            await self.hub.update_measurement(
                self.participant.id,
                bpm=bpm,
                rr_intervals_ms=rr_intervals,
                sensor_contact=True,
            )
            await self._sleep(_UPDATE_INTERVAL_S)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def stop(self) -> None:
        self._stop.set()


class Simulator:
    """Drives a SimulatedStrap per participant."""

    def __init__(self, participants: list[ParticipantConfig], hub: TelemetryHub) -> None:
        self._straps = [
            SimulatedStrap(
                p,
                hub,
                phase=i * math.pi,  # offset participants so they don't move in lockstep
                # Only the second participant simulates dropouts, to show both states.
                dropout_every=25 if i == 1 else None,
            )
            for i, p in enumerate(participants)
        ]

    async def run(self) -> None:
        await asyncio.gather(*(s.run() for s in self._straps))

    def stop(self) -> None:
        for s in self._straps:
            s.stop()
