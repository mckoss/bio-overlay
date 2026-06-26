# bio-overlay — Design Notes

Architecture, setup, configuration, and the CLI are documented in the
[README](../README.md); real-hardware findings and the dev workflow are in
[development.md](development.md). This file keeps only the design rationale,
risks, and open questions not covered there.

## Problem & approach

A remote trainer should see live heart rates for up to two people exercising in
the same physical location, watched through a normal Zoom call. Because both
monitored people are local to one computer, the simplest useful target is a
single local process: one collector for both straps, a local telemetry server,
and a transparent OBS overlay. OBS handles compositing and exposes a Virtual
Camera; Zoom just selects that camera, so no Zoom SDK or cloud telemetry is
needed.

## Respiration rate (experimental)

The original Gemini source suggested deriving respiration from Polar H10 RR
intervals (respiratory sinus arrhythmia) or ECG-derived respiration. This is
more research project than core requirement, so it is deliberately deferred:

- Real-hardware testing confirmed **RR intervals are present in every standard
  H10 notification**, so the input signal for an RR-based estimator is available
  and is already captured in telemetry and the daily history file.
- Do not show respiration in the user-facing overlay until validated; build a
  rolling-window breath-rate prototype separately from the BPM path.
- Label any displayed respiration metric as estimated.
- ECG-derived respiration would likely need Polar-specific GATT characteristics,
  not just the standard `0x2A37` heart-rate measurement.

## Risks & open questions

- **Two simultaneous H10 connections** from one Mac are unproven — range,
  reliability, and reconnection behavior still need testing (single-strap works).
- **OBS Browser Source resilience**: verify the overlay reconnects cleanly after
  scene reloads and computer sleep (the client has reconnect/backoff, but test it
  in OBS specifically).
- **Zoom compression** may make small overlay text unreadable; keep cards large
  and high-contrast.
- **Overlay placement**: should it sit over the webcam, a shared workout view, or
  both? Drives sizing and default position.
- **History retention**: a local daily `history/YYYY-MM-DD.json` is now written.
  Decide whether/when to prune or export it (it is git-ignored and disableable
  with `--no-history`).

## Roadmap (later)

- Two-strap UI polish with independent connection state.
- A setup/config UI for naming participants and binding straps.
- Packaging so the collector runs without a developer shell.
- An RR-interval respiration prototype if signal quality proves out.
