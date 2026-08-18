// Live compositing page: webcam + HR overlay in one tab, for sharing in Zoom.
//
// Wiring: strap.js (Web Bluetooth) -> hub.js (state accounting) ->
// ./overlay/render.js (the same renderer the OBS overlay uses). Readings are
// written through to IndexedDB so a tab reload doesn't lose the day's data;
// "Download JSONL" exports today's readings in the desktop history format.
//
// `?sim=2` runs two simulated straps for trying the page without hardware.

import { createOverlayRenderer } from "./overlay/render.js";
import { WebHub } from "./hub.js";
import { requestStrap, streamStrap, deviceIdFromName } from "./strap.js";
import { openDb, addReading, getAllReadings, localDay } from "./db.js";
import { loadProfiles, saveProfile } from "./profiles.js";

const BATTERY_LOW_PCT = 20;
const BAR_HIDE_MS = 5000;

const hub = new WebHub();
const renderer = createOverlayRenderer({
  panelsEl: document.getElementById("panels"),
  clockEl: document.getElementById("session-clock"),
});

const cameraEl = document.getElementById("camera");
const barEl = document.getElementById("bar");
const strapsEl = document.getElementById("straps");
const noticeEl = document.getElementById("notice");
const connectBtn = document.getElementById("connect");
const cameraBtn = document.getElementById("camera-btn");
const mirrorBtn = document.getElementById("mirror-btn");
const newSessionBtn = document.getElementById("new-session");
const exportBtn = document.getElementById("export");

function notice(msg) {
  noticeEl.textContent = msg;
}

// ---------- profile editor (inline UI, replaces prompt dialogs) ----------

const editorEl = document.getElementById("profile-editor");
const peDevice = document.getElementById("pe-device");
const peName = document.getElementById("pe-name");
const peBy = document.getElementById("pe-by");
let editorSave = null; // active Save handler, swapped per open

function openProfileEditor(deviceId, profile, onSave) {
  peDevice.textContent = deviceId;
  peName.value = profile.name ?? "";
  peBy.value = profile.birthYear ?? "";
  editorSave = () => {
    const name = peName.value.trim() || profile.name || deviceId;
    const byRaw = peBy.value.trim();
    const birthYear = /^\d{4}$/.test(byRaw) ? Number(byRaw) : null;
    const updated = { name, birthYear };
    saveProfile(deviceId, updated);
    editorEl.classList.add("hidden");
    onSave(updated);
  };
  editorEl.classList.remove("hidden");
  peName.focus();
  peName.select();
}

document.getElementById("pe-save").addEventListener("click", () => editorSave?.());
document.getElementById("pe-cancel").addEventListener("click", () =>
  editorEl.classList.add("hidden"));
editorEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") editorSave?.();
  if (e.key === "Escape") editorEl.classList.add("hidden");
});

// ---------- IndexedDB write-through + JSONL export ----------

const dbPromise = openDb().catch((err) => {
  notice(`IndexedDB unavailable: ${err?.message ?? err}`);
  return null;
});

hub.onReading(async ({ id, index, bpm, rrMs, atMs }) => {
  const db = await dbPromise;
  if (!db) return;
  const p = hub.participants.get(id);
  addReading(db, {
    day: localDay(atMs),
    sessionStart: hub.sessionStartedAtMs,
    pid: id,
    name: p?.displayName ?? id,
    index,
    atMs,
    bpm,
    rr: rrMs.map((v) => Math.round(v * 10) / 10),
  });
  exportBtn.disabled = false;
});

async function exportJsonl() {
  const db = await dbPromise;
  if (!db) return;
  const today = localDay(Date.now());
  const rows = await getAllReadings(db, today);
  if (!rows.length) return;

  // Desktop history format: a session header line, then compact reading lines
  // with seconds relative to the session start.
  const lines = [];
  let currentSession = null;
  for (const r of rows) {
    if (r.sessionStart !== currentSession) {
      currentSession = r.sessionStart;
      const ids = [...new Map(rows.filter((x) => x.sessionStart === r.sessionStart)
        .map((x) => [x.index, x])).values()]
        .sort((a, b) => a.index - b.index);
      lines.push(JSON.stringify({
        session: new Date(r.sessionStart ?? r.atMs).toISOString(),
        participants: ids.map((x) => ({ id: x.pid, name: x.name, deviceId: x.pid })),
      }));
    }
    lines.push(JSON.stringify({
      s: Math.round((r.atMs - (r.sessionStart ?? r.atMs)) / 1000),
      p: r.index,
      bpm: r.bpm,
      rr: r.rr,
    }));
  }
  const blob = new Blob([lines.join("\n") + "\n"], { type: "application/jsonl" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${today}.jsonl`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---------- straps ----------

function addStrapChip(deviceId) {
  const chip = document.createElement("span");
  chip.className = "strap-chip";
  chip.innerHTML =
    `<span class="dot"></span><span class="label"></span><span class="batt"></span>`;
  chip.querySelector(".label").textContent = deviceId;
  strapsEl.appendChild(chip);
  return {
    chip,
    setLabel(text) { chip.querySelector(".label").textContent = text; },
    setDot(kind) { chip.querySelector(".dot").className = `dot ${kind}`; },
    setBattery(pct) {
      const el = chip.querySelector(".batt");
      el.textContent = `${pct}%`;
      el.classList.toggle("low", pct <= BATTERY_LOW_PCT);
    },
  };
}

// device.id (stable per device+origin) -> {pid, ui} — guards against
// connecting the same strap twice, which would duplicate the chip and
// double-count every reading via a second notification listener.
const connected = new Map();

function applyProfile(pid, ui, profile) {
  hub.registerParticipant(pid, {
    displayName: profile.name,
    birthYear: profile.birthYear,
  });
  ui.setLabel(profile.name);
}

async function onConnectClick() {
  connectBtn.disabled = true;
  try {
    const device = await requestStrap();
    if (connected.has(device.id)) {
      notice(`${connected.get(device.id).pid} is already connected`);
      return;
    }
    const pid = deviceIdFromName(device.name) || device.name || device.id;
    // Register (with any stored profile) BEFORE streaming so the first
    // readings already carry the right display name.
    const profile = loadProfiles()[pid] ??
      { name: (device.name || pid).replace(/^Polar\s+/, ""), birthYear: null };
    const ui = addStrapChip(pid);
    connected.set(device.id, { pid, ui });
    applyProfile(pid, ui, profile);
    // Click the chip to rename / set birth year.
    ui.chip.addEventListener("click", () =>
      openProfileEditor(pid, loadProfiles()[pid] ?? profile,
        (updated) => applyProfile(pid, ui, updated)));

    try {
      await streamStrap(device, {
        onReading(m) {
          hub.reading(pid, m.bpm, m.rrIntervalsMs);
        },
        onStatus(text, kind) {
          ui.setDot(kind);
          if (kind !== "ok") notice(`${pid}: ${text}`);
          hub.setConnected(pid, kind === "ok");
        },
        onBattery(pct) {
          ui.setBattery(pct);
          if (pct <= BATTERY_LOW_PCT) notice(`${pid}: battery low (${pct}%) — replace CR2025 soon`);
        },
      });
    } catch (err) {
      // Initial connection failed: undo the chip so a retry starts clean.
      ui.chip.remove();
      connected.delete(device.id);
      throw err;
    }
    hub.setConnected(pid, true);
    notice("");
    // First time seeing this strap: ask for name/birth year via the inline
    // editor (readings keep flowing under the default meanwhile).
    if (!loadProfiles()[pid]) {
      openProfileEditor(pid, profile, (updated) => applyProfile(pid, ui, updated));
    }
  } catch (err) {
    if (err.name !== "NotFoundError") notice(`connect failed: ${err.message}`);
  } finally {
    connectBtn.disabled = false;
  }
}

// ---------- camera ----------

let cameraStream = null;

async function toggleCamera() {
  if (cameraStream) {
    for (const track of cameraStream.getTracks()) track.stop();
    cameraStream = null;
    cameraEl.srcObject = null;
    cameraEl.classList.remove("on");
    cameraBtn.textContent = "Start camera";
    return;
  }
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
    cameraEl.srcObject = cameraStream;
    cameraEl.classList.add("on");
    cameraBtn.textContent = "Stop camera";
  } catch (err) {
    // NotReadableError/AbortError = the device is held by another app —
    // common on Windows, where webcams are exclusive-access.
    if (err.name === "NotReadableError" || err.name === "AbortError") {
      notice(
        "camera is in use by another app — turn off your Zoom video " +
        "(or close other camera apps), then click Start camera again"
      );
    } else if (err.name === "NotAllowedError") {
      notice(
        "camera access is blocked — allow the camera for this site " +
        "(click the icon by the address bar), then click Start camera again"
      );
    } else {
      notice(`camera failed: ${err.message}`);
    }
  }
}

// ---------- control bar auto-hide ----------

let hideTimer = null;

function showBar() {
  barEl.classList.remove("hidden");
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => barEl.classList.add("hidden"), BAR_HIDE_MS);
}

document.addEventListener("mousemove", showBar);
document.addEventListener("keydown", showBar);
showBar();

// ---------- simulated straps (?sim=N) ----------

function startSim(n) {
  const profiles = [
    { id: "SIM-A", name: "Mike", birthYear: 1960, base: 105 },
    { id: "SIM-B", name: "Debbie", birthYear: 1962, base: 95 },
    { id: "SIM-C", name: "Sim C", birthYear: null, base: 120 },
  ].slice(0, Math.min(n, 3));
  for (const p of profiles) {
    hub.registerParticipant(p.id, { displayName: p.name, birthYear: p.birthYear });
    hub.setConnected(p.id, true);
    let bpm = p.base;
    const chip = addStrapChip(p.id);
    chip.setLabel(`${p.name} (sim)`);
    chip.setDot("ok");
    chip.setBattery(87);
    setInterval(() => {
      bpm = Math.max(55, Math.min(175, bpm + (Math.random() - 0.48) * 4));
      const rr = 60000 / bpm;
      hub.reading(p.id, Math.round(bpm), [rr, rr * 1.02]);
    }, 1000);
  }
  notice(`simulating ${profiles.length} strap(s)`);
}

// ---------- wiring ----------

connectBtn.addEventListener("click", onConnectClick);
cameraBtn.addEventListener("click", toggleCamera);
mirrorBtn.addEventListener("click", () => cameraEl.classList.toggle("mirrored"));
newSessionBtn.addEventListener("click", () => {
  hub.resetSession();
  renderer.render(hub.snapshot()); // clear panels now, not at the next tick
  notice("new session started — stats, sparklines, and zone times cleared");
});
exportBtn.addEventListener("click", exportJsonl);

if (!navigator.bluetooth) {
  connectBtn.disabled = true;
  notice("Web Bluetooth not available — use Chrome or Edge");
}

const sim = new URLSearchParams(location.search).get("sim");
if (sim) startSim(Number(sim) || 2);

// Render loop: 1 Hz is plenty; readings arrive ~1/s per strap anyway.
setInterval(() => renderer.render(hub.snapshot()), 1000);
// Dev aid: `?editor` opens the profile editor with sample data so its layout
// can be checked (e.g. in a headless screenshot) without connecting a strap.
if (new URLSearchParams(location.search).has("editor")) {
  openProfileEditor("16CD9E3C", { name: "Mike", birthYear: 1960 }, () => {});
}
// Dev aid: `?reset=MS` clicks New session after MS, to verify the reset path
// in a headless screenshot.
const resetMs = Number(new URLSearchParams(location.search).get("reset"));
if (resetMs) setTimeout(() => newSessionBtn.click(), resetMs);
