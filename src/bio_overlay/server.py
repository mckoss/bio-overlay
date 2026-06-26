"""Local telemetry server: static overlay files + WebSocket broadcast + config UI.

A single aiohttp app serves the transparent overlay page (for the OBS Browser
Source), a WebSocket that pushes telemetry snapshots to overlay clients, and a
config/setup page for editing participants and pairing straps.

Endpoints:
    GET  /            -> overlay/index.html (transparent overlay)
    GET  /config      -> overlay/config.html (setup UI)
    GET  /<file>      -> static overlay assets
    GET  /ws          -> WebSocket; receives {"type": "state", ...} snapshots
    GET  /healthz     -> liveness probe
    GET  /api/config  -> current config as JSON
    PUT  /api/config  -> save config to disk
    GET  /api/scan    -> discover nearby straps (deviceId, name, address)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from aiohttp import WSCloseCode, WSMsgType, web

from .config import AppConfig
from .telemetry import TelemetryHub

logger = logging.getLogger(__name__)


def _overlay_dir() -> Path:
    """Locate the overlay/ static assets in dev and in a PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", None)
    if base:  # bundled: assets are added under <bundle>/overlay
        return Path(base) / "overlay"
    return Path(__file__).resolve().parents[2] / "overlay"


OVERLAY_DIR = _overlay_dir()


async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
    hub: TelemetryHub = request.app["hub"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    logger.info("overlay client connected (%s)", request.remote)

    async def send(message: dict) -> None:
        await ws.send_str(json.dumps(message))

    # Send current state immediately so a freshly loaded overlay isn't blank.
    await send(hub.snapshot())
    hub.subscribe(send)
    request.app["websockets"].add(ws)

    try:
        async for msg in ws:
            # The overlay is receive-only today; just drain any inbound frames.
            if msg.type == WSMsgType.ERROR:
                logger.warning("ws error: %s", ws.exception())
    finally:
        hub.unsubscribe(send)
        request.app["websockets"].discard(ws)
        logger.info("overlay client disconnected (%s)", request.remote)
    return ws


async def _on_shutdown(app: web.Application) -> None:
    """Close open overlay sockets so shutdown doesn't wait on their heartbeat."""
    for ws in set(app["websockets"]):
        await ws.close(code=WSCloseCode.GOING_AWAY, message=b"server shutdown")


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(OVERLAY_DIR / "index.html")


async def _config_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(OVERLAY_DIR / "config.html")


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# -- config API -----------------------------------------------------------

DEFAULT_CONFIG_PATH = "config.json"


def _config_path(app: web.Application) -> Path:
    return Path(app["config_path"] or DEFAULT_CONFIG_PATH)


async def _get_config(request: web.Request) -> web.Response:
    path = _config_path(request.app)
    if path.exists():
        config = AppConfig.load(path)
    else:
        config = request.app["config"] or AppConfig.default()
    return web.json_response({"path": str(path), "config": config.to_dict()})


async def _put_config(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(reason="invalid JSON")
    try:
        config = AppConfig.from_dict(body)
    except (KeyError, TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(reason=f"invalid config: {exc}")
    if not config.participants:
        raise web.HTTPBadRequest(reason="at least one participant is required")
    ids = [p.id for p in config.participants]
    if len(set(ids)) != len(ids):
        raise web.HTTPBadRequest(reason="participant ids must be unique")

    path = _config_path(request.app)
    config.save(path)
    request.app["config"] = config
    logger.info("saved config to %s (%d participant(s))", path, len(config.participants))
    return web.json_response({"ok": True, "path": str(path)})


async def _scan(request: web.Request) -> web.Response:
    from .ble_collector import device_id_from_name, scan

    timeout = float(request.query.get("timeout", 8))
    prefix = request.query.get("namePrefix", "Polar")
    try:
        devices = await scan(timeout=timeout, name_prefix=prefix or None)
    except Exception as exc:  # noqa: BLE001 - surface scan errors to the UI
        logger.warning("scan failed: %s", exc)
        raise web.HTTPInternalServerError(reason=f"scan failed: {exc}")
    return web.json_response(
        {
            "devices": [
                {
                    "deviceId": device_id_from_name(name),
                    "name": name,
                    "address": address,
                }
                for address, name, _services in devices
            ]
        }
    )


def build_app(
    hub: TelemetryHub,
    config: AppConfig | None = None,
    config_path: str | None = None,
) -> web.Application:
    app = web.Application()
    app["hub"] = hub
    app["config"] = config
    app["config_path"] = config_path
    app["websockets"] = set()
    app.on_shutdown.append(_on_shutdown)
    app.add_routes(
        [
            web.get("/", _index),
            web.get("/config", _config_page),
            web.get("/ws", _ws_handler),
            web.get("/healthz", _healthz),
            web.get("/api/config", _get_config),
            web.put("/api/config", _put_config),
            web.get("/api/scan", _scan),
        ]
    )
    # Serve remaining overlay assets (css/js) as static files.
    app.router.add_static("/", OVERLAY_DIR, show_index=False)
    return app


async def run_server(
    hub: TelemetryHub,
    host: str,
    port: int,
    config: AppConfig | None = None,
    config_path: str | None = None,
) -> web.AppRunner:
    """Start the server and return the runner (caller is responsible for cleanup)."""
    app = build_app(hub, config=config, config_path=config_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("overlay server on http://%s:%d  (OBS Browser Source URL)", host, port)
    logger.info("config/setup page at http://%s:%d/config", host, port)
    return runner
