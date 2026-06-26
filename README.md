# bio-overlay

Real-time heart-rate video overlay for remote training sessions.

bio-overlay reads live heart-rate data from up to two Polar BLE chest straps in
one location, renders those metrics as a transparent browser overlay, and lets
OBS composite that overlay onto your video and send it to a remote trainer
through Zoom (or any app that can select a camera).

## How it works

```text
Polar H10 #1 -- BLE --+
                      |
                      v
                Local collector  ──>  history/YYYY-MM-DD.json (optional)
                      |
Polar H10 #2 -- BLE --+-- WebSocket --> Overlay webpage --> OBS Browser Source
                                                            |
                                                            v
                                                     OBS Virtual Camera
                                                            |
                                                            v
                                                          Zoom
```

A single local process (`bio-overlay run`) connects to the straps, serves a
transparent overlay page, and broadcasts live telemetry to it over a WebSocket.
OBS loads that page as a Browser Source, composites it over your camera/screen,
and exposes the result as a Virtual Camera that Zoom selects.

Each overlay card shows the live BPM, a sparkline of the last 5 minutes (with
that window's min/max), and whole-session min/avg/max.

## Status

Working end-to-end and verified against a real Polar H10:

1. ✅ Parse the standard BLE Heart Rate Measurement characteristic (`0x2A37`).
2. ✅ Connect to a Polar H10 and stream live BPM + RR intervals.
3. ✅ Local WebSocket telemetry server + transparent overlay with sparkline/stats.
4. ✅ Server-side session history (survives overlay/OBS reloads) + daily history file.
5. ✅ Simulator for hardware-free development.
6. ⏳ Two straps at once (single-strap proven; dual-strap reliability still to test).

An experimental estimated breathing rate (from RR-interval variation) is also
shown on each card, labeled EST — see [Respiration](#respiration-experimental).

## Download

Prebuilt single-file executables for **macOS (Apple Silicon)** and **Windows
(x64)** are attached to each [GitHub Release](https://github.com/mckoss/bio-overlay/releases).
Download the one for your OS and run it. With no arguments the executable starts
collecting and **automatically opens the setup page** in your browser, where you
can add participants, pair straps, and copy the URL to paste into OBS (it also
links straight to the overlay). Pass `--no-browser` to skip the auto-open.

First-run notes:
- **macOS:** the binary is unsigned, so Gatekeeper may block it — right-click →
  **Open** the first time (or `xattr -d com.apple.quarantine ./bio-overlay`).
  Run it from **Terminal** so the Bluetooth permission prompt can be granted.
- **Windows:** SmartScreen may warn on an unsigned exe — **More info → Run anyway**.

When run as a packaged executable, config and history live in
`~/Documents/Bio-Overlay/` (see [Where files are stored](#where-files-are-stored)).

## Install from source (macOS)

```bash
git clone https://github.com/mckoss/bio-overlay.git
cd bio-overlay
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Quick start

No hardware (simulated data) — good for building the overlay and OBS scene:

```bash
bio-overlay simulate            # then open http://127.0.0.1:8080/
```

With real straps:

```bash
cp config.example.json config.json
bio-overlay scan                # discover straps; copy each deviceId into config.json
bio-overlay run -c config.json  # then open http://127.0.0.1:8080/
```

> **macOS Bluetooth permission:** the first BLE access pops a system permission
> prompt for your terminal app — click **Allow**. If you miss it, grant access
> under *System Settings → Privacy & Security → Bluetooth* and re-run. Until
> this is granted, `scan`/`run` will appear to hang.

## Configuration

### Setup page (no JSON editing)

The easiest way to configure participants and pair straps is the built-in setup
page at `/config`, which opens automatically when you start the app (`bio-overlay`
or `bio-overlay run`). It's also served at `http://127.0.0.1:8080/config`.

From there you can add/remove participants, edit each one's name/id/deviceId,
**Scan** for nearby straps to assign (pair) them to a participant, and copy the
overlay URL for OBS — then **Save**.

### `config.json` (manual)

Copy `config.example.json` to `config.json` (which is git-ignored, since it can
contain personal device IDs) and edit it. Without `-c`, sensible defaults are
used (two unbound participants, `127.0.0.1:8080`).

```json
{
  "host": "127.0.0.1",
  "port": 8080,
  "staleAfterSeconds": 5.0,
  "participants": [
    {
      "id": "mike-koss",
      "displayName": "Mike",
      "deviceId": "16CD9E3C",
      "namePrefix": "Polar H10"
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `host` / `port` | Address the overlay server binds to. `port` is also the OBS Browser Source URL port. |
| `staleAfterSeconds` | If no fresh reading arrives within this many seconds, the card shows a "no signal" stale state. |
| `participants[]` | One entry per person (max two). |
| `participants[].id` | Stable internal key. Used as the panel key and the log/history key — keep it short and file-safe (e.g. `mike-koss`). |
| `participants[].displayName` | Pretty name shown on the overlay card. Can be changed anytime without affecting keys. |
| `participants[].deviceId` | **Preferred binding.** The Polar ID printed on the strap (e.g. `16CD9E3C`), also shown by `scan`. Identifies the physical sensor and is portable across Macs. |
| `participants[].address` | Optional fallback: the macOS CoreBluetooth UUID. Mac-specific, not printed on the strap; only used if `deviceId` is unset. |
| `participants[].namePrefix` | Advertised-name prefix to match (default `Polar H10`). |

Strap resolution order: `deviceId` → `address` → first strap whose advertised
name starts with `namePrefix`.

### Binding a strap to a participant

1. Put on / activate the strap (a Polar H10 only advertises when it detects skin
   contact — wear it, or bridge the electrode pads with damp fingers).
2. Run `bio-overlay scan`. It prints, for each strap, the `deviceId` (matching
   the number on the physical strap) plus a paste-ready snippet.
3. Put that `deviceId` into the matching participant in `config.json`. Tip:
   physically label the straps (P1/P2) so the mapping is unambiguous mid-session.

## Command-line reference

Global: `-v` / `--verbose` enables debug logging; `--version` prints the version.

### `bio-overlay scan`
Discover nearby BLE straps and print their `deviceId` and macOS address.

| Option | Default | Meaning |
| --- | --- | --- |
| `--timeout N` | `10` | Seconds to scan. |
| `--name-prefix STR` | `Polar` | Only show devices whose name starts with this. |
| `--all` | off | Show all BLE devices (ignore the name filter). |

### `bio-overlay run`
Collect from real straps and serve the overlay.

| Option | Default | Meaning |
| --- | --- | --- |
| `-c, --config PATH` | built-in defaults | Path to `config.json`. |
| `--host HOST` | `127.0.0.1` | Override the bind host. |
| `--port PORT` | `8080` | Use this exact port (strict — errors if busy). |
| `--port-scan` | on unless `--port` given | If the port is busy, pick the next free one. |
| `--history-dir DIR` | data dir `/history` | Directory for daily history files. |
| `--no-history` | off | Don't write the daily history file. |

By default (no explicit `--port`) bio-overlay auto-picks a free port starting at
8080, so double-clicking the app "just works" even if another copy is running.
Passing `--port N` is strict: if that exact port is busy it prints a short
message (not a stack trace). Add `--port-scan` to make an explicit port fall
back to scanning.

### `bio-overlay simulate`
Serve the overlay with synthetic data (no hardware, no history file written).
Accepts `-c/--config`, `--host`, `--port`, `--port-scan`, `--no-browser`.

Running with **no arguments** (e.g. double-clicking the executable, or a bare
`bio-overlay`) is the same as `run`. Both `run` and `simulate` open the setup
page in the browser on start; pass `--no-browser` to suppress it.

## Where files are stored

`config.json` and the `history/` directory live in different places depending on
how you run bio-overlay:

| How you run it | Default config | Default history |
| --- | --- | --- |
| From a source checkout | `./config.json` | `./history/` |
| From a packaged executable | `~/Documents/Bio-Overlay/config.json` | `~/Documents/Bio-Overlay/history/` |

`-c/--config` and `--history-dir` always override these defaults.

## The overlay page

Open `http://<host>:<port>/` (default `http://127.0.0.1:8080/`).

- The background is **truly transparent** (real alpha) for OBS. In a normal
  browser it looks like dark cards on white — that's just the browser's page
  background, not the overlay.
- A small dot in the bottom-right shows WebSocket status (green = connected).
- Debug aid: append `?bg=green` (or any CSS color) to paint the transparent
  background so you can see exactly which area is overlay vs see-through, e.g.
  `http://127.0.0.1:8080/?bg=magenta`. Leave it off for OBS.

## OBS setup

1. **Add the overlay.** Sources → **+** → **Browser**.
   - URL: `http://127.0.0.1:8080/`
   - Width/Height: `1920` × `1080` (the CSS also works at 1280×720).
   - **Uncheck** "Shutdown source when not visible" so the WebSocket stays alive
     across scene switches.
   - **No Chroma Key needed** — a Browser Source composites real transparency.
     Adding a green/chroma key would only cause fringing.
2. **Add your video below it** — a Video Capture Device (webcam) and/or a Display
   /Window Capture. Order the overlay above your video in the source list and
   position/scale it where you want the cards.
3. **Start the Virtual Camera.** Controls → **Start Virtual Camera**.

To refresh the overlay after editing it, right-click the Browser Source →
**Refresh** (or its properties → "Refresh cache of current page"). No need to
restart `bio-overlay` for overlay edits.

## Zoom setup

Zoom just consumes the OBS Virtual Camera — no Zoom SDK or plugin is involved.

1. Start `bio-overlay run …` and OBS (with the Virtual Camera started).
2. In Zoom: **Settings → Video → Camera → OBS Virtual Camera** (or use the
   in-meeting camera `^` menu next to "Stop Video").
3. The trainer sees your video with the live heart-rate cards composited in.

Tip: Zoom compresses video, so keep the cards reasonably large and
high-contrast for readability. Any app with a camera picker (Meet, FaceTime,
etc.) works the same way.

## Session history

- **In-session (for the overlay):** the server retains each participant's
  rolling 5-minute sparkline window and whole-session min/avg/max, and sends
  them on every update. A page reload, OBS scene reload, or reconnect restores
  the sparkline and stats immediately. History also accrues while no overlay is
  connected.
- **On disk (daily file):** `run` appends every real reading to
  `history/YYYY-MM-DD.json` (git-ignored) — a JSON array of
  `{t, participantId, deviceId, bpm, rrIntervalsMs}`. It flushes atomically on a
  timer and on shutdown, appends to an existing same-day file, and rolls over at
  midnight. Disable with `--no-history` or relocate with `--history-dir`.
  `simulate` never writes history.
- **Survives server restarts:** on startup `run` reloads today's history file
  and rebuilds the session stats, sparkline window, and respiration estimate, so
  restarting the server mid-session keeps the displayed history (note: all of a
  day's readings count as one session, so separate sessions on the same day
  merge unless you use a fresh `--history-dir`).

### Respiration (experimental)

Each card can also show an estimated breathing rate (`resp N br/min · EST`),
derived from RR-interval variation (respiratory sinus arrhythmia). It is shown
only when the signal is confident enough, and is **experimental** — RSA fades
during hard exercise, so treat the number as approximate. See
[docs/design.md](docs/design.md).

## Development

- **Tests:** `pytest` (no hardware required).
- **Overlay edits** (`overlay/*`): served fresh from disk — just hard-refresh the
  browser (Cmd+Shift+R) or OBS Browser Source. No server restart.
- **Python edits** (`src/bio_overlay/*`): Ctrl+C (now instant) and re-run.

See [docs/development.md](docs/development.md) for deeper handoff notes and
real-hardware findings.

## Building & releasing

Build a single-file executable locally (output in `dist/`):

```bash
python -m pip install -e ".[package]"
pyinstaller packaging/bio-overlay.spec --noconfirm
./dist/bio-overlay --version
```

PyInstaller cannot cross-compile, so each OS is built on its own runner. The
[`Release` workflow](.github/workflows/release.yml) builds **macOS** and
**Windows** executables and publishes them to a GitHub Release. To cut a
release, tag a [SemVer](https://semver.org) version and push it:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The tagged builds are attached to the release, and the Releases page is the
version history. (You can also run the workflow manually via
*Actions → Release → Run workflow* to get build artifacts without publishing.)
Keep the tag in sync with the `version` in `pyproject.toml` / `__init__.py`.

## Documents

- [Design notes](docs/design.md)
- [Development & handoff notes](docs/development.md)
- [Source summary](docs/source-summary.md)

## Non-Goals

- Replacing OBS or Zoom.
- Cloud telemetry (all data stays local; the daily history file is local and
  git-ignored, and can be disabled with `--no-history`).
- Medical-grade biometric analysis.
