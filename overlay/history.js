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

      card.append(sparkline(p.points || [], p.zones));
      detailEl.appendChild(card);
    }
  }

  const ZONE_NAMES = ["rest", "z1", "z2", "z3", "z4", "z5", "over"];

  // Translucent intensity bands + hairlines at the divisors (when the current
  // config knows the participant's HR range). Bands span divisor-to-divisor
  // only (Z1..Z5) so the colored stripes line up exactly with the axis
  // labels; the rest/over margins beyond the outermost divisors stay
  // uncolored.
  function zoneBandsSvg(zones, lo, hi, y) {
    if (!zones || !Array.isArray(zones.divisors)) return "";
    const d = zones.divisors;
    let svg = "";
    for (let i = 0; i + 1 < d.length; i++) {
      const top = y(Math.min(d[i + 1], hi));
      const bottom = y(Math.max(d[i], lo));
      if (bottom <= top) continue;
      svg += `<rect class="band ${ZONE_NAMES[i + 1]}" x="0" y="${top.toFixed(1)}" ` +
        `width="${SPARK_W}" height="${(bottom - top).toFixed(1)}"/>`;
    }
    for (const div of d) {
      if (div <= lo || div >= hi) continue;
      svg += `<line class="band-line" x1="0" y1="${y(div).toFixed(1)}" ` +
        `x2="${SPARK_W}" y2="${y(div).toFixed(1)}"/>`;
    }
    return svg;
  }

  function sparkline(points, zones) {
    const wrap = el("div", "p-spark");
    if (points.length < 2) {
      wrap.appendChild(el("span", "hint", "not enough data"));
      return wrap;
    }
    let dataLo = Infinity, dataHi = -Infinity;
    const s0 = points[0][0], sN = points[points.length - 1][0];
    for (const [, bpm] of points) {
      if (bpm < dataLo) dataLo = bpm;
      if (bpm > dataHi) dataHi = bpm;
    }
    // With zones, fix the y-scale to the participant's HR range (45..105% of
    // HRmax) so the bands fill the chart; out-of-range readings clamp to the
    // edges. Without zones, auto-scale to the data as before.
    const banded = zones && Array.isArray(zones.divisors);
    let lo = dataLo, hi = dataHi;
    if (banded) {
      lo = Math.round(zones.maxHr * 0.45);
      hi = Math.round(zones.maxHr * 1.05);
    }
    const span = hi - lo || 1;
    const sSpan = sN - s0 || 1;
    const innerH = SPARK_H - 2 * SPARK_PAD;
    const x = (s) => ((s - s0) / sSpan) * SPARK_W;
    const y = (bpm) =>
      SPARK_PAD + (1 - (Math.min(hi, Math.max(lo, bpm)) - lo) / span) * innerH;
    const pts = points.map(([s, bpm]) => `${x(s).toFixed(1)},${y(bpm).toFixed(1)}`);
    const bands = zoneBandsSvg(zones, lo, hi, y);
    // With zone bands, the area fill just muddies the band colors — skip it.
    const area = bands
      ? ""
      : `<path class="spark-area" d="M ${x(s0).toFixed(1)},${SPARK_H} ` +
        pts.map((p) => `L ${p}`).join(" ") +
        ` L ${x(sN).toFixed(1)},${SPARK_H} Z"/>`;

    // Axis labels pinned to their gridlines: with zones, every divisor as a
    // percent of max HR (50%..100%); otherwise the data min/max in BPM.
    const pct = (v) => ((v / SPARK_H) * 100).toFixed(1) + "%";
    const labels = banded
      ? zones.divisors
          .map(
            (d) =>
              `<span class="y-tick" style="top:${pct(y(d))}">` +
              `${Math.round((d / zones.maxHr) * 100)}%</span>`
          )
          .join("")
      : `<span class="y-tick" style="top:${pct(y(dataHi))}">${dataHi}</span>` +
        `<span class="y-tick" style="top:${pct(y(dataLo))}">${dataLo}</span>`;

    wrap.innerHTML =
      `<svg viewBox="0 0 ${SPARK_W} ${SPARK_H}" preserveAspectRatio="none">` +
      bands +
      area +
      `<polyline class="spark-line" points="${pts.join(" ")}"/>` +
      `</svg>` +
      labels;
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
