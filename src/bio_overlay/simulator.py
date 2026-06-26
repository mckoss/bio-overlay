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
        breaths_per_min: float = 14.0,
        rsa_amp_ms: float = 35.0,
    ) -> None:
        self.participant = participant
        self.hub = hub
        self._phase = phase
        self._dropout_every = dropout_every
        self._f_resp = breaths_per_min / 60.0
        self._rsa_amp_ms = rsa_amp_ms
        self._tick = 0
        # Beat-time clock (seconds) advanced by each RR interval, so the RR
        # series carries a self-consistent respiratory modulation the estimator
        # can recover — useful for demoing respiration without hardware.
        self._beat_time_s = 0.0
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
            mean_rr = 60000.0 / bpm
            # Respiratory sinus arrhythmia: modulate RR at the breathing rate.
            rsa = self._rsa_amp_ms * math.sin(
                2 * math.pi * self._f_resp * self._beat_time_s + self._phase
            )
            rr = round(mean_rr + rsa, 1)
            self._beat_time_s += rr / 1000.0

            await self.hub.update_measurement(
                self.participant.id,
                bpm=bpm,
                rr_intervals_ms=[rr],
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
                # Distinct breathing rates so the two cards differ.
                breaths_per_min=14.0 if i == 0 else 11.0,
            )
            for i, p in enumerate(participants)
        ]

    async def run(self) -> None:
        await asyncio.gather(*(s.run() for s in self._straps))

    def stop(self) -> None:
        for s in self._straps:
            s.stop()
