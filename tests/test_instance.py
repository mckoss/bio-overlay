"""Tests for the "is one already running?" probe.

The app's original failure was a stale instance masquerading as a free port, so
what matters here is that the probe identifies bio-overlay specifically — and
stays quiet about anything else listening.
"""

import asyncio
import json

import pytest
from aiohttp import web

from bio_overlay import __version__, instance
from bio_overlay.server import run_server
from bio_overlay.telemetry import TelemetryHub


async def _free_port() -> int:
    """Bind and release a port, so the number is almost certainly still free."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port


@pytest.fixture
async def running_server():
    """A real bio-overlay server on a free port."""
    port = await _free_port()
    runner, bound = await run_server(TelemetryHub(), "127.0.0.1", port)
    try:
        yield bound
    finally:
        await runner.cleanup()


async def test_probe_identifies_a_running_instance(running_server):
    found = await asyncio.to_thread(instance.probe, "127.0.0.1", running_server)
    assert found is not None
    assert found.version == __version__
    assert found.port == running_server
    assert found.pid is not None
    # The message is the whole point of the probe — it has to name the version.
    assert __version__ in found.describe()


async def test_probe_returns_none_when_nothing_is_listening():
    port = await _free_port()
    assert await asyncio.to_thread(instance.probe, "127.0.0.1", port) is None


async def test_probe_ignores_a_server_that_is_not_bio_overlay():
    """Someone else's /healthz must not read as our app.

    Otherwise the app would refuse to start and blame a copy of itself that
    isn't there.
    """

    async def healthz(_request):
        return web.json_response({"ok": True})

    app = web.Application()
    app.add_routes([web.get("/healthz", healthz)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = await _free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    try:
        assert await asyncio.to_thread(instance.probe, "127.0.0.1", port) is None
    finally:
        await runner.cleanup()


async def test_healthz_payload_identifies_the_app(running_server):
    """The probe's contract, checked from the wire rather than through probe()."""
    import urllib.request

    def fetch():
        with urllib.request.urlopen(
            f"http://127.0.0.1:{running_server}/healthz", timeout=2
        ) as response:
            return json.loads(response.read().decode())

    payload = await asyncio.to_thread(fetch)
    assert payload["app"] == instance.APP_ID
    assert payload["version"] == __version__
    assert payload["port"] == running_server
    assert payload["uptimeSeconds"] >= 0


def test_probe_host_maps_wildcard_binds_to_loopback():
    """A server bound to 0.0.0.0 isn't reachable at that address."""
    assert instance._probe_host("0.0.0.0") == "127.0.0.1"
    assert instance._probe_host("") == "127.0.0.1"
    assert instance._probe_host("192.168.1.5") == "192.168.1.5"
