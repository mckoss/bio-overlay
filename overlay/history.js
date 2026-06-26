/*
 * History page: list recorded sessions and show a per-session detail with the
 * full-workout sparkline and statistics. Backed by /api/history and
 * /api/history/<id>.
 */

(() => {
  "use strict";

  const SPARK_W = 600;
  const SPARK_H = 90;
  const SPARK_PAD = 6;

  const listView = document.getElementById("list-view");
  const detailView = document.getElementById("detail-view");
  const sessionsEl = document.getElementById("sessions");
  const detailEl = document.getElementById("detail");

  function el(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text != null) n.textContent = text;
    return n;
  }

  function fmtDuration(totalSeconds) {
    const s = Math.max(0, Math.round(totalSeconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  }

  function fmtStarted(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  async function loadList() {
    sessionsEl.innerHTML = "Loading…";
    try {
      const res = await fetch("/api/history");
      const data = await res.json();
      renderList(data.sessions || []);
    } catch (err) {
      sessionsEl.textContent = "Could not load history: " + err;
    }
  }

  function renderList(sessions) {
    sessionsEl.innerHTML = "";
    if (!sessions.length) {
      sessionsEl.appendChild(el("p", "hint", "No recorded sessions yet."));
      return;
    }
    for (const s of sessions) {
      const card = el("button", "session");
      card.append(el("span", "when", fmtStarted(s.startedAt)));
      card.append(el("span", "dur", fmtDuration(s.durationS)));
      const who = (s.participants || []).join(", ") || "—";
      card.append(el("span", "who", who));
      card.addEventListener("click", () => loadDetail(s.id));
      sessionsEl.appendChild(card);
    }
  }

  async function loadDetail(id) {
    if (location.hash !== "#" + id) location.hash = id;
    detailEl.innerHTML = "Loading…";
    listView.hidden = true;
    detailView.hidden = false;
    try {
      const res = await fetch("/api/history/" + encodeURIComponent(id));
      if (!res.ok) throw new Error(await res.text());
      renderDetail(await res.json());
    } catch (err) {
      detailEl.textContent = "Could not load session: " + err.message;
    }
  }

  function renderDetail(session) {
    detailEl.innerHTML = "";
    const head = el("div", "detail-head");
    head.append(el("h2", null, fmtStarted(session.startedAt)));
    head.append(el("span", "dur-big", "duration " + fmtDuration(session.durationS)));
    detailEl.appendChild(head);

    for (const p of session.participants || []) {
      const card = el("div", "p-card");
      card.append(el("div", "p-name", p.name));

      const st = p.stats || {};
      const stats = el("div", "p-stats");
      stats.innerHTML =
        `<span>min <b>${st.min}</b></span>` +
        `<span>avg <b>${st.avg}</b></span>` +
        `<span>max <b>${st.max}</b></span>` +
        `<span>${st.count} samples</span>`;
      card.append(stats);

      card.append(sparkline(p.points || []));
      detailEl.appendChild(card);
    }
  }

  function sparkline(points) {
    const wrap = el("div", "p-spark");
    if (points.length < 2) {
      wrap.appendChild(el("span", "hint", "not enough data"));
      return wrap;
    }
    let lo = Infinity, hi = -Infinity, s0 = points[0][0], sN = points[points.length - 1][0];
    for (const [, bpm] of points) {
      if (bpm < lo) lo = bpm;
      if (bpm > hi) hi = bpm;
    }
    const span = hi - lo || 1;
    const sSpan = sN - s0 || 1;
    const innerH = SPARK_H - 2 * SPARK_PAD;
    const x = (s) => ((s - s0) / sSpan) * SPARK_W;
    const y = (bpm) => SPARK_PAD + (1 - (bpm - lo) / span) * innerH;
    const pts = points.map(([s, bpm]) => `${x(s).toFixed(1)},${y(bpm).toFixed(1)}`);
    const area =
      `M ${x(s0).toFixed(1)},${SPARK_H} ` +
      pts.map((p) => `L ${p}`).join(" ") +
      ` L ${x(sN).toFixed(1)},${SPARK_H} Z`;

    wrap.innerHTML =
      `<svg viewBox="0 0 ${SPARK_W} ${SPARK_H}" preserveAspectRatio="none">` +
      `<path class="spark-area" d="${area}"/>` +
      `<polyline class="spark-line" points="${pts.join(" ")}"/>` +
      `</svg>` +
      `<span class="y-max">${hi}</span><span class="y-min">${lo}</span>`;
    return wrap;
  }

  document.getElementById("back").addEventListener("click", () => {
    location.hash = "";
    detailView.hidden = true;
    listView.hidden = false;
  });

  // Deep-link: /history#<session-id> opens that session directly.
  const initial = decodeURIComponent(location.hash.replace(/^#/, ""));
  loadList().then(() => {
    if (initial) loadDetail(initial);
  });
})();
