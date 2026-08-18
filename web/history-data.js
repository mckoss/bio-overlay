// Pure transforms from IndexedDB reading rows to the session shapes the
// shared history UI (overlay/history-ui.js) renders — the same shapes the
// desktop server returns from /api/history (see history.list_sessions and
// history.load_session in the Python source). Dependency-free w.r.t. the DOM
// and IndexedDB so it can be unit tested in Node.

import { zoneIndex } from "./zones.js";

// Gaps between samples longer than this are dropouts — not attributed to any
// zone (matches the server's ZONE_MAX_GAP_S).
const ZONE_MAX_GAP_S = 15;
const N_ZONE_BUCKETS = 7;

/** Stable session id for a reading row. */
export function sessionKeyOf(row) {
  return `${row.day}__${row.sessionStart ?? row.atMs}`;
}

/** Group rows by session id; each group sorted by time. */
export function groupBySession(rows) {
  const map = new Map();
  for (const r of rows) {
    const k = sessionKeyOf(r);
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(r);
  }
  for (const list of map.values()) list.sort((a, b) => a.atMs - b.atMs);
  return map;
}

function byParticipant(list) {
  const map = new Map(); // index -> rows (already time-sorted)
  for (const r of list) {
    if (!map.has(r.index)) map.set(r.index, []);
    map.get(r.index).push(r);
  }
  return new Map([...map.entries()].sort((a, b) => a[0] - b[0]));
}

// A participant's display name can change mid-session (profile edit); the
// latest row wins, falling back to the strap id.
function nameOf(rows) {
  const last = rows[rows.length - 1];
  return last.name || last.pid;
}

function zoneTimesS(rows, divisors) {
  const times = new Array(N_ZONE_BUCKETS).fill(0);
  for (let i = 1; i < rows.length; i++) {
    const dt = (rows[i].atMs - rows[i - 1].atMs) / 1000;
    if (dt <= 0 || dt > ZONE_MAX_GAP_S) continue;
    times[zoneIndex(rows[i].bpm, divisors)] += dt;
  }
  return times.map((t) => Math.round(t));
}

/**
 * Session summaries, newest first — the /api/history `sessions` shape:
 * {id, date, startedAt, durationS, participants: [name], samples,
 *  zoneTimes: [{name, timesS}]}.
 *
 * divisorsForPid(pid) supplies each participant's zone divisors.
 */
export function listSessions(rows, divisorsForPid) {
  const out = [];
  for (const [id, list] of groupBySession(rows)) {
    const start = list[0].sessionStart ?? list[0].atMs;
    const parts = byParticipant(list);
    out.push({
      id,
      date: list[0].day,
      startedAt: new Date(start).toISOString(),
      durationS: Math.round((list[list.length - 1].atMs - list[0].atMs) / 1000),
      participants: [...parts.values()].map(nameOf),
      samples: list.length,
      zoneTimes: [...parts.values()].map((rows) => ({
        name: nameOf(rows),
        timesS: zoneTimesS(rows, divisorsForPid(rows[rows.length - 1].pid)),
      })),
    });
  }
  out.sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  return out;
}

/**
 * One session serialized in the desktop app's history-file format: a
 * session header line, then compact reading lines with seconds relative to
 * the session start. Returns null for an unknown id.
 */
export function sessionToJsonl(rows, id) {
  const list = groupBySession(rows).get(id);
  if (!list) return null;
  const start = list[0].sessionStart ?? list[0].atMs;
  const lines = [JSON.stringify({
    session: new Date(start).toISOString(),
    participants: [...byParticipant(list).values()].map((rows) => {
      const last = rows[rows.length - 1];
      return { id: last.pid, name: nameOf(rows), deviceId: last.pid };
    }),
  })];
  for (const r of list) {
    lines.push(JSON.stringify({
      s: Math.round((r.atMs - start) / 1000),
      p: r.index,
      bpm: r.bpm,
      rr: r.rr,
    }));
  }
  return lines.join("\n") + "\n";
}

/**
 * Full detail for one session — the /api/history/<id> shape:
 * {id, date, startedAt, durationS, participants: [{id, name, deviceId,
 *  points: [[s, bpm]], stats: {min, max, avg, count}, zones}]}.
 *
 * zonesForPid(pid) supplies each participant's zones object. Returns null
 * for an unknown id.
 */
export function sessionDetail(rows, id, zonesForPid) {
  const list = groupBySession(rows).get(id);
  if (!list) return null;
  const start = list[0].sessionStart ?? list[0].atMs;

  const participants = [];
  for (const rows of byParticipant(list).values()) {
    const bpms = rows.map((r) => r.bpm);
    const pid = rows[rows.length - 1].pid;
    participants.push({
      id: pid,
      name: nameOf(rows),
      deviceId: pid,
      points: rows.map((r) => [Math.round((r.atMs - start) / 100) / 10, r.bpm]),
      stats: {
        min: Math.min(...bpms),
        max: Math.max(...bpms),
        avg: Math.round(bpms.reduce((a, b) => a + b, 0) / bpms.length),
        count: bpms.length,
      },
      zones: zonesForPid(pid),
    });
  }
  return {
    id,
    date: list[0].day,
    startedAt: new Date(start).toISOString(),
    durationS: Math.round((list[list.length - 1].atMs - list[0].atMs) / 1000),
    participants,
  };
}
