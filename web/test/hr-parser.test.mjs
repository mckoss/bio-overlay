// Unit tests for the Heart Rate Measurement parser — a port of
// tests/test_hr_parser.py so the JS and Python parsers stay pinned to the
// same byte-level behavior. Run with: node --test web/test/
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseHrMeasurement } from "../hr-parser.js";

function view(...bytes) {
  return new DataView(new Uint8Array(bytes).buffer);
}

function u16le(n) {
  return [n & 0xff, (n >> 8) & 0xff];
}

function approxEqual(actual, expected) {
  assert.equal(actual.length, expected.length);
  for (let i = 0; i < actual.length; i++) {
    assert.ok(Math.abs(actual[i] - expected[i]) < 1e-6, `[${i}] ${actual[i]} !~ ${expected[i]}`);
  }
}

test("8-bit bpm, no extras", () => {
  // flags=0x00 (8-bit HR, no contact/energy/RR), bpm=72
  const m = parseHrMeasurement(view(0x00, 72));
  assert.equal(m.bpm, 72);
  assert.deepEqual(m.rrIntervalsMs, []);
  assert.equal(m.energyExpendedJ, null);
  assert.equal(m.sensorContact, null);
});

test("16-bit bpm", () => {
  // flags bit0 set => 16-bit BPM little-endian, bpm=300
  const m = parseHrMeasurement(view(0x01, ...u16le(300)));
  assert.equal(m.bpm, 300);
});

test("RR intervals converted to ms", () => {
  // flags bit4 set => RR present. 1024 units == 1000 ms; 512 units == 500 ms.
  const m = parseHrMeasurement(view(0x10, 60, ...u16le(1024), ...u16le(512)));
  assert.equal(m.bpm, 60);
  approxEqual(m.rrIntervalsMs, [1000.0, 500.0]);
});

test("sensor contact supported and detected", () => {
  // bit1 (detected) + bit2 (supported) set => sensorContact true
  const m = parseHrMeasurement(view(0x06, 80));
  assert.equal(m.sensorContact, true);
});

test("sensor contact supported, not detected", () => {
  // bit2 set, bit1 clear => contact supported but not detected
  const m = parseHrMeasurement(view(0x04, 80));
  assert.equal(m.sensorContact, false);
});

test("energy expended then RR", () => {
  // bit3 (energy) + bit4 (RR). energy=500, one RR of 1024 units.
  const m = parseHrMeasurement(view(0x18, 70, ...u16le(500), ...u16le(1024)));
  assert.equal(m.bpm, 70);
  assert.equal(m.energyExpendedJ, 500);
  approxEqual(m.rrIntervalsMs, [1000.0]);
});

test("truncated packet throws", () => {
  assert.throws(() => parseHrMeasurement(view(0x00)));
});

test("truncated 16-bit throws", () => {
  // claims 16-bit, only 1 data byte
  assert.throws(() => parseHrMeasurement(view(0x01, 0x10)));
});
