# Mac Setup Notes

This project should move from the current Windows/WSL planning environment to a
Mac for the first real hardware spike, because macOS will be the BLE host for
the Polar H10 straps and likely the OBS machine.

## Clone

```bash
git clone https://github.com/mckoss/bio-overlay.git
cd bio-overlay
```

## Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install bleak
```

## Hardware Spike Checklist

1. Pair or wake one Polar H10 strap near the Mac.
2. Use `bleak` discovery to find the strap's macOS UUID.
3. Subscribe to the Heart Rate Measurement characteristic:
   `00002a37-0000-1000-8000-00805f9b34fb`.
4. Print raw packets and parsed BPM.
5. Check whether RR intervals are present in normal notifications.
6. Repeat with two straps connected at once.

## OBS Setup Target

When the overlay webpage exists:

1. Start the local telemetry server.
2. Open OBS Studio.
3. Add a Browser Source pointing to the local overlay URL.
4. Use a transparent background and size the source for 1920x1080.
5. Start OBS Virtual Camera.
6. Select OBS Virtual Camera in Zoom.

## First Mac Milestone

The first useful commit from the Mac should prove one strap end-to-end:

```text
Polar H10 -> bleak notification -> parsed BPM -> terminal output
```

Do not start with the OBS overlay until that packet path is real.
