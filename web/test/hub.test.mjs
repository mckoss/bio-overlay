// Tests for the browser-side telemetry hub: snapshot shape and accounting
// mirror PlayerState in src/bio_overlay/telemetry.py.
import { test } from "node:test";
import assert from "node:assert/strict";

import { WebHub, STALE_AFTER_MS } from "../hub.js";

const T0 = 1_700_000_000_000;

function hubWithOne() {
  const hub = new WebHub();
  hub.registerParticipant("AAAA", { displayName: "Mike", birthYear: 1960 });
  return hub;
}

test("snapshot has the server /ws state shape", () => {
  const hub = hubWithOne();
  hub.reading("AAAA", 100, [600], T0);
  const s = hub.snapshot(T0 + 1000);
  assert.equal(s.type, "state");
  assert.equal(s.sessionStartedAt, new Date(T0).toISOString());
  const p = s.participants[0];
  assert.equal(p.participantId, "AAAA");
  assert.equal(p.displayName, "Mike");
  assert.equal(p.bpm, 100);
  assert.equal(p.active, true);
  assert.equal(p.stale, false);
  assert.ok(Array.isArray(p.zones.divisors));
  assert.equal(p.zoneTimesMs.length, 7);
  assert.deepEqual(p.samples, [[T0, 100]]);
  assert.deepEqual(p.session, { min: 100, max: 100, avg: 100, count: 1 });
  assert.equal(p.respiration, null);
});

test("session stats accumulate; zero bpm keeps panel but not stats", () => {
  const hub = hubWithOne();
  hub.reading("AAAA", 100, [], T0);
  hub.reading("AAAA", 120, [], T0 + 1000);
  hub.reading("AAAA", 0, [], T0 + 2000); // loose contact
  hub.reading("AAAA", 80, [], T0 + 3000);
  const p = hub.snapshot(T0 + 3000).participants[0];
  assert.deepEqual(p.session, { min: 80, max: 120, avg: 100, count: 3 });
  assert.equal(p.samples.length, 3);
});

test("zone time attributed to the current reading's zone, gaps skipped", () => {
  const hub = hubWithOne();
  // Zones for 1960: divisors [81, 97, 113, 130, 146, 162] (Z-boundaries).
  hub.reading("AAAA", 100, [], T0);
  hub.reading("AAAA", 100, [], T0 + 2000); // 2s in Z2 (97 <= 100 < 113)
  hub.reading("AAAA", 120, [], T0 + 3000); // 1s in Z3
  hub.reading("AAAA", 120, [], T0 + 60_000); // 57s gap > 15s: not attributed
  const p = hub.snapshot(T0 + 60_000).participants[0];
  assert.equal(p.zoneTimesMs[2], 2000);
  assert.equal(p.zoneTimesMs[3], 1000);
  assert.equal(p.zoneTimesMs.reduce((a, b) => a + b, 0), 3000);
});

test("stale after no readings for STALE_AFTER_MS", () => {
  const hub = hubWithOne();
  hub.reading("AAAA", 100, [], T0);
  assert.equal(hub.snapshot(T0 + STALE_AFTER_MS).participants[0].stale, false);
  assert.equal(hub.snapshot(T0 + STALE_AFTER_MS + 1).participants[0].stale, true);
});

test("resetSession clears stats and session start", () => {
  const hub = hubWithOne();
  hub.reading("AAAA", 100, [], T0);
  hub.resetSession();
  const s = hub.snapshot(T0 + 1000);
  assert.equal(s.sessionStartedAt, null);
  assert.deepEqual(s.participants[0].session, { min: null, max: null, avg: null, count: 0 });
  hub.reading("AAAA", 90, [], T0 + 2000);
  assert.equal(hub.snapshot(T0 + 2000).sessionStartedAt, new Date(T0 + 2000).toISOString());
});

test("onReading recorder fires for valid readings only", () => {
  const hub = hubWithOne();
  const seen = [];
  hub.onReading((r) => seen.push(r));
  hub.reading("AAAA", 100, [601.5], T0);
  hub.reading("AAAA", 0, [], T0 + 1000);
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0], { id: "AAAA", index: 0, bpm: 100, rrMs: [601.5], atMs: T0 });
});
