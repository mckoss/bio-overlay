// Tests for the zones port. Expected values are generated from the Python
// source of truth (bio_overlay.zones) — including the half-to-even rounding
// cases where JS Math.round would disagree (162.5 -> 162, not 163).
import { test } from "node:test";
import assert from "node:assert/strict";

import { zonesFor, zoneIndex, pyRound, DEFAULT_AGE } from "../zones.js";

test("default zones assume age 65 (Tanaka 162.5 rounds half-to-even to 162)", () => {
  const z = zonesFor(null);
  assert.deepEqual(z, {
    maxHr: 162,
    divisors: [81, 97, 113, 130, 146, 162],
    assumedAge: DEFAULT_AGE,
  });
});

test("birth year 1960 in 2026 matches Python", () => {
  const z = zonesFor(1960, null, 2026);
  assert.deepEqual(z, { maxHr: 162, divisors: [81, 97, 113, 130, 146, 162] });
});

test("birth year 1985 in 2026 matches Python", () => {
  const z = zonesFor(1985, null, 2026);
  assert.deepEqual(z, { maxHr: 179, divisors: [90, 107, 125, 143, 161, 179] });
});

test("explicit maxHr 175 matches Python (87.5 -> 88, 157.5 -> 158)", () => {
  const z = zonesFor(null, 175);
  assert.deepEqual(z, { maxHr: 175, divisors: [88, 105, 122, 140, 158, 175] });
});

test("implausible birth year falls back to assumed age", () => {
  const z = zonesFor(65, null, 2026); // "65" typo for a birth year
  assert.equal(z.assumedAge, DEFAULT_AGE);
});

test("zoneIndex buckets match Python across boundaries", () => {
  const d = [81, 97, 113, 130, 146, 162];
  const expected = [
    [40, 0], [81, 1], [96, 1], [97, 2], [113, 3],
    [130, 4], [145, 4], [146, 5], [162, 5], [163, 6],
  ];
  for (const [bpm, idx] of expected) {
    assert.equal(zoneIndex(bpm, d), idx, `bpm ${bpm}`);
  }
});

test("pyRound half-to-even", () => {
  assert.equal(pyRound(162.5), 162);
  assert.equal(pyRound(163.5), 164);
  assert.equal(pyRound(87.5), 88);
  assert.equal(pyRound(129.6), 130);
  assert.equal(pyRound(97.2), 97);
});
