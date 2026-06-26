"""Unit tests for the Heart Rate Measurement parser.

These run with no BLE hardware and pin down the byte-level packet handling that
the live device path depends on.
"""

import struct

import pytest

from bio_overlay.hr_parser import parse_hr_measurement


def test_8bit_bpm_no_extras():
    # flags=0x00 (8-bit HR, no contact/energy/RR), bpm=72
    m = parse_hr_measurement(bytes([0x00, 72]))
    assert m.bpm == 72
    assert m.rr_intervals_ms == []
    assert m.energy_expended_j is None
    assert m.sensor_contact is None


def test_16bit_bpm():
    # flags bit0 set => 16-bit BPM little-endian, bpm=300
    data = bytes([0x01]) + struct.pack("<H", 300)
    m = parse_hr_measurement(data)
    assert m.bpm == 300


def test_rr_intervals_converted_to_ms():
    # flags bit4 set => RR present. 1024 units == 1000 ms; 512 units == 500 ms.
    data = bytes([0x10, 60]) + struct.pack("<HH", 1024, 512)
    m = parse_hr_measurement(data)
    assert m.bpm == 60
    assert m.rr_intervals_ms == pytest.approx([1000.0, 500.0])


def test_sensor_contact_supported_and_detected():
    # bit1 (detected) + bit2 (supported) set => sensor_contact True
    m = parse_hr_measurement(bytes([0x06, 80]))
    assert m.sensor_contact is True


def test_sensor_contact_supported_not_detected():
    # bit2 set, bit1 clear => contact supported but not detected
    m = parse_hr_measurement(bytes([0x04, 80]))
    assert m.sensor_contact is False


def test_energy_expended_then_rr():
    # bit3 (energy) + bit4 (RR). energy=500, one RR of 1024 units.
    data = bytes([0x18, 70]) + struct.pack("<H", 500) + struct.pack("<H", 1024)
    m = parse_hr_measurement(data)
    assert m.bpm == 70
    assert m.energy_expended_j == 500
    assert m.rr_intervals_ms == pytest.approx([1000.0])


def test_truncated_packet_raises():
    with pytest.raises(ValueError):
        parse_hr_measurement(bytes([0x00]))


def test_truncated_16bit_raises():
    with pytest.raises(ValueError):
        parse_hr_measurement(bytes([0x01, 0x10]))  # claims 16-bit, only 1 data byte
