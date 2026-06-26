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
import contextlib
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
# start_notify can race CoreBluetooth service discovery right after connect.
_SUBSCRIBE_RETRIES = 6
_SUBSCRIBE_RETRY_DELAY_S = 0.5


def device_id_from_name(name: str) -> str | None:
    """Extract the trailing Polar device ID from an advertised name.

    "Polar H10 16CD9E3C" -> "16CD9E3C". Returns None if there's no trailing
    token that looks like an ID.
    """
    parts = name.split()
    if len(parts) >= 2 and parts[-1] not in {"H10", "?"}:
        return parts[-1]
    return None


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

    def _name_matches(self, device, adv) -> bool:
        """True if an advertised device matches this participant's binding.

        Always requires the name prefix; if a device_id is configured, the
        advertised name must also contain it (case-insensitive).
        """
        name = device.name or adv.local_name or ""
        p = self.participant
        if not name.startswith(p.name_prefix):
            return False
        if p.device_id and p.device_id.upper() not in name.upper():
            return False
        return True

    async def _resolve_device(self):
        """Find the BLE device for this participant.

        Preference order: device_id (printed on the strap, portable) > address
        (Mac-specific CoreBluetooth UUID) > first strap matching name_prefix.
        """
        from bleak import BleakScanner  # lazy import

        p = self.participant

        # Preferred: match the physical strap by its Polar device ID.
        if p.device_id:
            return await BleakScanner.find_device_by_filter(
                self._name_matches, timeout=_SCAN_TIMEOUT_S
            )

        # Fallback: the Mac-specific CoreBluetooth UUID.
        if p.address:
            device = await BleakScanner.find_device_by_address(
                p.address, timeout=_SCAN_TIMEOUT_S
            )
            if device:
                return device
            logger.warning(
                "[%s] address %s not found; falling back to name scan",
                p.id,
                p.address,
            )

        # Last resort: first strap whose name starts with the prefix.
        return await BleakScanner.find_device_by_filter(
            self._name_matches, timeout=_SCAN_TIMEOUT_S
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
                    await self._subscribe(client)
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

    async def _subscribe(self, client) -> None:
        """Start HR notifications, tolerating the CoreBluetooth discovery race.

        On macOS, ``start_notify`` immediately after connect can raise
        "Service Discovery has not been performed yet" if the GATT service
        scan hasn't finished. Retry briefly before giving up to the outer
        reconnect loop.
        """
        last_exc: Exception | None = None
        for attempt in range(_SUBSCRIBE_RETRIES):
            try:
                await client.start_notify(HR_MEASUREMENT_CHAR_UUID, self._on_notify)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.debug(
                    "[%s] start_notify attempt %d failed: %s",
                    self.participant.id,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(_SUBSCRIBE_RETRY_DELAY_S)
        raise last_exc  # type: ignore[misc]

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def stop(self) -> None:
        self._stop.set()


def _is_configured(p: ParticipantConfig) -> bool:
    """A participant is connectable only once bound to a specific strap.

    Without a deviceId/address we must NOT fall back to "any matching strap" —
    otherwise multiple unconfigured participants all grab the same strap and show
    duplicate readings.
    """
    return bool(p.device_id or p.address)


def _binding_changed(a: ParticipantConfig, b: ParticipantConfig) -> bool:
    """True if a participant's strap binding changed (needs a reconnect)."""
    return (a.device_id, a.address, a.name_prefix) != (b.device_id, b.address, b.name_prefix)


class BleCollector:
    """Supervises one :class:`StrapConnection` per participant, with live apply.

    Connections can be added, removed, or rebound at runtime via :meth:`apply`
    so config edits take effect without restarting the process.
    """

    def __init__(self, participants: list[ParticipantConfig], hub: TelemetryHub) -> None:
        self._hub = hub
        self._params: dict[str, ParticipantConfig] = {p.id: p for p in participants}
        self._conns: dict[str, StrapConnection] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()

    def _start(self, p: ParticipantConfig) -> None:
        conn = StrapConnection(p, self._hub)
        self._conns[p.id] = conn
        self._tasks[p.id] = asyncio.create_task(conn.run())

    async def _stop_one(self, pid: str) -> None:
        conn = self._conns.pop(pid, None)
        task = self._tasks.pop(pid, None)
        if conn is not None:
            conn.stop()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def run(self) -> None:
        for p in self._params.values():
            if _is_configured(p):
                self._start(p)
            else:
                logger.info("[%s] no strap bound; not connecting", p.id)
        await self._stop.wait()
        for pid in list(self._conns):
            await self._stop_one(pid)

    async def apply(self, participants: list[ParticipantConfig]) -> None:
        """Reconcile running connections to a new participant list.

        Only configured (strap-bound) participants get a connection.
        """
        new = {p.id: p for p in participants}
        wanted = {pid: p for pid, p in new.items() if _is_configured(p)}
        # Stop connections that were removed, unbound, or whose strap changed.
        for pid in list(self._conns):
            old = self._params.get(pid)
            cur = wanted.get(pid)
            if cur is None or (old is not None and _binding_changed(old, cur)):
                await self._stop_one(pid)
                logger.info("[%s] connection stopped (config change)", pid)
        # Start connections that are newly bound.
        for pid, p in wanted.items():
            if pid not in self._conns:
                self._start(p)
                logger.info("[%s] connection started (config change)", pid)
        self._params = new

    def stop(self) -> None:
        self._stop.set()
