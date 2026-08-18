// Live compositing page: webcam + HR overlay in one tab, for sharing in Zoom.
//
// Wiring: strap.js (Web Bluetooth) -> hub.js (state accounting) ->
// ../overlay/render.js (the same renderer the OBS overlay uses). Readings are
// written through to IndexedDB so a tab reload doesn't lose the day's data;
// "Download JSONL" exports today's readings in the desktop history format.
//
// `?sim=2` runs two simulated straps for trying the page without hardware.

import { createOverlayRenderer } from "../overlay/render.js";
import { WebHub } from "./hub.js";
import { connectStrap } from "./strap.js";

const PROFILES_KEY = "bio-overlay-web.profiles"; // deviceId -> {name, birthYear}
const BATTERY_LOW_PCT = 20;
const BAR_HIDE_MS = 5000;
const DB_NAME = "bio-overlay-web";
const DB_STORE = "readings";

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

// ---------- participant profiles (name + birth year per strap) ----------

function loadProfiles() {
  try {
    return JSON.parse(localStorage.getItem(PROFILES_KEY)) || {};
  } catch {
    return {};
  }
}

function saveProfile(deviceId, profile) {
  const all = loadProfiles();
  all[deviceId] = profile;
  localStorage.setItem(PROFILES_KEY, JSON.stringify(all));
}

function profileFor(deviceId, suggestedName) {
  const existing = loadProfiles()[deviceId];
  if (existing) return existing;
  const name = (prompt(`Name for strap ${deviceId}?`, suggestedName) || suggestedName).trim();
  const byRaw = prompt(`Birth year for ${name}? (blank = assume age 65)`, "");
  const birthYear = /^\d{4}$/.test((byRaw || "").trim()) ? Number(byRaw.trim()) : null;
  const profile = { name, birthYear };
  saveProfile(deviceId, profile);
  return profile;
}

// ---------- IndexedDB write-through + JSONL export ----------

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const store = req.result.createObjectStore(DB_STORE, { autoIncrement: true });
      store.createIndex("day", "day");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

const dbPromise = openDb().catch((err) => {
  notice(`IndexedDB unavailable: ${err?.message ?? err}`);
  return null;
});

function localDay(atMs) {
  const d = new Date(atMs);
  return (
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-` +
    String(d.getDate()).padStart(2, "0")
  );
}

hub.onReading(async ({ id, index, bpm, rrMs, atMs }) => {
  const db = await dbPromise;
  if (!db) return;
  const p = hub.participants.get(id);
  db.transaction(DB_STORE, "readwrite").objectStore(DB_STORE).add({
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
  const rows = await new Promise((resolve, reject) => {
    const req = db
      .transaction(DB_STORE)
      .objectStore(DB_STORE)
      .index("day")
      .getAll(today);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
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

async function onConnectClick() {
  connectBtn.disabled = true;
  try {
    let ui = null;
    let pid = null;
    const strap = await connectStrap({
      onReading(m) {
        if (pid) hub.reading(pid, m.bpm, m.rrIntervalsMs);
      },
      onStatus(text, kind) {
        if (ui) ui.setDot(kind);
        if (kind !== "ok") notice(`${pid ?? "strap"}: ${text}`);
        if (pid) hub.setConnected(pid, kind === "ok");
      },
      onBattery(pct) {
        if (ui) ui.setBattery(pct);
        if (pct <= BATTERY_LOW_PCT) notice(`${pid}: battery low (${pct}%) — replace CR2025 soon`);
      },
    });
    pid = strap.deviceId || strap.name;
    const profile = profileFor(pid, strap.name.replace(/^Polar\s+/, ""));
    hub.registerParticipant(pid, {
      displayName: profile.name,
      birthYear: profile.birthYear,
    });
    hub.setConnected(pid, true);
    ui = addStrapChip(pid);
    ui.setLabel(profile.name);
    ui.setDot("ok");
    // Click the chip to rename / set birth year.
    ui.chip.addEventListener("click", () => {
      const name = (prompt(`Name for ${pid}?`, profile.name) || profile.name).trim();
      const byRaw = prompt(`Birth year for ${name}? (blank = assume age 65)`,
        profile.birthYear ?? "");
      profile.name = name;
      profile.birthYear = /^\d{4}$/.test((byRaw || "").trim()) ? Number(byRaw.trim()) : null;
      saveProfile(pid, profile);
      hub.registerParticipant(pid, { displayName: name, birthYear: profile.birthYear });
      ui.setLabel(name);
    });
    notice("");
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
    notice(`camera failed: ${err.message}`);
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
newSessionBtn.addEventListener("click", () => hub.resetSession());
exportBtn.addEventListener("click", exportJsonl);

if (!navigator.bluetooth) {
  connectBtn.disabled = true;
  notice("Web Bluetooth not available — use Chrome or Edge");
}

const sim = new URLSearchParams(location.search).get("sim");
if (sim) startSim(Number(sim) || 2);

// Render loop: 1 Hz is plenty; readings arrive ~1/s per strap anyway.
setInterval(() => renderer.render(hub.snapshot()), 1000);
