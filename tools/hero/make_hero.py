#!/usr/bin/env python3
"""Regenerate the hero screenshot (README + about page) from raw inputs.

Composites the real overlay over tools/hero/gym-background.jpg by replaying a
recorded session, truncated at a fraction of its duration and time-shifted so
that moment reads as live (real sparklines, session stats, part-filled
time-in-zone bars), then screenshotting the actual overlay page — with the
gym photo injected as the page background via the overlay's ?bg debug
parameter — in headless Chrome.

Rerun this whenever the background image or the overlay design changes:

    python3 tools/hero/make_hero.py

All inputs live in this directory so the image is reproducible from the repo
alone: gym-background.jpg (the webcam backdrop), session.jsonl (a real
recorded workout session), and hero-config.json (the matching participants
and birth years, for zones — named to dodge the .gitignore rule that keeps
personal config.json files out of the repo). Override with --source/--config/--fraction
(fraction = where in the session "now" is; default 0.5 = halfway).

Outputs: docs/overlay-screenshot.jpg and web/img/overlay-screenshot.jpg.
Requires: macOS with Google Chrome, and the repo venv at .venv.

Internal: with --serve, runs (under the venv python) the overlay server
seeded from the replay file, then marks each participant live via the
normal ingest path — seeding alone leaves cards in the "no signal" state,
which is correct app behavior but not what a hero shot wants.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OVERLAY_PORT = 8123
IMAGE_PORT = 8124


def serve(config_path: Path, history_dir: Path, port: int) -> None:
    """Run the overlay server on the replayed session (venv python only)."""
    import asyncio

    from bio_overlay.config import AppConfig
    from bio_overlay.history import read_records, trailing_records
    from bio_overlay.server import run_server
    from bio_overlay.telemetry import TelemetryHub

    async def main() -> None:
        config = AppConfig.load(config_path)
        # Huge stale threshold: hold the live look for the whole shoot.
        hub = TelemetryHub(stale_after_s=3600)
        for p in config.participants:
            hub.register_participant(
                p.id, p.display_name, device_id=p.device_id,
                birth_year=p.birth_year, max_hr=p.max_hr,
            )
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        records = trailing_records(read_records(history_dir, today), 1800)
        if not records:
            sys.exit("no replay records for today")
        hub.seed_history(records)
        await run_server(hub, "127.0.0.1", port, config=config,
                         history_dir=str(history_dir))
        # Newest restored reading per participant -> the normal live path.
        newest = {r["p"]: r for r in records}
        for pid, r in newest.items():
            await hub.update_measurement(
                pid, bpm=r["bpm"], rr_intervals_ms=r.get("rr") or [])
        await asyncio.Event().wait()

    asyncio.run(main())


def build_replay_file(source: Path, fraction: float, out_dir: Path) -> None:
    """Write today's history file: the source session truncated at
    `fraction` of its duration, time-shifted so its end is ~now."""
    lines = source.read_text(encoding="utf-8").splitlines()
    header = None
    data = []
    for line in lines:
        if not line.strip():
            continue
        rec = json.loads(line)
        if "session" in rec:
            if header is None:
                header = rec
            else:
                break  # replay only the first session in the file
        elif header is not None:
            data.append(rec)
    if header is None or not data:
        sys.exit(f"no session found in {source}")

    last_s = max(r["s"] for r in data)
    keep = [r for r in data if r["s"] <= last_s * fraction]
    new_last = max(r["s"] for r in keep)
    start = datetime.now().astimezone() - timedelta(seconds=new_last)
    header = dict(header, session=start.isoformat(timespec="milliseconds"))
    header.pop("reset", None)

    out = out_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for r in keep:
            f.write(json.dumps(r) + "\n")
    mins = new_last // 60
    print(f"replay: {len(keep)} readings, ends at {mins:.0f} of {last_s // 60:.0f} min")


def wait_for(url: str, timeout_s: float = 15) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError:
            time.sleep(0.3)
    sys.exit(f"timed out waiting for {url}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=HERE / "session.jsonl")
    ap.add_argument("--config", type=Path, default=HERE / "hero-config.json")
    ap.add_argument("--fraction", type=float, default=0.5)
    ap.add_argument("--venv", type=Path, default=REPO / ".venv",
                    help="venv with bio_overlay deps (e.g. the main checkout's "
                         "when running from a worktree)")
    ap.add_argument("--serve", nargs=3, metavar=("CONFIG", "HISTORY_DIR", "PORT"),
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.serve:
        serve(Path(args.serve[0]), Path(args.serve[1]), int(args.serve[2]))
        return

    tmp = Path(tempfile.mkdtemp(prefix="bio-overlay-hero-"))
    build_replay_file(args.source, args.fraction, tmp)

    server = subprocess.Popen(
        [str(args.venv / "bin" / "python"), str(HERE / "make_hero.py"),
         "--serve", str(args.config), str(tmp), str(OVERLAY_PORT)],
        cwd=REPO, env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    images = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(IMAGE_PORT),
         "--directory", str(Path(__file__).parent)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for(f"http://127.0.0.1:{OVERLAY_PORT}/")
        wait_for(f"http://127.0.0.1:{IMAGE_PORT}/gym-background.jpg")

        # The overlay's ?bg debug parameter takes any CSS background
        # shorthand — including the gym photo as a cover image.
        bg = f"url(http://127.0.0.1:{IMAGE_PORT}/gym-background.jpg) center/cover"
        url = (f"http://127.0.0.1:{OVERLAY_PORT}/?bg=" +
               urllib.parse.quote(bg, safe=""))
        shot = tmp / "hero.png"
        subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--window-size=1920,1080",
             "--timeout=6000", f"--screenshot={shot}", url],
            check=True, capture_output=True,
        )

        for dest in (REPO / "docs" / "overlay-screenshot.jpg",
                     REPO / "web" / "img" / "overlay-screenshot.jpg"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
                 str(shot), "--out", str(dest)],
                check=True, capture_output=True,
            )
            print(f"wrote {dest.relative_to(REPO)}")
    finally:
        server.terminate()
        images.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
