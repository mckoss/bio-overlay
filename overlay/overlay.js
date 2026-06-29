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
    const respEl = el("div", "resp");
    const badgeEl = el("div", "badge");

    root.append(nameEl, metrics, sessionEl, respEl, badgeEl);
    panelsEl.appendChild(root);

    panel = { root, nameEl, bpmEl, sparkEl, maxEl, minEl, sessionEl, respEl, badgeEl };
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

    // History comes from the server, so a reload restores it immediately.
    renderSparkline(panel, p.samples || []);
    renderSession(panel, p.session);
    renderRespiration(panel, p.respiration);
    panel.badgeEl.textContent = "";
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

  function renderSparkline(panel, samples) {
    // Only draw samples within the window; older points (e.g. frozen during a
    // disconnect) are clipped relative to the client's clock.
    const cutoff = Date.now() - WINDOW_MS;
    const s = samples.filter(([t]) => t >= cutoff);

    if (!s.length) {
      panel.sparkEl.innerHTML = "";
      panel.maxEl.textContent = "--";
      panel.minEl.textContent = "--";
      return;
    }

    let lo = Infinity;
    let hi = -Infinity;
    for (const [, bpm] of s) {
      if (bpm < lo) lo = bpm;
      if (bpm > hi) hi = bpm;
    }
    panel.maxEl.textContent = String(hi);
    panel.minEl.textContent = String(lo);

    const span = hi - lo || 1; // avoid divide-by-zero on a flat line
    const innerH = SPARK_H - 2 * SPARK_PAD_Y;
    const x = (t) => ((t - cutoff) / WINDOW_MS) * SPARK_W;
    const y = (bpm) => SPARK_PAD_Y + (1 - (bpm - lo) / span) * innerH;

    const pts = s.map(([t, bpm]) => `${x(t).toFixed(1)},${y(bpm).toFixed(1)}`);
    const [lastT, lastBpm] = s[s.length - 1];
    const area =
      `M ${x(s[0][0]).toFixed(1)},${SPARK_H} ` +
      pts.map((pt) => `L ${pt}`).join(" ") +
      ` L ${x(lastT).toFixed(1)},${SPARK_H} Z`;

    panel.sparkEl.innerHTML =
      `<svg viewBox="0 0 ${SPARK_W} ${SPARK_H}" preserveAspectRatio="none">` +
      `<path class="spark-area" d="${area}"/>` +
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
