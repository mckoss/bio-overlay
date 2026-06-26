# bio-overlay

Real-time heart-rate video overlay for remote training sessions.

The goal is to read live heart-rate data from up to two Polar BLE chest straps in
one location, render those metrics as a transparent browser overlay in OBS, and
send the composited video to a remote trainer through Zoom.

## Current Status

Scaffolding for the local proof of concept is in place and the full
telemetry → overlay path runs **without hardware** via a built-in simulator. The
BLE collector is wired and waiting for the Polar H10 straps.

1. ✅ Parse the standard Bluetooth LE Heart Rate Measurement characteristic (`hr_parser`, unit-tested).
2. ✅ Publish live BPM updates to a local WebSocket server (`server` + `telemetry`).
3. ✅ Render a transparent OBS Browser Source overlay (`overlay/`).
4. ✅ Simulator so the server + overlay can be tested with no straps.
5. ⏳ Discover/connect to real Polar H10 straps (`ble_collector`, needs hardware).

See [docs/development.md](docs/development.md) for the handoff notes, including
the macOS Bluetooth permission step.

Respiration-rate display is an optional later feature. The design treats it as
experimental until validated against real RR-interval or ECG data from the straps.

## Documents

- [Design](docs/design.md)
- [Source summary](docs/source-summary.md)
- [Mac setup notes](docs/mac-setup.md)

## Getting Started on a Mac

```bash
git clone https://github.com/mckoss/bio-overlay.git
cd bio-overlay
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Try it with no hardware:

```bash
bio-overlay simulate          # then open http://127.0.0.1:8080/
```

With hardware (the first BLE access triggers a macOS Bluetooth permission prompt
— Allow it):

```bash
cp config.example.json config.json
bio-overlay scan              # copy each strap's macOS UUID into config.json
bio-overlay run -c config.json
```

## Intended Runtime Shape

```text
Polar H10 #1 -- BLE --+
                      |
                      v
                Local collector
                      |
Polar H10 #2 -- BLE --+-- WebSocket --> Overlay webpage --> OBS Browser Source
                                                            |
                                                            v
                                                     OBS Virtual Camera
                                                            |
                                                            v
                                                          Zoom
```

## Non-Goals

- Replacing OBS or Zoom.
- Cloud telemetry for the first version.
- Medical-grade biometric analysis.
- Storing biometric history by default.
