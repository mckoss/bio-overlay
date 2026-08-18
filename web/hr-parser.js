// Parser for the standard Bluetooth LE Heart Rate Measurement characteristic.
//
// Characteristic: Heart Rate Measurement, UUID 0x2A37
// (full UUID 00002a37-0000-1000-8000-00805f9b34fb).
//
// The packet layout is defined by the Bluetooth SIG Heart Rate Service spec:
//
//     byte 0      : flags
//     byte 1..    : heart rate value (uint8 or uint16, per flags bit 0)
//     [optional]  : energy expended (uint16) if flags bit 3 set
//     [optional]  : RR intervals (uint16 each, 1/1024 s units) if flags bit 4 set
//
// This module is a direct port of src/bio_overlay/hr_parser.py and is
// dependency-free so it can be unit tested in Node without BLE hardware.

const FLAG_HR_16BIT = 0x01; // bit 0: 0 => uint8 BPM, 1 => uint16 BPM
const FLAG_CONTACT_DETECTED = 0x02; // bit 1: sensor contact detected (valid if bit 2)
const FLAG_CONTACT_SUPPORTED = 0x04; // bit 2: sensor contact feature supported
const FLAG_ENERGY_PRESENT = 0x08; // bit 3: energy expended field present
const FLAG_RR_PRESENT = 0x10; // bit 4: one or more RR-interval values present

const RR_UNITS_PER_SECOND = 1024.0;

/**
 * Parse a raw Heart Rate Measurement characteristic value.
 *
 * @param {DataView} view - characteristic value (as delivered by Web Bluetooth)
 * @returns {{bpm: number, rrIntervalsMs: number[], energyExpendedJ: number|null,
 *            sensorContact: boolean|null}}
 *   sensorContact null means the sensor does not report contact status at all.
 * @throws {Error} if the packet is too short to contain the declared fields.
 */
export function parseHrMeasurement(view) {
  if (view.byteLength < 2) {
    throw new Error(`HR measurement too short: ${view.byteLength} bytes`);
  }

  const flags = view.getUint8(0);
  let offset = 1;

  let bpm;
  if (flags & FLAG_HR_16BIT) {
    if (view.byteLength < offset + 2) {
      throw new Error("HR measurement claims 16-bit BPM but is truncated");
    }
    bpm = view.getUint16(offset, /* littleEndian= */ true);
    offset += 2;
  } else {
    bpm = view.getUint8(offset);
    offset += 1;
  }

  let sensorContact = null;
  if (flags & FLAG_CONTACT_SUPPORTED) {
    sensorContact = Boolean(flags & FLAG_CONTACT_DETECTED);
  }

  let energyExpendedJ = null;
  if (flags & FLAG_ENERGY_PRESENT) {
    if (view.byteLength < offset + 2) {
      throw new Error("HR measurement claims energy field but is truncated");
    }
    energyExpendedJ = view.getUint16(offset, true);
    offset += 2;
  }

  const rrIntervalsMs = [];
  if (flags & FLAG_RR_PRESENT) {
    while (offset + 2 <= view.byteLength) {
      const rrUnits = view.getUint16(offset, true);
      offset += 2;
      rrIntervalsMs.push((rrUnits / RR_UNITS_PER_SECOND) * 1000.0);
    }
  }

  return { bpm, rrIntervalsMs, energyExpendedJ, sensorContact };
}
