"""Parser for the standard Bluetooth LE Heart Rate Measurement characteristic.

Characteristic: Heart Rate Measurement, UUID 0x2A37
(full UUID 00002a37-0000-1000-8000-00805f9b34fb).

The packet layout is defined by the Bluetooth SIG Heart Rate Service spec:

    byte 0      : flags
    byte 1..    : heart rate value (uint8 or uint16, per flags bit 0)
    [optional]  : energy expended (uint16) if flags bit 3 set
    [optional]  : RR intervals (uint16 each, 1/1024 s units) if flags bit 4 set

This module is intentionally dependency-free so it can be unit tested without
any BLE hardware.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# Flag bit masks (byte 0 of the characteristic value).
_FLAG_HR_16BIT = 0x01  # bit 0: 0 => uint8 BPM, 1 => uint16 BPM
_FLAG_CONTACT_SUPPORTED = 0x04  # bit 2: sensor contact feature supported
_FLAG_CONTACT_DETECTED = 0x02  # bit 1: sensor contact detected (valid if bit 2)
_FLAG_ENERGY_PRESENT = 0x08  # bit 3: energy expended field present
_FLAG_RR_PRESENT = 0x10  # bit 4: one or more RR-interval values present

_RR_UNITS_PER_SECOND = 1024.0


@dataclass
class HeartRateMeasurement:
    """Parsed Heart Rate Measurement notification."""

    bpm: int
    rr_intervals_ms: list[float] = field(default_factory=list)
    energy_expended_j: int | None = None
    # None means the sensor does not report contact status at all.
    sensor_contact: bool | None = None


def parse_hr_measurement(data: bytes | bytearray) -> HeartRateMeasurement:
    """Parse a raw Heart Rate Measurement characteristic value.

    Raises ValueError if the packet is too short to contain the declared fields.
    """
    if len(data) < 2:
        raise ValueError(f"HR measurement too short: {len(data)} bytes")

    flags = data[0]
    offset = 1

    if flags & _FLAG_HR_16BIT:
        if len(data) < offset + 2:
            raise ValueError("HR measurement claims 16-bit BPM but is truncated")
        (bpm,) = struct.unpack_from("<H", data, offset)
        offset += 2
    else:
        bpm = data[offset]
        offset += 1

    sensor_contact: bool | None = None
    if flags & _FLAG_CONTACT_SUPPORTED:
        sensor_contact = bool(flags & _FLAG_CONTACT_DETECTED)

    energy_expended_j: int | None = None
    if flags & _FLAG_ENERGY_PRESENT:
        if len(data) < offset + 2:
            raise ValueError("HR measurement claims energy field but is truncated")
        (energy_expended_j,) = struct.unpack_from("<H", data, offset)
        offset += 2

    rr_intervals_ms: list[float] = []
    if flags & _FLAG_RR_PRESENT:
        while offset + 2 <= len(data):
            (rr_units,) = struct.unpack_from("<H", data, offset)
            offset += 2
            rr_intervals_ms.append(rr_units / _RR_UNITS_PER_SECOND * 1000.0)

    return HeartRateMeasurement(
        bpm=bpm,
        rr_intervals_ms=rr_intervals_ms,
        energy_expended_j=energy_expended_j,
        sensor_contact=sensor_contact,
    )
