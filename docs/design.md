# bio-overlay Design

## Problem

A remote trainer should be able to see live heart rates for up to two people who
are exercising in the same physical location. The people being monitored are
local to the computer running the overlay. The trainer watches the output through
a normal Zoom call.

OBS will handle video compositing and Zoom integration. This project should
provide the biometric data capture, local telemetry transport, and transparent
browser overlay that OBS can consume.

## Goals

- Support up to two simultaneous Polar BLE heart-rate monitors.
- Run locally on macOS for the initial version.
- Display each participant's current BPM clearly enough to be readable in a Zoom
  call.
- Use OBS Browser Source for overlay rendering.
- Keep all telemetry local by default.
- Recover gracefully from strap disconnects.
- Avoid storing biometric data unless a future feature explicitly requires it.

## Later Goals

- Show estimated respiration rate if real-world testing proves the signal is
  reliable enough.
- Add a setup UI for naming participants and binding straps.
- Package the collector so it can be run without a developer shell.
- Add recording-friendly status indicators and optional session markers.

## Architecture

```text
Participant 1: [Polar H10] -- BLE --+
                                    |
                                    v
                              Local collector
                                    |
Participant 2: [Polar H10] -- BLE --+
                                    |
                                    v
                            Local telemetry server
                                    |
                                    v
                          Browser overlay webpage
                                    |
                                    v
                         OBS Studio Browser Source
                                    |
                                    v
                           OBS Virtual Camera
                                    |
                                    v
                                  Zoom
```

The first implementation should keep the collector and telemetry server on the
same machine. A later split is possible, but co-located participants make a
single local machine the simplest useful target.

## Components

### BLE Collector

The collector discovers and subscribes to Polar H10 heart-rate notifications.
The standard Bluetooth LE Heart Rate Service is enough for the first version:

- Service UUID: `0x180D`
- Heart Rate Measurement characteristic: `0x2A37`

The characteristic packet contains flags, BPM, and optionally RR intervals. The
collector should parse:

- 8-bit or 16-bit BPM values.
- RR intervals when present, expressed in 1/1024-second units.
- Sensor contact and energy-expended flags only if they become useful for UI.

Preferred initial implementation: Python with `bleak`, because it supports
macOS BLE and has simple async notification handling.

### Telemetry Server

The telemetry server accepts updates from the collector and broadcasts the most
recent state to the overlay page over WebSockets.

For the first milestone, the collector and server may be one process. Splitting
them is only worthwhile when the code needs a cleaner process boundary or a
setup UI.

Example message:

```json
{
  "participantId": "participant-1",
  "displayName": "Participant 1",
  "bpm": 132,
  "rrIntervalsMs": [742.2, 735.4],
  "connected": true,
  "updatedAt": "2026-06-25T23:45:00.000-07:00"
}
```

Overlay clients should also receive stale/disconnected state so OBS does not
freeze misleading values on screen.

### OBS Overlay Webpage

The overlay page connects to the telemetry server and renders a transparent UI.
OBS loads this page as a Browser Source.

Design constraints:

- Transparent background.
- Large, high-contrast BPM numerals.
- Two participant panels max.
- Stable layout when data is missing or reconnecting.
- Clear disconnected/stale state.
- No setup controls visible in the OBS scene.

Initial page size should target 1920x1080, with CSS that also works if OBS uses
1280x720.

### Zoom Output

OBS Virtual Camera sends the composited video to Zoom. Zoom sees only the OBS
Virtual Camera; this project does not need to use the Zoom SDK.

## Respiration Rate

The Gemini source suggested deriving respiration from Polar H10 RR intervals or
ECG data using ECG-derived respiration / respiratory sinus arrhythmia. This is
plausible, but it is more research project than core requirement.

Treat respiration as experimental:

- Do not show respiration in the first user-facing overlay unless validated.
- Capture RR intervals in the internal telemetry model when available.
- Build a prototype analysis path separately from the core BPM overlay.
- Label any displayed respiration metric as estimated.

## Risks and Unknowns

- macOS exposes BLE devices by UUID rather than stable MAC address, so binding
  straps may need a discovery and selection step.
- Two simultaneous Polar H10 connections from one Mac must be tested for range,
  reliability, and reconnection behavior.
- Polar H10 may not always include RR intervals in the standard heart-rate
  notification stream.
- ECG access may require Polar-specific characteristics, not just the standard
  GATT heart-rate characteristic.
- OBS Browser Source WebSocket behavior should be tested after scene reloads and
  computer sleep.
- Zoom compression may make small overlay text unreadable.

## Milestones

### 1. Single-Strap Spike

- Connect to one Polar H10.
- Print parsed BPM updates.
- Log raw packet shapes for real device samples.

### 2. Local Telemetry Loop

- Add local WebSocket broadcast.
- Add a minimal overlay page showing one live BPM value.
- Verify the overlay in OBS Browser Source.

### 3. Two-Strap Support

- Bind two straps to participant names.
- Maintain independent connection state.
- Show two participant panels in OBS.

### 4. Operator Polish

- Add setup/config file.
- Add reconnect behavior and stale-data UI.
- Add launch instructions.

### 5. Respiration Research

- Evaluate RR interval availability and quality.
- Prototype a rolling-window breath-rate estimator.
- Decide whether the metric is useful enough for live display.

## Open Questions

- Which Mac will run OBS and collect BLE data?
- Will both participants always use Polar H10 straps?
- Should the overlay appear over webcam video, a shared workout view, or both?
- Should session data ever be recorded, exported, or deliberately discarded?
- What names/labels should appear in the trainer-facing overlay?

