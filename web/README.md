# bio-overlay — web version (experimental)

An experimental browser-only version of bio-overlay, built alongside (not
replacing) the desktop app and its OBS overlay. Roadmap:

- **Phase 0 (done, validated with hardware): Web Bluetooth test page.**
  Verified the chooser flow, RR-interval delivery, dual straps, and
  reconnect behavior on Mac Chrome. (The diagnostics page itself was
  removed after validation — it lives in git history, PR #13.)
- **Phase 1 (this directory): camera + overlay compositing page.** Webcam
  feed with the live HR overlay drawn on top (`index.html`), for
  screen-sharing a Chrome tab in Zoom — replaces the OBS setup for 1-on-1
  remote training. Uses the same renderer as the OBS overlay
  (`overlay/render.js`) fed by a browser-side hub (`hub.js`) that mirrors
  the server's accounting, plus a history page (`history.html`) — the same
  UI as the desktop app's — backed by the readings in IndexedDB.
  Static hosting (GitHub Pages) next.
- **Phase 2: hosted app (Railway).** Google sign-in, trainer/client accounts,
  live relay to a trainer dashboard, server-side session history.

## Running the pages

Web Bluetooth needs a secure context; `localhost` qualifies. Serve from the
**repo root** (the pages import shared modules from ../overlay/):

    python3 -m http.server 8090

- **Live page**: http://localhost:8090/web/ — webcam + the HR overlay in
  one tab. Click **Start camera** and **Connect strap…** (repeat for a
  second strap; an inline editor asks for a name and birth year per strap,
  remembered in localStorage). In Zoom, use *Share Screen → this Chrome
  tab* and tick **"Optimize for video clip"** — that replaces the OBS
  setup for 1-on-1 remote sessions. The control bar auto-hides when idle
  so it stays out of the shared picture. Readings are written through to
  IndexedDB; **Download JSONL** exports today's data in the desktop
  history format. Try it without hardware: `?sim=2`.
- **History page**: http://localhost:8090/web/history.html — the same
  session list and per-session detail as the desktop app (time-in-zone
  bars, banded sparkline, stats, delete), backed by the IndexedDB
  readings. Note history is per browser + origin: sessions recorded on
  localhost stay on localhost.

Requirements: Chrome or Edge (Safari/Firefox have no Web Bluetooth), and a
one-time macOS Bluetooth permission grant to Chrome on first use.

## Tests

The HR measurement parser (`hr-parser.js`) is a direct port of
`src/bio_overlay/hr_parser.py`; its tests mirror `tests/test_hr_parser.py`
to keep the two pinned to the same byte-level behavior:

    node --test web/test/hr-parser.test.mjs
