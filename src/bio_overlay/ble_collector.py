"""BLE collector for Polar H10 (and any standard HR-service strap).

Each participant gets one :class:`StrapConnection` that runs an independent
retry loop: scan/connect, subscribe to the Heart Rate Measurement
characteristic, push parsed updates into the :class:`TelemetryHub`, and
reconnect on drop.

`bleak` is imported lazily so the rest of the package (parser, telemetry,
server, simulator) can be used and tested on machines without it installed.
"""

from __future__ import annotations

import asyncio
import logging

from .config import ParticipantConfig
from .hr_parser import parse_hr_measurement
from .telemetry import TelemetryHub

logger = logging.getLogger(__name__)

# Standard Bluetooth SIG assigned numbers.
HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_CHAR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

_RECONNECT_DELAY_S = 3.0
_SCAN_TIMEOUT_S = 10.0


async def scan(timeout: float = _SCAN_TIMEOUT_S, name_prefix: str | None = None):
    """Return discovered BLE devices, optionally filtered by name prefix.

    Each entry is ``(address, name, advertised_services)``. On macOS the
    address is a CoreBluetooth UUID string.
    """
    from bleak import BleakScanner  # lazy import

    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    results = []
    for address, (device, adv) in devices.items():
        name = device.name or adv.local_name or "?"
        if name_prefix and not name.startswith(name_prefix):
            continue
        results.append((address, name, list(adv.service_uuids or [])))
    return results


class StrapConnection:
    """Manages one strap's connection lifecycle for a single participant."""

    def __init__(self, participant: ParticipantConfig, hub: TelemetryHub) -> None:
        self.participant = participant
        self.hub = hub
        self._stop = asyncio.Event()

    async def _resolve_device(self):
        """Find the BLE device for this participant, by address or name prefix."""
        from bleak import BleakScanner  # lazy import

        if self.participant.address:
            device = await BleakScanner.find_device_by_address(
                self.participant.address, timeout=_SCAN_TIMEOUT_S
            )
            if device:
                return device
            logger.warning(
                "[%s] address %s not found; falling back to name scan",
                self.participant.id,
                self.participant.address,
            )

        return await BleakScanner.find_device_by_filter(
            lambda d, adv: bool(
                (d.name or adv.local_name or "").startswith(self.participant.name_prefix)
            ),
            timeout=_SCAN_TIMEOUT_S,
        )

    def _on_notify(self, _sender, data: bytearray) -> None:
        try:
            measurement = parse_hr_measurement(data)
        except ValueError as exc:
            logger.warning("[%s] bad HR packet %s: %s", self.participant.id, data.hex(), exc)
            return
        # Notification callbacks are sync; schedule the async hub update.
        asyncio.create_task(
            self.hub.update_measurement(
                self.participant.id,
                bpm=measurement.bpm,
                rr_intervals_ms=measurement.rr_intervals_ms,
                sensor_contact=measurement.sensor_contact,
            )
        )

    async def run(self) -> None:
        """Connect-and-subscribe retry loop until :meth:`stop` is called."""
        from bleak import BleakClient  # lazy import

        while not self._stop.is_set():
            device = None
            try:
                device = await self._resolve_device()
                if device is None:
                    logger.info("[%s] strap not found; retrying", self.participant.id)
                    await self._wait_or_stop(_RECONNECT_DELAY_S)
                    continue

                logger.info("[%s] connecting to %s", self.participant.id, device)
                async with BleakClient(device) as client:
                    await client.start_notify(HR_MEASUREMENT_CHAR_UUID, self._on_notify)
                    await self.hub.set_connected(self.participant.id, True)
                    logger.info("[%s] connected", self.participant.id)

                    # Stay connected until the client drops or we're told to stop.
                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(0.5)

                    await client.stop_notify(HR_MEASUREMENT_CHAR_UUID)
            except Exception as exc:  # noqa: BLE001 - keep the retry loop alive
                logger.warning("[%s] connection error: %s", self.participant.id, exc)
            finally:
                await self.hub.set_connected(self.participant.id, False)

            if not self._stop.is_set():
                await self._wait_or_stop(_RECONNECT_DELAY_S)

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def stop(self) -> None:
        self._stop.set()


class BleCollector:
    """Runs one :class:`StrapConnection` per configured participant."""

    def __init__(self, participants: list[ParticipantConfig], hub: TelemetryHub) -> None:
        self._connections = [StrapConnection(p, hub) for p in participants]

    async def run(self) -> None:
        await asyncio.gather(*(c.run() for c in self._connections))

    def stop(self) -> None:
        for c in self._connections:
            c.stop()
