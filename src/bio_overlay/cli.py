"""Command-line entry point.

Subcommands:
    scan       Discover nearby BLE straps and print their macOS UUIDs.
    run        Start the telemetry server + BLE collector (needs hardware).
    simulate   Start the telemetry server + simulated data (no hardware).

Both `run` and `simulate` serve the overlay at http://<host>:<port>/ for use as
an OBS Browser Source.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from .config import AppConfig
from .server import run_server
from .telemetry import TelemetryHub


def _load_config(args: argparse.Namespace) -> AppConfig:
    config = AppConfig.load(args.config) if args.config else AppConfig.default()
    if getattr(args, "host", None):
        config.host = args.host
    if getattr(args, "port", None):
        config.port = args.port
    return config


def _build_hub(config: AppConfig) -> TelemetryHub:
    hub = TelemetryHub(stale_after_s=config.stale_after_seconds)
    for p in config.participants:
        hub.register_participant(p.id, p.display_name)
    return hub


async def _serve_with_source(config: AppConfig, source_factory) -> None:
    """Run the server alongside a telemetry source (collector or simulator)."""
    hub = _build_hub(config)
    hub.start_watchdog()
    runner = await run_server(hub, config.host, config.port)
    source = source_factory(config, hub)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - non-unix
            pass

    source_task = asyncio.create_task(source.run())
    try:
        await stop.wait()
    finally:
        logging.info("shutting down...")
        source.stop()
        source_task.cancel()
        await hub.stop_watchdog()
        await runner.cleanup()


async def _cmd_scan(args: argparse.Namespace) -> None:
    from .ble_collector import scan

    prefix = None if args.all else args.name_prefix
    print(f"Scanning for {args.timeout:.0f}s"
          + (f" (name prefix '{prefix}')" if prefix else " (all devices)") + " ...")
    devices = await scan(timeout=args.timeout, name_prefix=prefix)
    if not devices:
        print("No matching devices found.")
        return
    print(f"\nFound {len(devices)} device(s):\n")
    for address, name, services in devices:
        device_id = _device_id_from_name(name)
        print(f"  {name}")
        if device_id:
            print(f"    deviceId: {device_id}   <- printed on the strap; use this")
        print(f"    address:  {address}   (macOS UUID, this Mac only)")
        if services:
            print(f"    services: {', '.join(services)}")
        print()
    print("Put the deviceId into config.json under the matching participant, e.g.:")
    print('    { "id": "participant-1", "displayName": "Alice", "deviceId": "16CD9E3C" }')


def _device_id_from_name(name: str) -> str | None:
    """Extract the trailing Polar device ID from an advertised name.

    "Polar H10 16CD9E3C" -> "16CD9E3C". Returns None if there's no trailing
    token that looks like an ID.
    """
    parts = name.split()
    if len(parts) >= 2 and parts[-1] not in {"H10", "?"}:
        return parts[-1]
    return None


async def _cmd_run(args: argparse.Namespace) -> None:
    from .ble_collector import BleCollector

    config = _load_config(args)
    await _serve_with_source(
        config,
        lambda cfg, hub: BleCollector(cfg.participants, hub),
    )


async def _cmd_simulate(args: argparse.Namespace) -> None:
    from .simulator import Simulator

    config = _load_config(args)
    await _serve_with_source(
        config,
        lambda cfg, hub: Simulator(cfg.participants, hub),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bio-overlay", description=__doc__)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
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
        p.set_defaults(func=func)

    return parser


def main(argv: list[str] | None = None) -> None:
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


if __name__ == "__main__":
    main()
