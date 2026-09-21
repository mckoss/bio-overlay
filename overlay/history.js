/*
 * Desktop history page: the shared history UI (history-ui.js) backed by the
 * server's /api/history endpoints.
 */

import { createHistoryPage } from "./overlay/history-ui.js";
import {
  STATE_TEXT,
  createSessionControls,
  watchSession,
} from "./session-controls.js";

async function checkOk(res) {
  if (!res.ok) throw new Error(await res.text());
  return res;
}

// Live recording status + controls. A newly recorded session doesn't show up
// in the list until it's stopped/reloaded, so this also explains why.
const stripEl = document.getElementById("session-strip");
const stateEl = document.getElementById("session-state");
const sinceEl = document.getElementById("session-since");
const controls = createSessionControls(document.getElementById("session-controls"), {
  onError: (err) => (sinceEl.textContent = "Failed: " + err.message),
});

watchSession((msg) => {
  const state = msg.sessionState || "recording";
  stripEl.hidden = false;
  stripEl.classList.toggle("not-recording", state !== "recording");
  stateEl.textContent = STATE_TEXT[state] || state;
  const started = msg.sessionStartedAt ? new Date(msg.sessionStartedAt) : null;
  sinceEl.textContent =
    started && !isNaN(started)
      ? `session started ${started.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
      : state === "recording"
        ? "armed — the next reading starts a session"
        : "";
  controls.update(state);
});

createHistoryPage({
  listView: document.getElementById("list-view"),
  detailView: document.getElementById("detail-view"),
  sessionsEl: document.getElementById("sessions"),
  detailEl: document.getElementById("detail"),
  backEl: document.getElementById("back"),
  api: {
    async list() {
      return (await checkOk(await fetch("/api/history"))).json();
    },
    async get(id) {
      return (await checkOk(await fetch("/api/history/" + encodeURIComponent(id)))).json();
    },
    async delete(id) {
      await checkOk(await fetch("/api/history/" + encodeURIComponent(id), { method: "DELETE" }));
    },
  },
});
