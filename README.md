# bio-overlay

Real-time heart-rate video overlay for remote training sessions.

The goal is to read live heart-rate data from up to two Polar BLE chest straps in
one location, render those metrics as a transparent browser overlay in OBS, and
send the composited video to a remote trainer through Zoom.

## Current Status

Planning repo. The first milestone is a local proof of concept:

1. Discover and connect to one Polar H10 from a Mac.
2. Parse the standard Bluetooth LE Heart Rate Measurement characteristic.
3. Publish live BPM updates to a local WebSocket server.
4. Render a transparent OBS Browser Source overlay.
5. Extend the pipeline to two straps.

Respiration-rate display is an optional later feature. The design treats it as
experimental until validated against real RR-interval or ECG data from the straps.

## Documents

- [Design](docs/design.md)
- [Source summary](docs/source-summary.md)

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

