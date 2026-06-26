"""Command-line entry point.

Subcommands:
    scan       Discover nearby BLE straps and print their device IDs.
    run        Start the telemetry server + BLE collector (needs hardware).
    simulate   Start the telemetry server + simulated data (no hardware).

`run` and `simulate` serve the overlay at http://<host>:<port>/ for use as an
OBS Browser Source, and the setup page at /config to edit config and pair straps.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .config import AppConfig
from .paths import default_config_path, default_history_dir
from .server import PortInUseError, run_server
from .telemetry import TelemetryHub


def _resolve_config_path(args: argparse.Namespace) -> str:
    return args.config or str(default_config_path())


def _load_config(args: argparse.Namespace) -> AppConfig:
    path = Path(_resolve_config_path(args))
    config = AppConfig.load(path) if path.exists() else AppConfig.default()
    if getattr(args, "host", None):
        config.host = args.host
    if getattr(args, "port", None):
        config.port = args.port
    return config


def _should_open_browser(args: argparse.Namespace) -> bool:
    """Auto-open the setup page on start unless told not to (same in repo & exe)."""
    return not getattr(args, "no_browser", False)


def _should_port_scan(args: argparse.Namespace) -> bool:
    """Scan for a free port by default; an explicit --port is strict unless
    --port-scan is also given."""
    return bool(getattr(args, "port_scan", False)) or getattr(args, "port", None) is None


def _browser_host(host: str) -> str:
    # A wildcard bind isn't browsable; point the browser at loopback.
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _build_hub(config: AppConfig) -> TelemetryHub:
    hub = TelemetryHub(stale_after_s=config.stale_after_seconds)
    for p in config.participants:
        hub.register_participant(p.id, p.display_name, device_id=p.device_id)
    return hub


async def _serve_with_source(
    config: AppConfig,
    source_factory,
    *,
    history_dir: str | None = None,
    config_path: str | None = None,
    open_browser: bool = False,
    port_scan: bool = False,
) -> None:
    """Run the server alongside a telemetry source (collector or simulator).

    If source_factory is None, only the server runs (e.g. the `config` setup UI).
    If history_dir is given, real readings are persisted to a daily JSON file.
    """
    hub = _build_hub(config)
    hub.start_watchdog()

    writer = None
    if history_dir:
        from datetime import datetime, timezone

        from .history import DailyHistoryWriter, read_records

        # Restore an in-progress session from today's file so a server restart
        # mid-session keeps the sparkline, session stats, and respiration.
        today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        seeded = read_records(history_dir, today)
        if seeded:
            hub.seed_history(seeded)
            logging.info("restored %d readings from %s/%s.jsonl", len(seeded), history_dir, today)

        writer = DailyHistoryWriter(history_dir)
        writer.start()
        writer.start_session(config.participants)
        hub.set_recorder(writer.record)
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

    # The history page reads past sessions even when not writing (simulate /
    # --no-history), so resolve a directory to read from regardless.
    history_read_dir = history_dir or str(default_history_dir())

    # The Quit button on the setup page POSTs /api/quit, which sets this.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    runner, port = await run_server(
        hub,
        config.host,
        config.port,
        config=config,
        config_path=config_path,
        apply_config=apply_config,
        port_scan=port_scan,
        history_dir=history_read_dir,
        request_shutdown=lambda: loop.call_soon_threadsafe(stop.set),
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
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bio-overlay", description=__doc__)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    parser.add_argument(
        "--version", action="version", version=f"bio-overlay {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="discover nearby BLE straps")
    p_scan.add_argument("--timeout", type=float, default=10.0)
    p_scan.add_argument("--name-prefix", default="Polar")
    p_scan.add_argument("--all", action="store_true", help="show all devices")
    p_scan.set_defaults(func=_cmd_scan)

    for name, func, help_text in (
        ("run", _cmd_run, "collect from real straps and serve the overlay"),
        ("simulate", _cmd_simulate, "serve the overlay with simulated data"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("-c", "--config", help="path to config.json")
        p.add_argument("--host", help="server bind host (default 127.0.0.1)")
        p.add_argument("--port", type=int, help="server port (default 8080)")
        p.add_argument(
            "--port-scan",
            action="store_true",
            help="if the port is busy, pick the next free one "
            "(default unless --port is given explicitly)",
        )
        if name == "run":
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
        # The setup page opens in the browser on start by default.
        p.add_argument(
            "--no-browser",
            action="store_true",
            help="do not auto-open the setup page in a browser",
        )
        p.set_defaults(func=func)

    return parser


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    # No arguments (e.g. double-clicking the executable, or a bare `bio-overlay`)
    # defaults to `run`, which starts collecting and opens the setup page.
    if not argv:
        argv = ["run"]
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        pass
    except PortInUseError as exc:
        port = exc.port
        prog = "bio-overlay"
        print(f"\nError: port {port} is already in use.", file=sys.stderr)
        if exc.last_tried is not None:
            print(
                f"Ports {port}–{exc.last_tried} are all busy.", file=sys.stderr
            )
        print("Another bio-overlay window may already be running.", file=sys.stderr)
        print("Fix it by either:", file=sys.stderr)
        print(f"  • choosing a port:        {prog} --port 8090", file=sys.stderr)
        print(f"  • auto-picking a free one: {prog} --port-scan", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
