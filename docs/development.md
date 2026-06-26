# Development & Handoff Notes

Scaffolding for milestones 1–3 is in place. The whole telemetry → overlay path
runs today **without hardware** via a simulator; the BLE collector is wired and
ready for the H10 straps.

## Layout

```
src/bio_overlay/
  hr_parser.py     Pure parser for the 0x2A37 Heart Rate Measurement char.
  telemetry.py     ParticipantState model + TelemetryHub (pub/sub + staleness watchdog).
  ble_collector.py bleak connection per strap, retry/reconnect loop (needs hardware).
  simulator.py     Hardware-free telemetry source (fake BPM + dropouts).
  server.py        aiohttp: serves overlay/ + /ws WebSocket broadcast.
  config.py        config.json loading (participant <-> strap binding).
  cli.py           `scan` / `run` / `simulate` subcommands.
overlay/           Transparent OBS Browser Source page (index.html, css, js).
tests/             Parser + telemetry unit tests (no hardware).
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Run without hardware (works now)

```bash
bio-overlay simulate          # serves http://127.0.0.1:8080/
# or: python -m bio_overlay.cli simulate
```

Open `http://127.0.0.1:8080/` in a browser (or add it as an OBS Browser Source).
You should see two panels with live, drifting BPM. Participant 2 periodically
goes to a "no signal" stale state to demonstrate the disconnected UI.

## Run with hardware (when the H10 arrives)

```bash
cp config.example.json config.json   # config.json is git-ignored
bio-overlay scan                     # discover straps -> copy each macOS UUID into config.json
bio-overlay run -c config.json       # collect from real straps + serve overlay
```

`scan` matches by name prefix `Polar` by default; once you copy each strap's
`address` (a CoreBluetooth UUID on macOS) into `config.json`, `run` binds each
participant to a specific strap.

### ⚠️ macOS Bluetooth permission

The **first** BLE access triggers a macOS privacy (TCC) prompt for the host
program (Terminal / iTerm). While AFK this prompt blocks `scan` indefinitely —
that's the cause of any apparent hang, not a code bug. When you're at the
machine:

1. Run `bio-overlay scan` once and **Allow** the Bluetooth prompt.
2. If no prompt appears, grant the terminal app access under
   *System Settings → Privacy & Security → Bluetooth*, then re-run.

## Tests

```bash
pytest                # 12 tests, no hardware required
```

## Verified so far

- `pytest` — parser + telemetry hub (12 passing).
- `simulate` — server up, `/healthz`, `/`, `/overlay.css`, `/overlay.js` all 200;
  `/ws` streams live `{"type":"state", ...}` snapshots for both participants.

## Not yet verified (needs the H10)

- Real `scan` discovery output and the exact advertised name.
- Live `0x2A37` packet shapes (esp. whether RR intervals appear in normal
  notifications — log real packets here).
- Two simultaneous H10 connections from one Mac: range, reliability, reconnect.
- Overlay inside OBS Browser Source + Virtual Camera → Zoom readability.

## Design decisions / assumptions made while AFK

- **OBS path confirmed.** Zoom has no public API to inject an overlay onto your
  outgoing webcam feed, so OBS Browser Source → OBS Virtual Camera → Zoom is the
  right approach. No Zoom SDK needed.
- **Collector + server are one process** (per design milestone 1–2).
- **aiohttp** serves both the static overlay and the WebSocket on one port, so
  there's a single URL to point OBS at.
- **bleak is imported lazily** so the parser/server/simulator/tests all run on
  machines without it (and without triggering the BLE permission prompt).
- Full state for *all* participants is broadcast on every update, giving the
  overlay a stable two-panel layout even when one strap is missing.
- RR intervals are parsed and carried in telemetry but **not displayed**
  (respiration stays experimental per the design).

## Suggested next steps

1. Hardware bring-up: `scan`, then `run`, log real packet shapes.
2. Confirm overlay legibility in OBS at 1920x1080 and 1280x720.
3. Milestone 4: reconnect polish + operator config UI.
4. Milestone 5: RR-interval-based respiration prototype (separate from BPM path).
```
