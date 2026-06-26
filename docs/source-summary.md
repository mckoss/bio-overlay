# Source Summary

Source: Gemini shared conversation at
<https://share.gemini.google/pGZJIOkqnF7q>

Fetched: 2026-06-25

## Summary

The shared Gemini conversation explored hardware and architecture for displaying
heart-rate and possible respiration data during a Zoom-based remote training
session.

Key points captured from the conversation:

- The recommended sensor is the Polar H10 Heart Rate Sensor.
- The Polar H10 broadcasts the standard Bluetooth LE Heart Rate Service.
- The relevant standard GATT characteristic for live BPM is Heart Rate
  Measurement, `0x2A37`.
- Python `bleak` was suggested as the first local BLE library to try on macOS.
- Garmin HRM Pro Plus was mentioned as an alternative for basic BLE heart-rate
  data, but with more advanced features tied to Garmin's ecosystem.
- The original distributed architecture was adjusted after clarifying that both
  monitored participants are in one physical location.
- The intended local architecture is:

```text
Participant 1: [Polar H10] -- BLE --+
                                    |
                                    v
                              Local Mac/PC
                                    |
Participant 2: [Polar H10] -- BLE --+
                                    |
                                    v
                            Local telemetry server
                                    |
                                    v
                         OBS Browser Source overlay
                                    |
                                    v
                            OBS Virtual Camera
                                    |
                                    v
                            Zoom feed to trainer
```

- The Gemini answer suggested parsing BPM and RR intervals from standard
  heart-rate packets.
- It also suggested estimating respiration rate from RR intervals or ECG-derived
  respiration. This should be treated as experimental until validated.
- OBS is the right place to composite the live overlay into video.
- Zoom should consume OBS Virtual Camera rather than requiring a custom Zoom
  integration.

## Caution

The Gemini content is useful planning input, not authoritative implementation
evidence. Before relying on advanced metrics, the project should validate actual
Polar H10 packet contents, macOS BLE behavior, and dual-strap reliability.

