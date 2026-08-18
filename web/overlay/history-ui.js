/*
 * History page UI, shared by the desktop app (overlay/history.js, backed by
 * /api/history) and the web version (web/history.js, backed by IndexedDB).
 *
 * Lists recorded sessions and shows a per-session detail with the
 * full-workout sparkline and statistics. The data source is injected:
 *
 *   api = {
 *     list():   Promise<{sessions: [{id, startedAt, durationS,
 *                 participants: [name], zoneTimes?: [{name, timesS}]}]}>,
 *     get(id):  Promise<{id, startedAt, durationS,
 *                 participants: [{name, points: [[s, bpm]], stats, zones}]}>,
 *     delete(id): Promise<void>,   // throws on failure
 *   }
 */

const SPARK_W = 600;
const SPARK_H = 90;
const SPARK_PAD = 6;

const ZONE_NAMES = ["rest", "z1", "z2", "z3", "z4", "z5", "over"];
const ZONE_TIME_LABELS = ["Rest", "Z1", "Z2", "Z3", "Z4", "Z5", "Max"];
// Gaps between samples longer than this are dropouts — not attributed to
// any zone. The history file records one sample per ~5s, so this must
// comfortably exceed that cadence (matches the server's ZONE_MAX_GAP_S).
const ZONE_MAX_GAP_S = 15;
// The bar's full track width represents at least this much time.
const ZONEBAR_MIN_SCALE_S = 60 * 60;

export function createHistoryPage({ listView, detailView, sessionsEl, detailEl, backEl, api }) {
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
      const data = await api.list();
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
      // Inline time-in-zone bars (one per participant), all on the same
      // max(1 hour, duration) scale so sessions compare at a glance.
      const zt = s.zoneTimes || [];
      if (zt.length) {
        const bars = el("span", "list-zonebars");
        const scaleS = Math.max(ZONEBAR_MIN_SCALE_S, s.durationS || 0);
        for (const z of zt) {
          const bar = zoneBarEl(z.timesS || [], scaleS);
          if (!bar) continue;
          const row = el("span", "list-zonebar-row");
          if (zt.length > 1) row.append(el("span", "zl-name", z.name || ""));
          row.append(bar);
          bars.append(row);
        }
        card.append(bars);
      }
      card.addEventListener("click", () => loadDetail(s.id));

      const row = el("div", "session-row");
      const del = el("button", "btn-delete", "✕");
      del.title = "Delete this session";
      del.addEventListener("click", () => deleteSession(s));
      row.append(card, del);
      sessionsEl.appendChild(row);
    }
  }

  async function deleteSession(s) {
    const who = (s.participants || []).join(", ") || "—";
    const ok = window.confirm(
      `Delete the session from ${fmtStarted(s.startedAt)} (${who}, ` +
      `${fmtDuration(s.durationS)})? This permanently removes its recorded data.`
    );
    if (!ok) return;
    try {
      await api.delete(s.id);
    } catch (err) {
      alert("Could not delete session: " + err.message);
    }
    // Ids can be positional within a day, so always re-fetch after a
    // delete — and return to the list in case we came from the detail view.
    showList();
    loadList();
  }

  async function loadDetail(id) {
    if (location.hash !== "#" + id) location.hash = id;
    detailEl.innerHTML = "Loading…";
    listView.hidden = true;
    detailView.hidden = false;
    try {
      renderDetail(await api.get(id));
    } catch (err) {
      detailEl.textContent = "Could not load session: " + err.message;
    }
  }

  function renderDetail(session) {
    detailEl.innerHTML = "";
    const head = el("div", "detail-head");
    head.append(el("h2", null, fmtStarted(session.startedAt)));
    head.append(el("span", "dur-big", "duration " + fmtDuration(session.durationS)));
    const del = el("button", "btn-delete", "✕ Delete session");
    del.addEventListener("click", () =>
      deleteSession({
        id: session.id,
        startedAt: session.startedAt,
        durationS: session.durationS,
        participants: (session.participants || []).map((p) => p.name),
      })
    );
    head.append(del);
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
      const zt = zoneTimeBar(p.points || [], p.zones);
      if (zt) card.append(zt);
      detailEl.appendChild(card);
    }
  }

  // Bucket for a reading: 0 = rest, 1..5 = Z1..Z5, 6 = over max (mirrors the
  // overlay's zoneFor and the server's zone_index).
  function zoneIndex(bpm, d) {
    if (bpm > d[d.length - 1]) return d.length;
    for (let i = 0; i < d.length; i++) {
      if (bpm < d[i]) return i;
    }
    return d.length - 1; // bpm == HRmax -> Z5
  }

  // Stacked bar of time per zone. Only zone colors are drawn (no background
  // track): a wrapper sized to tracked-time/scale holds the segments, which
  // split it proportionally. Returns null when there's no tracked time.
  function zoneBarEl(timesS, scaleS) {
    const total = timesS.reduce((a, b) => a + b, 0);
    if (!total) return null;
    const bar = el("span", "zonebar");
    const segs = el("span", "segs");
    segs.style.width = `${((total / scaleS) * 100).toFixed(2)}%`;
    bar.appendChild(segs);
    timesS.forEach((t, i) => {
      if (!t) return;
      const seg = el("span", `seg ${ZONE_NAMES[i]}`);
      seg.style.flexGrow = t;
      segs.appendChild(seg);
    });
    return bar;
  }

  // Detail-page bar: computed from the session's points, plus percentage
  // labels. Track width = max(1 hour, session duration), so sessions are
  // comparable by length.
  function zoneTimeBar(points, zones) {
    if (!zones || !Array.isArray(zones.divisors) || points.length < 2) return null;
    const times = new Array(ZONE_NAMES.length).fill(0);
    let total = 0;
    for (let i = 1; i < points.length; i++) {
      const dt = points[i][0] - points[i - 1][0];
      if (dt <= 0 || dt > ZONE_MAX_GAP_S) continue;
      times[zoneIndex(points[i][1], zones.divisors)] += dt;
      total += dt;
    }
    if (!total) return null;

    const scaleS = Math.max(ZONEBAR_MIN_SCALE_S, points[points.length - 1][0] - points[0][0]);
    const wrap = el("div", "p-zonetime");
    wrap.append(zoneBarEl(times, scaleS));
    const labels = el("div", "zonetime-labels");
    for (let i = 0; i < times.length; i++) {
      if (!times[i]) continue;
      const pct = Math.round((times[i] / total) * 100);
      labels.appendChild(el("span", `zl ${ZONE_NAMES[i]}`, `${ZONE_TIME_LABELS[i]} ${pct}%`));
    }
    wrap.append(labels);
    return wrap;
  }

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

    // Axis labels pinned to their gridlines: with zones, every divisor as its
    // BPM cut-off; otherwise the data min/max in BPM.
    const pct = (v) => ((v / SPARK_H) * 100).toFixed(1) + "%";
    const labels = banded
      ? zones.divisors
          .map((d) => `<span class="y-tick" style="top:${pct(y(d))}">${d}</span>`)
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

  function showList() {
    location.hash = "";
    detailView.hidden = true;
    listView.hidden = false;
  }

  backEl.addEventListener("click", showList);

  // Deep-link: history#<session-id> opens that session directly.
  const initial = decodeURIComponent(location.hash.replace(/^#/, ""));
  loadList().then(() => {
    if (initial) loadDetail(initial);
  });
}
