/*
 * Overlay client: connects to the telemetry WebSocket and feeds state
 * snapshots to the shared renderer (render.js). Designed to be resilient to
 * OBS scene reloads and computer sleep by auto-reconnecting with backoff.
 */

import { createOverlayRenderer } from "./overlay/render.js";
import { createSessionBadge, createSessionControls } from "./session-controls.js";

const WS_PATH = "/ws";
const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 5000;

const statusEl = document.getElementById("status");

const renderer = createOverlayRenderer({
  panelsEl: document.getElementById("panels"),
  clockEl: document.getElementById("session-clock"),
});

// The badge shows everywhere the overlay does (including the setup page's
// preview iframe and OBS); the buttons are for a real browser tab only, so
// they never end up in the video or in the tiny preview.
const badge = createSessionBadge(document.getElementById("session-badge"));
const controls =
  window.top === window.self
    ? createSessionControls(document.getElementById("session-controls"), {
        hoverReveal: true,
        links: true,
      })
    : null;

// Debug/keying aid: `?bg=green` (or any CSS color) paints the otherwise
// transparent background so you can see exactly which area is overlay vs
// see-through. Leave it off for OBS — Browser Sources composite real alpha.
const bg = new URLSearchParams(location.search).get("bg");
if (bg) document.body.style.background = bg;

let reconnectDelay = RECONNECT_MIN_MS;

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${WS_PATH}`;
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
      if (msg.type !== "state") return;
      renderer.render(msg);
      const sessionState = msg.sessionState || "recording";
      badge.update(sessionState);
      if (controls) controls.update(sessionState);
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
