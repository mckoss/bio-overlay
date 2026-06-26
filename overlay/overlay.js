/*
 * Overlay client: connects to the telemetry WebSocket and renders participant
 * panels. Designed to be resilient to OBS scene reloads and computer sleep by
 * auto-reconnecting with backoff.
 */

(() => {
  "use strict";

  const WS_PATH = "/ws";
  const RECONNECT_MIN_MS = 500;
  const RECONNECT_MAX_MS = 5000;

  const panelsEl = document.getElementById("panels");
  const statusEl = document.getElementById("status");

  // participantId -> { root, nameEl, bpmEl, badgeEl }
  const panels = new Map();
  let reconnectDelay = RECONNECT_MIN_MS;

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${WS_PATH}`;
  }

  function ensurePanel(participantId) {
    let panel = panels.get(participantId);
    if (panel) return panel;

    const root = document.createElement("div");
    root.className = "panel";

    const nameEl = document.createElement("div");
    nameEl.className = "name";

    const bpmEl = document.createElement("div");
    bpmEl.className = "bpm";
    bpmEl.textContent = "--";

    const unitEl = document.createElement("div");
    unitEl.className = "unit";
    unitEl.textContent = "BPM";

    const badgeEl = document.createElement("div");
    badgeEl.className = "badge";

    root.append(nameEl, bpmEl, unitEl, badgeEl);
    panelsEl.appendChild(root);

    panel = { root, nameEl, bpmEl, badgeEl };
    panels.set(participantId, panel);
    return panel;
  }

  function renderParticipant(p) {
    const panel = ensurePanel(p.participantId);
    panel.nameEl.textContent = p.displayName;

    const offline = p.stale || !p.connected || p.bpm == null;
    panel.root.classList.toggle("stale", offline);

    if (p.bpm != null) {
      panel.bpmEl.textContent = String(p.bpm);
      // Drive the heartbeat animation period from the live BPM.
      const beatSeconds = Math.max(0.3, Math.min(2, 60 / p.bpm));
      panel.root.style.setProperty("--beat", `${beatSeconds}s`);
    } else {
      panel.bpmEl.textContent = "--";
    }

    // Badge text: ".stale" CSS shows "no signal"; otherwise clear it.
    panel.badgeEl.textContent = offline ? "" : "";
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
