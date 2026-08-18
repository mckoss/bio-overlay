// Tests for the IndexedDB-row -> session-shape transforms behind the web
// history page. Semantics mirror the server's history.list_sessions /
// load_session (grouping, ordering, the 15s zone gap rule, stats).
import { test } from "node:test";
import assert from "node:assert/strict";

import { listSessions, sessionDetail, sessionKeyOf } from "../history-data.js";

const T0 = Date.UTC(2026, 7, 18, 16, 0, 0); // 2026-08-18 16:00Z
const DIVISORS = [81, 97, 113, 130, 146, 162];
const ZONES = { maxHr: 162, divisors: DIVISORS };

function row(sessionStart, offsetS, index, bpm, extra = {}) {
  return {
    day: "2026-08-18",
    sessionStart,
    pid: index === 0 ? "AAAA" : "BBBB",
    name: index === 0 ? "Mike" : "Debbie",
    index,
    atMs: sessionStart + offsetS * 1000,
    bpm,
    rr: [],
    ...extra,
  };
}

const S1 = T0; // one-hour-earlier session
const S2 = T0 + 3600_000; // later session

function sampleRows() {
  return [
    // session 1: Mike only, readings at 0/2/4s
    row(S1, 0, 0, 100),
    row(S1, 2, 0, 100), // 2s in Z2 (97 <= 100 < 113)
    row(S1, 4, 0, 120), // 2s in Z3
    // session 2: Mike + Debbie
    row(S2, 0, 0, 90),
    row(S2, 5, 0, 90), // 5s in Z1
    row(S2, 0, 1, 140),
    row(S2, 30, 1, 140), // 30s gap > 15s: not attributed
  ];
}

test("listSessions groups by session, newest first, with zone times", () => {
  const sessions = listSessions(sampleRows(), () => DIVISORS);
  assert.equal(sessions.length, 2);

  const [newest, oldest] = sessions;
  assert.equal(newest.id, `2026-08-18__${S2}`);
  assert.equal(newest.startedAt, new Date(S2).toISOString());
  assert.equal(newest.durationS, 30);
  assert.deepEqual(newest.participants, ["Mike", "Debbie"]);
  assert.equal(newest.samples, 4);
  // Mike: 5s in Z1; Debbie: nothing attributed (single 30s gap).
  assert.equal(newest.zoneTimes[0].name, "Mike");
  assert.equal(newest.zoneTimes[0].timesS[1], 5);
  assert.equal(newest.zoneTimes[1].timesS.reduce((a, b) => a + b, 0), 0);

  assert.equal(oldest.id, `2026-08-18__${S1}`);
  assert.equal(oldest.durationS, 4);
  assert.deepEqual(oldest.participants, ["Mike"]);
  assert.deepEqual(oldest.zoneTimes[0].timesS, [0, 0, 2, 2, 0, 0, 0]);
});

test("sessionDetail has points, stats, and zones per participant", () => {
  const d = sessionDetail(sampleRows(), `2026-08-18__${S1}`, () => ZONES);
  assert.equal(d.startedAt, new Date(S1).toISOString());
  assert.equal(d.durationS, 4);
  assert.equal(d.participants.length, 1);
  const p = d.participants[0];
  assert.equal(p.name, "Mike");
  assert.equal(p.id, "AAAA");
  assert.deepEqual(p.points, [[0, 100], [2, 100], [4, 120]]);
  assert.deepEqual(p.stats, { min: 100, max: 120, avg: 107, count: 3 });
  assert.deepEqual(p.zones, ZONES);
});

test("sessionDetail returns null for unknown id", () => {
  assert.equal(sessionDetail(sampleRows(), "2026-08-18__42", () => ZONES), null);
});

test("a mid-session rename uses the latest name", () => {
  const rows = [row(S1, 0, 0, 100), { ...row(S1, 2, 0, 100), name: "Michael" }];
  const sessions = listSessions(rows, () => DIVISORS);
  assert.deepEqual(sessions[0].participants, ["Michael"]);
});

test("sessionKeyOf matches list ids (delete filter)", () => {
  const rows = sampleRows();
  const sessions = listSessions(rows, () => DIVISORS);
  const remaining = rows.filter((r) => sessionKeyOf(r) !== sessions[0].id);
  assert.equal(remaining.length, 3);
  assert.ok(remaining.every((r) => r.sessionStart === S1));
});

test("rows with null sessionStart group under their first timestamp", () => {
  const rows = [
    { day: "2026-08-18", sessionStart: null, pid: "AAAA", name: "Mike", index: 0, atMs: T0, bpm: 100, rr: [] },
  ];
  const sessions = listSessions(rows, () => DIVISORS);
  assert.equal(sessions.length, 1);
  assert.equal(sessions[0].id, `2026-08-18__${T0}`);
});
