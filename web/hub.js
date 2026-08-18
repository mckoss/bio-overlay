// Browser-side telemetry hub: accumulates strap readings and produces state
// snapshots in exactly the shape the desktop server sends over /ws, so the
// shared overlay renderer (overlay/render.js) works unchanged on top of it.
//
// Ported from the accounting parts of src/bio_overlay/telemetry.py
// (PlayerState.record / record_zone_time / snapshot). Respiration is not
// ported (experimental, off by default on desktop too) and is always null.

import { zonesFor, zoneIndex, N_ZONE_BUCKETS } from "./zones.js";

// Rolling sparkline window kept per participant. The renderer clips to its
// own 5-minute window; keep a bit more so a late render never runs dry.
const WINDOW_MS = 6 * 60 * 1000;
// A reading marks its participant stale when nothing follows within this.
export const STALE_AFTER_MS = 5000;
// Gaps longer than this (signal dropouts) are not attributed to any zone.
const ZONE_MAX_GAP_MS = 15000;

export class WebHub {
  constructor() {
    this.participants = new Map(); // id -> state
    this.sessionStartedAtMs = null;
    this.recorders = []; // callbacks: ({id, index, bpm, rrMs, atMs}) => void
  }

  registerParticipant(id, { displayName, birthYear = null, maxHr = null } = {}) {
    if (!this.participants.has(id)) {
      this.participants.set(id, {
        id,
        index: this.participants.size,
        displayName: displayName || id,
        birthYear,
        maxHr,
        bpm: null,
        connected: false,
        updatedAtMs: null,
        samples: [], // [atMs, bpm], oldest first
        sessionMin: null,
        sessionMax: null,
        sessionSum: 0,
        sessionCount: 0,
        zoneMs: new Array(N_ZONE_BUCKETS).fill(0),
        zoneLastMs: null,
      });
    } else {
      const p = this.participants.get(id);
      if (displayName) p.displayName = displayName;
      p.birthYear = birthYear;
      p.maxHr = maxHr;
    }
    return this.participants.get(id);
  }

  /** Register a callback invoked for each valid (non-zero) reading. */
  onReading(cb) {
    this.recorders.push(cb);
  }

  setConnected(id, connected) {
    const p = this.participants.get(id);
    if (p) p.connected = connected;
  }

  /** Ingest one strap notification for participant `id`. */
  reading(id, bpm, rrMs = [], atMs = Date.now()) {
    const p = this.participants.get(id);
    if (!p) return;
    p.bpm = bpm;
    p.updatedAtMs = atMs;
    p.connected = true;
    // bpm == 0 is the strap reporting "no heartbeat detected" (loose
    // contact): keep the panel but don't pollute stats or history with it.
    if (bpm > 0) {
      if (this.sessionStartedAtMs === null) this.sessionStartedAtMs = atMs;
      const zones = zonesFor(p.birthYear, p.maxHr);
      if (p.zoneLastMs !== null) {
        const dt = atMs - p.zoneLastMs;
        if (dt > 0 && dt <= ZONE_MAX_GAP_MS) {
          p.zoneMs[zoneIndex(bpm, zones.divisors)] += dt;
        }
      }
      p.zoneLastMs = atMs;

      p.samples.push([atMs, bpm]);
      const cutoff = atMs - WINDOW_MS;
      while (p.samples.length && p.samples[0][0] < cutoff) p.samples.shift();

      p.sessionMin = p.sessionMin === null ? bpm : Math.min(p.sessionMin, bpm);
      p.sessionMax = p.sessionMax === null ? bpm : Math.max(p.sessionMax, bpm);
      p.sessionSum += bpm;
      p.sessionCount += 1;

      for (const cb of this.recorders) cb({ id, index: p.index, bpm, rrMs, atMs });
    }
  }

  /** Clear session state (stats, sparklines, zone times) for all participants. */
  resetSession() {
    this.sessionStartedAtMs = null;
    for (const p of this.participants.values()) {
      p.samples = [];
      p.sessionMin = p.sessionMax = null;
      p.sessionSum = p.sessionCount = 0;
      p.zoneMs = new Array(N_ZONE_BUCKETS).fill(0);
      p.zoneLastMs = null;
    }
  }

  /** Full state snapshot in the server's /ws message shape. */
  snapshot(nowMs = Date.now()) {
    const participants = [];
    for (const p of this.participants.values()) {
      const avg = p.sessionCount ? Math.round(p.sessionSum / p.sessionCount) : null;
      participants.push({
        participantId: p.id,
        displayName: p.displayName,
        bpm: p.bpm,
        connected: p.connected,
        stale: p.updatedAtMs === null || nowMs - p.updatedAtMs > STALE_AFTER_MS,
        // In the web version a participant exists only once a strap connects,
        // so every registered participant is active.
        active: true,
        sensorContact: null,
        updatedAt: p.updatedAtMs ? new Date(p.updatedAtMs).toISOString() : null,
        zones: zonesFor(p.birthYear, p.maxHr),
        zoneTimesMs: [...p.zoneMs],
        samples: p.samples.map(([t, b]) => [t, b]),
        session: {
          min: p.sessionMin,
          max: p.sessionMax,
          avg,
          count: p.sessionCount,
        },
        respiration: null,
      });
    }
    return {
      type: "state",
      sessionStartedAt: this.sessionStartedAtMs
        ? new Date(this.sessionStartedAtMs).toISOString()
        : null,
      participants,
    };
  }
}
