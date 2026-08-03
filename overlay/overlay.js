/*
 * Overlay client: connects to the telemetry WebSocket and renders participant
 * panels. Designed to be resilient to OBS scene reloads and computer sleep by
 * auto-reconnecting with backoff.
 *
 * Each panel shows the live BPM, a sparkline of the last 5 minutes (with the
 * window's min/max), and whole-session min/avg/max. The server is the source of
 * truth for history: every snapshot carries the sparkline samples and session
 * stats, so this client is a stateless renderer and a page/OBS reload restores
 * the full sparkline and stats immediately.
 */

(() => {
  "use strict";

  const WS_PATH = "/ws";
  const RECONNECT_MIN_MS = 500;
  const RECONNECT_MAX_MS = 5000;

  // Sparkline window and geometry (viewBox units; CSS sizes the element).
  const WINDOW_MS = 5 * 60 * 1000;
  const SPARK_W = 192;
  const SPARK_H = 48;
  const SPARK_PAD_Y = 5;

  const panelsEl = document.getElementById("panels");
  const clockEl = document.getElementById("session-clock");
  const statusEl = document.getElementById("status");

  // Debug/keying aid: `?bg=green` (or any CSS color) paints the otherwise
  // transparent background so you can see exactly which area is overlay vs
  // see-through. Leave it off for OBS — Browser Sources composite real alpha.
  applyDebugBackground();

  function applyDebugBackground() {
    const bg = new URLSearchParams(location.search).get("bg");
    if (bg) document.body.style.background = bg;
  }

  // participantId -> { root, nameEl, bpmEl, sparkEl, maxEl, minEl, sessionEl, badgeEl }
  const panels = new Map();
  let reconnectDelay = RECONNECT_MIN_MS;

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${WS_PATH}`;
  }

  function ensurePanel(participantId) {
    let panel = panels.get(participantId);
    if (panel) return panel;

    const root = el("div", "panel");
    const nameEl = el("div", "name");

    const live = el("div", "live");
    const bpmEl = el("div", "bpm", "--");
    const unitEl = el("div", "unit", "BPM");
    const zoneEl = el("div", "zone");
    live.append(bpmEl, unitEl, zoneEl);

    // The sparkline sits beside the name AND the live BPM (grid area spans
    // both rows) so the chart gets as much vertical space as possible.
    const spark = el("div", "spark");
    const sparkEl = el("div", "spark-svg");
    const boundsEl = el("div", "spark-bounds");
    spark.append(sparkEl, boundsEl);

    const sessionEl = el("div", "session");
    const respEl = el("div", "resp");
    const badgeEl = el("div", "badge");

    root.append(nameEl, live, spark, sessionEl, respEl, badgeEl);
    panelsEl.appendChild(root);

    panel = { root, nameEl, bpmEl, zoneEl, sparkEl, boundsEl, sessionEl, respEl, badgeEl };
    panels.set(participantId, panel);
    return panel;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderParticipant(p) {
    const panel = ensurePanel(p.participantId);
    panel.nameEl.textContent = p.displayName;

    // bpm == 0 is the H10 reporting "no heartbeat detected" (loose contact).
    const live = p.connected && !p.stale && p.bpm != null && p.bpm > 0;
    panel.root.classList.toggle("stale", !live);

    if (p.bpm != null) {
      panel.bpmEl.textContent = String(p.bpm);
      const beatSeconds = Math.max(0.3, Math.min(2, 60 / Math.max(p.bpm, 1)));
      panel.root.style.setProperty("--beat", `${beatSeconds}s`);
    } else {
      panel.bpmEl.textContent = "--";
    }

    // The big number and the chip both take the current zone's color.
    const zone = live ? zoneFor(p.bpm, p.zones) : null;
    panel.bpmEl.className = "bpm" + (zone ? " " + zone : "");
    renderZoneChip(panel, zone);
    // History comes from the server, so a reload restores it immediately.
    renderSparkline(panel, p.samples || [], p.zones);
    renderSession(panel, p.session);
    renderRespiration(panel, p.respiration);
    panel.badgeEl.textContent = "";
  }

  // Intensity zones (from the server: Tanaka HRmax + band divisors).
  const ZONE_NAMES = ["rest", "z1", "z2", "z3", "z4", "z5", "over"];
  const ZONE_LABELS = {
    rest: "Rest",
    z1: "Z1: Recovery",
    z2: "Z2: Endurance",
    z3: "Z3: Aerobic",
    z4: "Z4: Threshold",
    z5: "Z5: Peak",
    over: "SUPERMAX",
  };

  function zoneFor(bpm, zones) {
    if (bpm == null || !zones || !Array.isArray(zones.divisors)) return null;
    // divisors: [rest|z1, z1|z2, z2|z3, z3|z4, z4|z5, z5|over] — the last one
    // is HRmax itself (inclusive).
    const d = zones.divisors;
    for (let i = 0; i < d.length - 1; i++) {
      if (bpm < d[i]) return ZONE_NAMES[i];
    }
    return bpm <= d[d.length - 1] ? ZONE_NAMES[d.length - 1] : "over";
  }

  function renderZoneChip(panel, zone) {
    panel.zoneEl.textContent = zone ? ZONE_LABELS[zone] : "";
    panel.zoneEl.className = "zone" + (zone ? " " + zone : "");
  }

  // Experimental respiration is hidden below this confidence to avoid showing
  // misleading numbers when the RSA signal is weak (e.g. during hard effort).
  const RESP_MIN_CONFIDENCE = 0.2;

  function renderRespiration(panel, resp) {
    if (!resp || resp.breathsPerMin == null || resp.confidence < RESP_MIN_CONFIDENCE) {
      panel.respEl.innerHTML = "";
      return;
    }
    panel.respEl.innerHTML =
      `<span>resp</span>` +
      `<span><b>${resp.breathsPerMin.toFixed(0)}</b> br/min</span>` +
      `<span class="est">est</span>`;
  }

  // With zones, the y-axis is FIXED to the participant's heart-rate range
  // (45%..105% of HRmax) so the intensity bands are stable, full-height
  // gridlines; readings outside the range clamp to the chart edges rather
  // than stretching (and compressing) the scale. Without zones it
  // auto-scales to the window as before.
  function sparklineScale(dataLo, dataHi, zones) {
    if (!zones || !Array.isArray(zones.divisors)) return [dataLo, dataHi];
    return [Math.round(zones.maxHr * 0.45), Math.round(zones.maxHr * 1.05)];
  }

  // Axis labels beside the chart. With zones: every gridline, as a percent of
  // max HR (50% / 65% / 80% / 100%). Without: the window min/max in BPM.
  function renderBounds(panel, zones, y, dataLo, dataHi) {
    const tick = (yPos, text) =>
      `<div class="tick" style="top:${((yPos / SPARK_H) * 100).toFixed(1)}%">${text}</div>`;
    if (zones && Array.isArray(zones.divisors)) {
      panel.boundsEl.innerHTML = zones.divisors
        .map((d) => tick(y(d), `${Math.round((d / zones.maxHr) * 100)}%`))
        .join("");
    } else {
      panel.boundsEl.innerHTML = tick(y(dataHi), dataHi) + tick(y(dataLo), dataLo);
    }
  }

  // Translucent band rects + hairlines at the divisors, behind the line.
  function zoneBandsSvg(zones, lo, hi, y) {
    if (!zones || !Array.isArray(zones.divisors)) return "";
    const edges = [lo, ...zones.divisors, hi];
    let svg = "";
    for (let i = 0; i < ZONE_NAMES.length; i++) {
      const top = y(Math.min(edges[i + 1], hi));
      const bottom = y(Math.max(edges[i], lo));
      if (bottom <= top) continue; // band entirely outside the visible range
      svg += `<rect class="band ${ZONE_NAMES[i]}" x="0" y="${top.toFixed(1)}" ` +
        `width="${SPARK_W}" height="${(bottom - top).toFixed(1)}"/>`;
    }
    for (const d of zones.divisors) {
      if (d <= lo || d >= hi) continue;
      svg += `<line class="band-line" x1="0" y1="${y(d).toFixed(1)}" ` +
        `x2="${SPARK_W}" y2="${y(d).toFixed(1)}"/>`;
    }
    return svg;
  }

  function renderSparkline(panel, samples, zones) {
    // Only draw samples within the window; older points (e.g. frozen during a
    // disconnect) are clipped relative to the client's clock.
    const cutoff = Date.now() - WINDOW_MS;
    const s = samples.filter(([t]) => t >= cutoff);

    if (!s.length) {
      panel.sparkEl.innerHTML = "";
      panel.boundsEl.innerHTML = "";
      return;
    }

    let dataLo = Infinity;
    let dataHi = -Infinity;
    for (const [, bpm] of s) {
      if (bpm < dataLo) dataLo = bpm;
      if (bpm > dataHi) dataHi = bpm;
    }

    const [lo, hi] = sparklineScale(dataLo, dataHi, zones);
    const span = hi - lo || 1; // avoid divide-by-zero on a flat line
    const innerH = SPARK_H - 2 * SPARK_PAD_Y;
    const x = (t) => ((t - cutoff) / WINDOW_MS) * SPARK_W;
    const y = (bpm) =>
      SPARK_PAD_Y + (1 - (Math.min(hi, Math.max(lo, bpm)) - lo) / span) * innerH;

    renderBounds(panel, zones, y, dataLo, dataHi);

    const pts = s.map(([t, bpm]) => `${x(t).toFixed(1)},${y(bpm).toFixed(1)}`);
    const [lastT, lastBpm] = s[s.length - 1];
    const bands = zoneBandsSvg(zones, lo, hi, y);
    // With zone bands, the area fill just muddies the band colors — skip it.
    const area = bands
      ? ""
      : `<path class="spark-area" d="M ${x(s[0][0]).toFixed(1)},${SPARK_H} ` +
        pts.map((pt) => `L ${pt}`).join(" ") +
        ` L ${x(lastT).toFixed(1)},${SPARK_H} Z"/>`;

    panel.sparkEl.innerHTML =
      `<svg viewBox="0 0 ${SPARK_W} ${SPARK_H}" preserveAspectRatio="none">` +
      bands +
      area +
      `<polyline class="spark-line" points="${pts.join(" ")}"/>` +
      `<circle class="spark-dot" cx="${x(lastT).toFixed(1)}" cy="${y(lastBpm).toFixed(1)}" r="3.5"/>` +
      `</svg>`;
  }

  function renderSession(panel, session) {
    if (!session || !session.count) {
      panel.sessionEl.innerHTML = "";
      return;
    }
    panel.sessionEl.innerHTML =
      `<span>session</span>` +
      `<span>min <b>${session.min}</b></span>` +
      `<span>avg <b>${session.avg}</b></span>` +
      `<span>max <b>${session.max}</b></span>`;
  }

  function render(state) {
    const seen = new Set();
    for (const p of state.participants || []) {
      // Only show participants a source has activated; an unconfigured (unpaired)
      // participant is never touched, so it stays hidden.
      if (!p.active) continue;
      seen.add(p.participantId);
      renderParticipant(p);
    }
    // Remove panels for participants no longer present or no longer active.
    for (const [id, panel] of panels) {
      if (!seen.has(id)) {
        panel.root.remove();
        panels.delete(id);
      }
    }
    // Null when no session is open (e.g. after an idle auto-close).
    const started = state.sessionStartedAt ? new Date(state.sessionStartedAt) : null;
    sessionStartedAt = started && !isNaN(started) ? started : null;
    // Only show the clock alongside panels — an idle overlay stays blank.
    renderSessionClock(seen.size > 0);
  }

  // Session clock: "Start: HH:MM · Duration: N minutes" at the bottom center.
  let sessionStartedAt = null;
  let clockVisible = false;

  function renderSessionClock(visible) {
    clockVisible = visible;
    if (!visible || !sessionStartedAt) {
      clockEl.textContent = "";
      return;
    }
    const hhmm = sessionStartedAt.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const mins = Math.max(0, Math.floor((Date.now() - sessionStartedAt) / 60000));
    clockEl.textContent =
      `Start: ${hhmm} · Duration: ${mins} ${mins === 1 ? "minute" : "minutes"}`;
  }

  // Keep the duration fresh even when no telemetry is arriving.
  setInterval(() => renderSessionClock(clockVisible), 30000);

  function setStatus(online) {
    statusEl.classList.toggle("online", online);
    statusEl.classList.toggle("offline", !online);
  }

  function connect() {
    const ws = new WebSocket(wsUrl());

    ws.addEventListener("open", () => {
      reconnectDelay = RECONNECT_MIN_MS;
      setStatus(true);
    });

    ws.addEventListener("message", (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "state") render(msg);
      } catch (err) {
        console.error("bad telemetry message", err);
      }
    });

    ws.addEventListener("close", () => {
      setStatus(false);
      scheduleReconnect();
    });

    ws.addEventListener("error", () => ws.close());
  }

  function scheduleReconnect() {
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
  }

  connect();
})();
