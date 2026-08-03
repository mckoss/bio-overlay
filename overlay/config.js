/*
 * Setup page: load/edit config.json, scan for straps, and pair them to
 * participants. Talks to the server's /api/config and /api/scan endpoints.
 */

(() => {
  "use strict";

  let config = { host: "127.0.0.1", port: 8080, staleAfterSeconds: 5.0, participants: [] };

  const participantsEl = document.getElementById("participants");
  const scanResultsEl = document.getElementById("scan-results");
  const statusEl = document.getElementById("status");
  const pathEl = document.getElementById("config-path");

  function el(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text != null) n.textContent = text;
    return n;
  }

  function field(label, value, oninput) {
    const wrap = el("div", "field");
    const lab = el("label", null, label);
    const input = el("input");
    input.value = value || "";
    input.addEventListener("input", () => oninput(input.value));
    wrap.append(lab, input);
    return wrap;
  }

  function renderParticipants() {
    participantsEl.innerHTML = "";
    const optInt = (v) => {
      const n = parseInt(v, 10);
      return Number.isFinite(n) ? n : null;
    };
    config.participants.forEach((p, i) => {
      const row = el("div", "participant");
      // Warn (live, as the fields change) when zones will fall back to the
      // default assumed age because neither Born nor Max HR is set.
      const warn = el("div", "zone-default-warn");
      const updateWarn = () => {
        warn.textContent =
          p.birthYear == null && p.maxHr == null
            ? "⚠ No birth year or max HR — intensity zones assume age 65."
            : "";
      };
      row.append(
        field("Display name", p.displayName, (v) => (p.displayName = v)),
        field("ID (key)", p.id, (v) => (p.id = v)),
        field("Device ID", p.deviceId, (v) => (p.deviceId = v || null)),
        // Intensity zones: birth year drives the Tanaka HRmax formula; a
        // known (tested) max HR overrides it. Both optional.
        field("Born", p.birthYear, (v) => {
          p.birthYear = optInt(v);
          updateWarn();
        }),
        field("Max HR", p.maxHr, (v) => {
          p.maxHr = optInt(v);
          updateWarn();
        })
      );
      const remove = el("button", "btn-remove", "Remove");
      remove.title = "Remove participant";
      remove.addEventListener("click", () => {
        config.participants.splice(i, 1);
        renderAll();
      });
      row.append(remove, warn);
      updateWarn();
      participantsEl.appendChild(row);
    });
  }

  // -- live session state --------------------------------------------------
  // The setup page listens on the telemetry WebSocket so Start-new-session
  // knows whether the current session already has data worth confirming over.
  // (Live per-strap status is visible in the overlay preview itself.)

  const live = new Map(); // participantId -> latest participant message

  function connectLive() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.addEventListener("message", (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type !== "state" || !Array.isArray(msg.participants)) return;
      live.clear();
      for (const p of msg.participants) live.set(p.participantId, p);
    });
    // The server may restart (or the page may outlive it); keep retrying.
    ws.addEventListener("close", () => setTimeout(connectLive, 2000));
  }

  // -- overlay preview ----------------------------------------------------
  // A real (scaled-down) iframe of the overlay page, so the setup page shows
  // exactly what OBS will: beating hearts, pulse rates, sparklines.

  const previewEl = document.getElementById("overlay-preview");
  const frameEl = document.getElementById("overlay-frame");

  function sizePreview() {
    // The overlay targets a 1920x1080 canvas; render it full-size in the
    // iframe and scale it down to the preview's width.
    frameEl.style.transform = `scale(${previewEl.clientWidth / 1920})`;
  }

  function hasSessionData() {
    for (const p of live.values()) {
      if (p.session && p.session.count > 0) return true;
    }
    return false;
  }

  function renderScan(devices) {
    scanResultsEl.innerHTML = "";
    if (!devices.length) {
      scanResultsEl.appendChild(el("p", "hint", "No straps found. Is one worn/active?"));
      return;
    }
    for (const d of devices) {
      const row = el("div", "device");
      row.append(el("span", "id", d.deviceId || "?"));
      row.append(el("span", "name", d.name));
      row.append(el("span", "spacer"));

      const select = el("select");
      select.appendChild(new Option("Assign to…", ""));
      config.participants.forEach((p, i) => {
        select.appendChild(new Option(p.displayName || p.id, String(i)));
      });
      const assign = el("button", "btn", "Assign");
      assign.addEventListener("click", () => {
        const idx = select.value;
        if (idx === "") return;
        config.participants[Number(idx)].deviceId = d.deviceId;
        renderParticipants();
        setStatus(`Assigned ${d.deviceId} to ${config.participants[Number(idx)].displayName}.`, "ok");
      });
      row.append(select, assign);
      scanResultsEl.appendChild(row);
    }
  }

  function renderAll() {
    renderParticipants();
  }

  function setStatus(msg, kind) {
    statusEl.textContent = msg;
    statusEl.className = "status" + (kind ? " " + kind : "");
  }

  async function load() {
    try {
      const res = await fetch("/api/config");
      const data = await res.json();
      config = data.config;
      if (!config.participants) config.participants = [];
      pathEl.textContent = data.path;
      if (data.version) {
        document.getElementById("version").textContent = "v" + data.version;
      }
      renderAll();
    } catch (err) {
      setStatus("Could not load config: " + err, "err");
    }
  }

  function addParticipant() {
    const n = config.participants.length + 1;
    config.participants.push({
      id: `participant-${n}`,
      displayName: `Participant ${n}`,
      deviceId: null,
      namePrefix: "Polar H10",
    });
    renderAll();
  }

  async function scan(btn) {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Scanning…";
    setStatus("Scanning for ~8s…", "");
    try {
      const res = await fetch("/api/scan");
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      renderScan(data.devices);
      setStatus(`Found ${data.devices.length} device(s).`, "ok");
    } catch (err) {
      setStatus("Scan failed: " + err.message, "err");
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  async function save() {
    // Basic client-side checks; the server validates too.
    for (const p of config.participants) {
      if (!p.id) return setStatus("Every participant needs an ID.", "err");
    }
    try {
      const res = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      const msg = data.applied
        ? "Saved and applied — no restart needed."
        : `Saved to ${data.path}.`;
      setStatus(msg, "ok");
    } catch (err) {
      setStatus("Save failed: " + err.message, "err");
    }
  }

  function setupOverlayLinks() {
    const overlayUrl = location.origin + "/";
    document.getElementById("overlay-url").textContent = overlayUrl;
    previewEl.href = overlayUrl;
    const copy = document.getElementById("copy-url");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(overlayUrl);
        const prev = copy.textContent;
        copy.textContent = "Copied!";
        setTimeout(() => (copy.textContent = prev), 1200);
      } catch {
        setStatus("Copy failed — select the URL and copy manually.", "err");
      }
    });
  }

  async function newSession() {
    if (
      hasSessionData() &&
      !confirm(
        "Start a new session? On-screen stats and sparklines reset for everyone. " +
          "(The finished session is kept in history.)"
      )
    ) {
      return;
    }
    try {
      const res = await fetch("/api/new-session", { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      // The preview resets over the WebSocket; the clock confirms it too.
      setStatus("New session started.", "ok");
    } catch (err) {
      setStatus("Could not start a new session: " + err.message, "err");
    }
  }

  async function quit() {
    if (!confirm("Quit bio-overlay? The overlay will go offline until you start the app again.")) {
      return;
    }
    try {
      await fetch("/api/quit", { method: "POST" });
    } catch {
      // The server closes the connection as it shuts down; that's expected.
    }
    document.body.innerHTML =
      '<main class="wrap"><h1>bio-overlay stopped</h1>' +
      "<p class='hint'>You can close this tab. Re-open the app to start again.</p></main>";
  }

  document.getElementById("add").addEventListener("click", addParticipant);
  document.getElementById("scan").addEventListener("click", (e) => scan(e.currentTarget));
  document.getElementById("save").addEventListener("click", save);
  document.getElementById("new-session").addEventListener("click", newSession);
  document.getElementById("quit").addEventListener("click", quit);

  window.addEventListener("resize", sizePreview);
  sizePreview();

  setupOverlayLinks();
  connectLive();
  load();
})();
