/*
 * Desktop session controls: start / pause / stop recording, plus the red
 * "not recording" badge. Shared by the overlay page and the history page;
 * both are driven by the telemetry snapshot's `sessionState`
 * ("recording" | "paused" | "stopped").
 *
 * The overlay page doubles as an OBS Browser Source, so its buttons only
 * appear once the mouse moves over the page — OBS never sends mouse events,
 * so they stay out of the video. The red badge is deliberately NOT hidden
 * that way: when nothing is being recorded, that should be visible on camera.
 */

const HIDE_AFTER_MS = 3000;

export const STATE_TEXT = {
  recording: "Recording",
  paused: "Paused · not recording",
  stopped: "Stopped · not recording",
};

// Badge copy for the two states where no data is being recorded.
const BADGE_TEXT = {
  paused: "⏸ PAUSED · NOT RECORDING",
  stopped: "⏹ SESSION STOPPED · NOT RECORDING",
};

// Buttons per state: [label, action, className]. "new" starts a fresh
// session (and lifts a pause/stop); "resume" continues a paused one.
const BUTTONS = {
  recording: [
    ["⏸ Pause", "pause", ""],
    ["⏹ Stop", "stop", ""],
  ],
  paused: [
    ["▶ Resume", "resume", "primary"],
    ["↻ New session", "new", ""],
    ["⏹ Stop", "stop", ""],
  ],
  stopped: [["▶ Start new session", "new", "primary"]],
};

export async function sessionAction(action) {
  const res = await fetch(`/api/session/${action}`, { method: "POST" });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
}

/**
 * Subscribe to telemetry snapshots. Used by pages that don't already hold a
 * WebSocket of their own (the overlay page feeds its state in directly).
 * Reconnects for the life of the page, so a server restart heals itself.
 */
export function watchSession(onState) {
  let delay = 500;
  const connect = () => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.addEventListener("open", () => (delay = 500));
    ws.addEventListener("message", (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "state") onState(msg);
      } catch {
        /* ignore malformed frames */
      }
    });
    ws.addEventListener("close", () => {
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 5000);
    });
    ws.addEventListener("error", () => ws.close());
  };
  connect();
}

/**
 * The red not-recording badge. Clicking it does the obvious thing: resume a
 * paused session, or start a new one when stopped.
 */
export function createSessionBadge(el, { onError } = {}) {
  let shown = null;
  el.addEventListener("click", async () => {
    if (!shown) return;
    try {
      await sessionAction(shown === "paused" ? "resume" : "new");
    } catch (err) {
      if (onError) onError(err);
    }
  });
  return {
    update(state) {
      const text = BADGE_TEXT[state] || "";
      if (state === shown) return;
      shown = text ? state : null;
      el.textContent = text;
      el.hidden = !text;
      el.title = text ? "Click to start recording again" : "";
    },
  };
}

/**
 * The button bar. `hoverReveal` hides it while recording until the mouse
 * moves (for the overlay page); the not-recording states always show it,
 * since the overlay is otherwise blank then anyway.
 */
export function createSessionControls(el, { hoverReveal = false, links = false, onError } = {}) {
  let rendered = null;
  let hideTimer = null;

  const run = async (action) => {
    try {
      await sessionAction(action);
    } catch (err) {
      if (onError) onError(err);
    }
  };

  function button(label, action, className) {
    const b = document.createElement("button");
    b.className = "session-btn" + (className ? " " + className : "");
    b.textContent = label;
    b.addEventListener("click", () => run(action));
    return b;
  }

  function render(state) {
    el.innerHTML = "";
    for (const [label, action, className] of BUTTONS[state] || []) {
      el.appendChild(button(label, action, className));
    }
    if (links) {
      for (const [label, href] of [["⚙ Setup", "/config"], ["History", "/history"]]) {
        const a = document.createElement("a");
        a.className = "session-btn link";
        a.textContent = label;
        a.href = href;
        a.target = "_blank";
        a.rel = "noopener";
        el.appendChild(a);
      }
    }
  }

  function reveal() {
    el.classList.add("revealed");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (rendered === "recording") el.classList.remove("revealed");
    }, HIDE_AFTER_MS);
  }

  if (hoverReveal) {
    for (const ev of ["mousemove", "pointerdown", "keydown"]) {
      window.addEventListener(ev, reveal);
    }
  } else {
    el.classList.add("revealed");
  }

  return {
    update(state) {
      if (state === rendered) return;
      rendered = state;
      render(state);
      el.hidden = false;
      // Only a live session hides itself; stopped/paused stay on screen.
      if (hoverReveal && state !== "recording") el.classList.add("revealed");
    },
  };
}
