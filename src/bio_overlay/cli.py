"""Command-line entry point.

Subcommands:
    app        Open the desktop app window (the default; supervises `run`).
    scan       Discover nearby BLE straps and print their device IDs.
    run        Start the telemetry server + BLE collector (needs hardware).
    simulate   Start the telemetry server + simulated data (no hardware).

`run` and `simulate` serve the overlay at http://<host>:<port>/ for use as an
OBS Browser Source, and the setup page at /config to edit config and pair straps.

`app` is what double-clicking the packaged application does: it shows the setup
page in a window and runs `run` as a child process that it stops on the way out.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__, instance
from .config import DEFAULT_PORT, AppConfig
from .paths import default_config_path, default_history_dir
from .server import PortInUseError, run_server
from .telemetry import STOPPED, TelemetryHub


def _resolve_config_path(args: argparse.Namespace) -> str:
    # Absolute, so logs and the setup page show unambiguously which file is used.
    return str(Path(args.config).resolve() if args.config else default_config_path())


def _load_config(args: argparse.Namespace) -> AppConfig:
    path = Path(_resolve_config_path(args))
    if path.exists():
        config = AppConfig.load(path)
        logging.info(
            "loaded config from %s (%d participant(s))", path, len(config.participants)
        )
        if config.port_migrated:
            # Saved configs from before 2.0 pin the old shared default. Rewrite
            # it once so the file and the running app agree.
            config.save(path)
            logging.info(
                "migrated the default port in %s to %d "
                "(update your OBS Browser Source URL)",
                path,
                config.port,
            )
    elif args.config:
        # An explicit -c pointing at a missing file is an error, not a silent
        # fall-through to defaults (which looks like an "empty" config).
        sys.exit(f"Error: config file not found: {path}")
    else:
        config = AppConfig.default()
        logging.info(
            "no config file at %s; using built-in defaults (1 unconfigured participant)",
            path,
        )
    if getattr(args, "host", None):
        config.host = args.host
    if getattr(args, "port", None):
        config.port = args.port
    return config


def _should_open_browser(args: argparse.Namespace) -> bool:
    """Auto-open the setup page on start unless told not to (same in repo & exe)."""
    return not getattr(args, "no_browser", False)


def _should_port_scan(args: argparse.Namespace) -> bool:
    """Only scan when asked.

    This used to be the default, so a second launch silently bound the next port
    up instead of colliding — two collectors on the same straps, and an OBS
    Browser Source still pointed at the first one. On a port that is ours alone,
    a collision is information, not an obstacle to route around.
    """
    return bool(getattr(args, "port_scan", False))


def _browser_host(host: str) -> str:
    # A wildcard bind isn't browsable; point the browser at loopback.
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _watch_supervisor(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Shut down when the window process that launched us goes away.

    A parent's exit does not kill its children on macOS or Windows — orphans are
    reparented and keep running, which is how a server from an earlier session
    could still be holding the straps hours later. The window process hands us
    its end of a pipe as stdin and never writes to it, so anything that ends the
    parent (clean quit, crash, Force Quit, kill -9) closes that pipe and we read
    EOF here. Setting `stop` runs the same orderly shutdown a signal would:
    collector stopped, history flushed, port released.
    """
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:  # pragma: no cover - no stdin to inherit
        logging.warning("--supervised: no stdin to watch, so a parent exit won't be seen")
        return

    def wait_for_eof() -> None:
        try:
            while stream.read(1):
                pass  # The supervisor never writes; anything read is discarded.
        except OSError:
            pass
        logging.info("supervisor exited; shutting down")
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=wait_for_eof, name="supervisor-watchdog", daemon=True).start()


def _build_hub(config: AppConfig, *, enable_respiration: bool = False) -> TelemetryHub:
    hub = TelemetryHub(
        stale_after_s=config.stale_after_seconds,
        enable_respiration=enable_respiration,
    )
    for p in config.participants:
        hub.register_participant(
            p.id,
            p.display_name,
            device_id=p.device_id,
            birth_year=p.birth_year,
            max_hr=p.max_hr,
        )
    return hub


async def _serve_with_source(
    config: AppConfig,
    source_factory,
    *,
    history_dir: str | None = None,
    config_path: str | None = None,
    open_browser: bool = False,
    port_scan: bool = False,
    enable_respiration: bool = False,
    supervised: bool = False,
) -> None:
    """Run the server alongside a telemetry source (collector or simulator).

    If source_factory is None, only the server runs (e.g. the `config` setup UI).
    If history_dir is given, real readings are persisted to a daily JSON file.
    """
    hub = _build_hub(config, enable_respiration=enable_respiration)
    hub.start_watchdog()

    writer = None
    if history_dir:
        from datetime import datetime, timezone

        from .history import DailyHistoryWriter, read_records, trailing_records
        from .telemetry import DEFAULT_IDLE_CLOSE_S

        # Restore an in-progress session from today's file so a server restart
        # mid-session keeps the sparkline, session stats, and respiration.
        # Only the still-open session is restored: a 30-minute gap (matching
        # the idle auto-close) is a session boundary that seeding won't cross.
        today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        seeded = trailing_records(read_records(history_dir, today), DEFAULT_IDLE_CLOSE_S)
        if seeded:
            hub.seed_history(seeded)
            logging.info("restored %d readings from %s/%s.jsonl", len(seeded), history_dir, today)

        writer = DailyHistoryWriter(history_dir)
        writer.start()
        writer.start_session(config.participants)
        hub.set_recorder(writer.record)
        # When the hub auto-closes an idle session, mark a reset boundary in
        # the history file so no restart can re-use the closed session.
        hub.set_session_close_callback(writer.reset_session)
        logging.info("recording history to %s/YYYY-MM-DD.jsonl", history_dir)

    source = source_factory(config, hub) if source_factory else None

    async def apply_config(new_config: AppConfig) -> None:
        """Apply saved config edits live: reconcile the hub and the source."""
        await hub.reconcile_participants(new_config.participants)
        if source is not None and hasattr(source, "apply"):
            await source.apply(new_config.participants)
        if writer is not None:
            # Re-describe the session so the history header reflects the new
            # participant set/order.
            writer.start_session(new_config.participants)
        logging.info("applied config change (%d participants)", len(new_config.participants))

    async def start_new_session() -> None:
        """Reset live session state; mark the history so a restart won't restore it."""
        await hub.reset_session()
        if writer is not None:
            writer.reset_session()
        logging.info("started a new session (stats and sparklines cleared)")

    async def session_control(action: str) -> None:
        """Drive recording from the overlay/setup/history pages."""
        if action == "new":
            await start_new_session()
        elif action == "pause":
            await hub.pause_session()
            logging.info("paused recording (session kept; nothing recorded)")
        elif action == "resume":
            # Resuming after a stop has no session to return to, so it starts
            # (and marks) a new one instead.
            if hub.session_state == STOPPED:
                await start_new_session()
            else:
                await hub.resume_session()
                logging.info("resumed recording")
        elif action == "stop":
            await hub.stop_session()
            if writer is not None:
                # Mark a boundary so a restart can't re-seed the stopped session.
                writer.reset_session()
            logging.info("stopped recording (no session until restarted)")

    # The history page reads past sessions even when not writing (simulate /
    # --no-history), so resolve a directory to read from regardless.
    history_read_dir = history_dir or str(default_history_dir())

    # Set to end the run: by a signal, or by the supervising window process
    # going away (see _watch_supervisor).
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    if not port_scan:
        # Ask the port who is holding it before trying to bind, so a forgotten
        # instance reports itself by version and pid instead of surfacing as a
        # bare "address already in use".
        running = await asyncio.to_thread(
            instance.probe_after_grace, config.host, config.port
        )
        if running is not None:
            raise instance.AlreadyRunningError(running)

    runner, port = await run_server(
        hub,
        config.host,
        config.port,
        config=config,
        config_path=config_path,
        apply_config=apply_config,
        port_scan=port_scan,
        history_dir=history_read_dir,
        history_writer=writer,
        session_control=session_control,
    )

    if open_browser:
        url = f"http://{_browser_host(config.host)}:{port}/config"
        try:
            webbrowser.open(url)
            logging.info("opened setup page in browser: %s", url)
        except Exception as exc:  # noqa: BLE001 - never fail startup over this
            logging.debug("could not open browser: %s", exc)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - non-unix
            pass

    if supervised:
        _watch_supervisor(loop, stop)

    source_task = asyncio.create_task(source.run()) if source else None
    try:
        await stop.wait()
    finally:
        logging.info("shutting down...")
        if source is not None:
            source.stop()
        if source_task is not None:
            source_task.cancel()
        await hub.stop_watchdog()
        if writer is not None:
            await writer.close()
        await runner.cleanup()


async def _cmd_scan(args: argparse.Namespace) -> None:
    from .ble_collector import device_id_from_name, scan

    prefix = None if args.all else args.name_prefix
    print(f"Scanning for {args.timeout:.0f}s"
          + (f" (name prefix '{prefix}')" if prefix else " (all devices)") + " ...")
    devices = await scan(timeout=args.timeout, name_prefix=prefix)
    if not devices:
        print("No matching devices found.")
        return
    print(f"\nFound {len(devices)} device(s):\n")
    for address, name, services in devices:
        device_id = device_id_from_name(name)
        print(f"  {name}")
        if device_id:
            print(f"    deviceId: {device_id}   <- printed on the strap; use this")
        print(f"    address:  {address}   (macOS UUID, this Mac only)")
        if services:
            print(f"    services: {', '.join(services)}")
        print()
    print("Put the deviceId into config.json under the matching participant, e.g.:")
    print('    { "id": "participant-1", "displayName": "Alice", "deviceId": "16CD9E3C" }')
    print("Or use the setup page at /config while running `bio-overlay run`.")


async def _cmd_run(args: argparse.Namespace) -> None:
    from .ble_collector import BleCollector

    config = _load_config(args)
    history_dir = None
    if not args.no_history:
        history_dir = args.history_dir or str(default_history_dir())
    await _serve_with_source(
        config,
        lambda cfg, hub: BleCollector(cfg.participants, hub),
        history_dir=history_dir,
        config_path=_resolve_config_path(args),
        open_browser=_should_open_browser(args),
        port_scan=_should_port_scan(args),
        enable_respiration=args.respire_experiment,
        supervised=args.supervised,
    )


async def _cmd_simulate(args: argparse.Namespace) -> None:
    from .simulator import Simulator

    config = _load_config(args)
    await _serve_with_source(
        config,
        lambda cfg, hub: Simulator(cfg.participants, hub),
        config_path=_resolve_config_path(args),
        open_browser=_should_open_browser(args),
        port_scan=_should_port_scan(args),
        enable_respiration=args.respire_experiment,
        supervised=args.supervised,
    )


def _cmd_app(args: argparse.Namespace) -> None:
    """Open the app window, which runs and supervises the server.

    Synchronous on purpose: the macOS window server requires NSApplication to
    own the main thread, so this command runs there rather than inside
    asyncio.run() like the others.
    """
    from .window import WindowError, run_window

    config = _load_config(args)
    config_path = _resolve_config_path(args)
    try:
        run_window(args, config, config_path)
    except WindowError as exc:
        sys.exit(f"\nError: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bio-overlay", description=__doc__)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    parser.add_argument(
        "--version", action="version", version=f"bio-overlay {__version__}"
    )
    # `app` overrides this: it needs the main thread, so it isn't run under
    # asyncio.run() like every other subcommand.
    parser.set_defaults(blocking=False)
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="discover nearby BLE straps")
    p_scan.add_argument("--timeout", type=float, default=10.0)
    p_scan.add_argument("--name-prefix", default="Polar")
    p_scan.add_argument("--all", action="store_true", help="show all devices")
    p_scan.set_defaults(func=_cmd_scan)

    for name, func, help_text in (
        ("app", _cmd_app, "open the app window (default when double-clicked)"),
        ("run", _cmd_run, "collect from real straps and serve the overlay"),
        ("simulate", _cmd_simulate, "serve the overlay with simulated data"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("-c", "--config", help="path to config.json")
        p.add_argument("--host", help="server bind host (default 127.0.0.1)")
        p.add_argument(
            "--port", type=int, help=f"server port (default {DEFAULT_PORT})"
        )
        if name in ("run", "simulate"):
            p.add_argument(
                "--port-scan",
                action="store_true",
                help="if the port is busy, pick the next free one instead of "
                "reporting the instance already running there",
            )
            # The setup page opens in the browser on start by default. The app
            # window passes --no-browser, since it is the front end itself.
            p.add_argument(
                "--no-browser",
                action="store_true",
                help="do not auto-open the setup page in a browser",
            )
            p.add_argument(
                "--supervised",
                action="store_true",
                help="exit when the parent process does, by watching stdin for "
                "EOF (set by the app window; not useful on its own)",
            )
        if name in ("app", "run"):
            # Real readings are persisted to history/YYYY-MM-DD.json; simulated
            # data is never written there.
            p.add_argument(
                "--history-dir",
                default=None,
                help="directory for daily history files "
                "(default ./history, or ~/Documents/Bio-Overlay/history when packaged)",
            )
            p.add_argument(
                "--no-history",
                action="store_true",
                help="do not write the daily history file",
            )
        # Respiration is an experimental RSA-derived estimate, hidden by default.
        p.add_argument(
            "--respire-experiment",
            action="store_true",
            help="show the experimental respiration (breaths/min) estimate in the overlay",
        )
        # `app` owns the main thread for the window server; see _cmd_app.
        p.set_defaults(func=func, blocking=(name == "app"))

    return parser


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    # No arguments (e.g. double-clicking the app, or a bare `bio-overlay`)
    # defaults to `app`: a window, with the server as its child. `run` remains
    # the headless form for terminals and scripts.
    if not argv:
        argv = ["app"]
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        if args.blocking:
            args.func(args)
        else:
            asyncio.run(args.func(args))
    except KeyboardInterrupt:
        pass
    except instance.AlreadyRunningError as exc:
        running = exc.running
        print(f"\nError: {running.describe()} is already running.", file=sys.stderr)
        print(
            "Two copies fight over the same straps, so this one stopped instead.",
            file=sys.stderr,
        )
        print("Fix it by either:", file=sys.stderr)
        print(
            "  • using the copy that's running: "
            f"http://127.0.0.1:{running.port}/config",
            file=sys.stderr,
        )
        if running.pid is not None:
            print(f"  • stopping it:  kill {running.pid}", file=sys.stderr)
        sys.exit(1)
    except PortInUseError as exc:
        port = exc.port
        prog = "bio-overlay"
        print(f"\nError: port {port} is already in use.", file=sys.stderr)
        if exc.last_tried is not None:
            print(
                f"Ports {port}–{exc.last_tried} are all busy.", file=sys.stderr
            )
        # Not bio-overlay: /healthz was probed first and something else answered
        # (or nothing did).
        print("Something other than bio-overlay is holding it.", file=sys.stderr)
        print("Fix it by either:", file=sys.stderr)
        print(f"  • choosing a port:        {prog} --port 24700", file=sys.stderr)
        print(f"  • auto-picking a free one: {prog} --port-scan", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
