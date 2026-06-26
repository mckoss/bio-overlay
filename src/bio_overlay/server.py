"""Local telemetry server: static overlay files + WebSocket broadcast.

A single aiohttp app serves the transparent overlay page (for the OBS Browser
Source) and a WebSocket endpoint that pushes telemetry snapshots to every
connected overlay client.

Endpoints:
    GET /            -> overlay/index.html
    GET /<file>      -> static overlay assets
    GET /ws          -> WebSocket; receives {"type": "state", ...} snapshots
    GET /healthz     -> liveness probe (also handy to confirm the server is up)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import WSMsgType, web

from .telemetry import TelemetryHub

logger = logging.getLogger(__name__)

# Repo-root/overlay holds the static client.
OVERLAY_DIR = Path(__file__).resolve().parents[2] / "overlay"


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

    try:
        async for msg in ws:
            # The overlay is receive-only today; just drain any inbound frames.
            if msg.type == WSMsgType.ERROR:
                logger.warning("ws error: %s", ws.exception())
    finally:
        hub.unsubscribe(send)
        logger.info("overlay client disconnected (%s)", request.remote)
    return ws


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(OVERLAY_DIR / "index.html")


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def build_app(hub: TelemetryHub) -> web.Application:
    app = web.Application()
    app["hub"] = hub
    app.add_routes(
        [
            web.get("/", _index),
            web.get("/ws", _ws_handler),
            web.get("/healthz", _healthz),
        ]
    )
    # Serve remaining overlay assets (css/js) as static files.
    app.router.add_static("/", OVERLAY_DIR, show_index=False)
    return app


async def run_server(hub: TelemetryHub, host: str, port: int) -> web.AppRunner:
    """Start the server and return the runner (caller is responsible for cleanup)."""
    app = build_app(hub)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("overlay server on http://%s:%d  (OBS Browser Source URL)", host, port)
    return runner
