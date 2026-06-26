/*
 * Overlay client: connects to the telemetry WebSocket and renders participant
 * panels. Designed to be resilient to OBS scene reloads and computer sleep by
 * auto-reconnecting with backoff.
 *
 * Each panel shows the live BPM, a sparkline of the last 5 minutes (with the
 * window's min/max), and whole-session min/avg/max. History is kept in memory
 * only and resets when the page reloads (no biometric data is stored).
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
  // participantId -> { samples: [{t, bpm}], lastAt, sMin, sMax, sSum, sCount }
  const histories = new Map();
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

    const metrics = el("div", "metrics");
    const live = el("div", "live");
    const bpmEl = el("div", "bpm", "--");
    const unitEl = el("div", "unit", "BPM");
    live.append(bpmEl, unitEl);

    const spark = el("div", "spark");
    const sparkEl = el("div", "spark-svg");
    const bounds = el("div", "spark-bounds");
    const maxEl = el("div", "spark-max", "--");
    const minEl = el("div", "spark-min", "--");
    bounds.append(maxEl, minEl);
    spark.append(sparkEl, bounds);

    metrics.append(live, spark);

    const sessionEl = el("div", "session");
    const badgeEl = el("div", "badge");

    root.append(nameEl, metrics, sessionEl, badgeEl);
    panelsEl.appendChild(root);

    panel = { root, nameEl, bpmEl, sparkEl, maxEl, minEl, sessionEl, badgeEl };
    panels.set(participantId, panel);
    histories.set(participantId, {
      samples: [],
      lastAt: null,
      sMin: null,
      sMax: null,
      sSum: 0,
      sCount: 0,
    });
    return panel;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function recordSample(hist, bpm, atMs) {
    hist.samples.push({ t: atMs, bpm });
    hist.lastAt = atMs;
    hist.sMin = hist.sMin == null ? bpm : Math.min(hist.sMin, bpm);
    hist.sMax = hist.sMax == null ? bpm : Math.max(hist.sMax, bpm);
    hist.sSum += bpm;
    hist.sCount += 1;
  }

  function renderParticipant(p) {
    const panel = ensurePanel(p.participantId);
    const hist = histories.get(p.participantId);
    panel.nameEl.textContent = p.displayName;

    // A reading counts only when it's a live, fresh, non-zero BPM. bpm == 0 is
    // the H10 reporting "no heartbeat detected" (loose contact), not real data.
    const live = p.connected && !p.stale && p.bpm != null && p.bpm > 0;
    const atMs = p.updatedAt ? new Date(p.updatedAt).getTime() : NaN;
    if (live && Number.isFinite(atMs) && atMs !== hist.lastAt) {
      recordSample(hist, p.bpm, atMs);
    }

    const offline = !live;
    panel.root.classList.toggle("stale", offline);

    if (p.bpm != null) {
      panel.bpmEl.textContent = String(p.bpm);
      const beatSeconds = Math.max(0.3, Math.min(2, 60 / Math.max(p.bpm, 1)));
      panel.root.style.setProperty("--beat", `${beatSeconds}s`);
    } else {
      panel.bpmEl.textContent = "--";
    }

    renderSparkline(panel, hist);
    renderSession(panel, hist);
    panel.badgeEl.textContent = "";
  }

  function renderSparkline(panel, hist) {
    // Keep only the last WINDOW_MS of samples in memory.
    const now = Date.now();
    const cutoff = now - WINDOW_MS;
    const s = hist.samples;
    while (s.length && s[0].t < cutoff) s.shift();

    if (!s.length) {
      panel.sparkEl.innerHTML = "";
      panel.maxEl.textContent = "--";
      panel.minEl.textContent = "--";
      return;
    }

    let lo = Infinity;
    let hi = -Infinity;
    for (const pt of s) {
      if (pt.bpm < lo) lo = pt.bpm;
      if (pt.bpm > hi) hi = pt.bpm;
    }
    panel.maxEl.textContent = String(hi);
    panel.minEl.textContent = String(lo);

    const span = hi - lo || 1; // avoid divide-by-zero on a flat line
    const innerH = SPARK_H - 2 * SPARK_PAD_Y;
    const x = (t) => ((t - cutoff) / WINDOW_MS) * SPARK_W;
    const y = (bpm) => SPARK_PAD_Y + (1 - (bpm - lo) / span) * innerH;

    const pts = s.map((pt) => `${x(pt.t).toFixed(1)},${y(pt.bpm).toFixed(1)}`);
    const last = s[s.length - 1];
    const area =
      `M ${x(s[0].t).toFixed(1)},${SPARK_H} ` +
      pts.map((p) => `L ${p}`).join(" ") +
      ` L ${x(last.t).toFixed(1)},${SPARK_H} Z`;

    panel.sparkEl.innerHTML =
      `<svg viewBox="0 0 ${SPARK_W} ${SPARK_H}" preserveAspectRatio="none">` +
      `<path class="spark-area" d="${area}"/>` +
      `<polyline class="spark-line" points="${pts.join(" ")}"/>` +
      `<circle class="spark-dot" cx="${x(last.t).toFixed(1)}" cy="${y(last.bpm).toFixed(1)}" r="2.5"/>` +
      `</svg>`;
  }

  function renderSession(panel, hist) {
    if (!hist.sCount) {
      panel.sessionEl.innerHTML = "";
      return;
    }
    const avg = Math.round(hist.sSum / hist.sCount);
    panel.sessionEl.innerHTML =
      `<span>session</span>` +
      `<span>min <b>${hist.sMin}</b></span>` +
      `<span>avg <b>${avg}</b></span>` +
      `<span>max <b>${hist.sMax}</b></span>`;
  }

  function render(state) {
    const seen = new Set();
    for (const p of state.participants || []) {
      seen.add(p.participantId);
      renderParticipant(p);
    }
    // Remove panels for participants no longer present.
    for (const [id, panel] of panels) {
      if (!seen.has(id)) {
        panel.root.remove();
        panels.delete(id);
        histories.delete(id);
      }
    }
  }

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
