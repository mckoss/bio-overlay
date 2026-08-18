# bio-overlay — web version (experimental)

An experimental browser-only version of bio-overlay, built alongside (not
replacing) the desktop app and its OBS overlay. Roadmap:

- **Phase 0 (this directory, now): Web Bluetooth spike.** A static page that
  connects to heart-rate straps directly from Chrome — no app install, no
  server. Verifies the chooser flow, RR-interval delivery, dual straps, and
  reconnect behavior on Mac Chrome.
- **Phase 1: camera + overlay compositing page.** Webcam feed with the live HR
  overlay drawn on top, for screen-sharing a Chrome tab in Zoom — replaces the
  OBS setup for 1-on-1 remote training. Static hosting (GitHub Pages).
- **Phase 2: hosted app (Railway).** Google sign-in, trainer/client accounts,
  live relay to a trainer dashboard, server-side session history.

## Running the spike

Web Bluetooth needs a secure context; `localhost` qualifies:

    python3 -m http.server 8090 --directory web

Open http://localhost:8090/ in Chrome, click **Connect strap…**, and pick a
strap from the chooser (click again to add a second strap). The page shows
live BPM, RR intervals, sensor contact, battery, and notification rate, and
logs connect/reconnect events. **Download JSONL** exports the captured
readings in (roughly) the desktop app's history-line format.

Requirements: Chrome or Edge (Safari/Firefox have no Web Bluetooth), and a
one-time macOS Bluetooth permission grant to Chrome on first use.

## Tests

The HR measurement parser (`hr-parser.js`) is a direct port of
`src/bio_overlay/hr_parser.py`; its tests mirror `tests/test_hr_parser.py`
to keep the two pinned to the same byte-level behavior:

    node --test web/test/hr-parser.test.mjs
