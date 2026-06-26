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
    config.participants.forEach((p, i) => {
      const row = el("div", "participant");
      row.append(
        field("Display name", p.displayName, (v) => (p.displayName = v)),
        field("ID (key)", p.id, (v) => (p.id = v)),
        field("Device ID", p.deviceId, (v) => (p.deviceId = v || null))
      );
      const remove = el("button", "btn-remove", "Remove");
      remove.title = "Remove participant";
      remove.addEventListener("click", () => {
        config.participants.splice(i, 1);
        renderAll();
      });
      row.append(remove);
      participantsEl.appendChild(row);
    });
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
    document.getElementById("open-overlay").href = overlayUrl;
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

  document.getElementById("add").addEventListener("click", addParticipant);
  document.getElementById("scan").addEventListener("click", (e) => scan(e.currentTarget));
  document.getElementById("save").addEventListener("click", save);

  setupOverlayLinks();
  load();
})();
