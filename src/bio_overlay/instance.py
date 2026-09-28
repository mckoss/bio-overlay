"""Find out whether a bio-overlay server is already holding our port.

The app used to scan past a busy port and start a second copy of itself, so a
forgotten instance from an earlier session kept streaming from the straps while
a freshly launched one looked broken. Asking the port who it is — rather than
only whether it is free — turns that into a message naming the version and pid.

Implemented with urllib rather than aiohttp so the window process (which has no
event loop of its own) and the server process can share one implementation; the
async side calls these through ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# Must match server.APP_ID. Anything else answering /healthz is not us.
APP_ID = "bio-overlay"


@dataclass(frozen=True)
class RunningInstance:
    """Identity of a bio-overlay server already listening on a port."""

    version: str
    pid: int | None
    port: int
    uptime_seconds: float | None

    def describe(self) -> str:
        parts = [f"bio-overlay {self.version}"]
        if self.pid is not None:
            parts.append(f"pid {self.pid}")
        if self.uptime_seconds is not None:
            parts.append(f"up {self.uptime_seconds:.0f}s")
        return f"{parts[0]} on port {self.port} ({', '.join(parts[1:])})"


def probe(host: str, port: int, timeout: float = 1.0) -> RunningInstance | None:
    """Return the instance holding `port`, or None if it is free or not ours.

    Not-ours (some other server on the port) reads as None on purpose: the
    caller's next step is to try binding, which will fail with a plain
    address-in-use error that says what it means.
    """
    url = f"http://{_probe_host(host)}:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    if payload.get("app") != APP_ID:
        return None
    return RunningInstance(
        version=str(payload.get("version", "unknown")),
        pid=payload.get("pid"),
        port=int(payload.get("port") or port),
        uptime_seconds=payload.get("uptimeSeconds"),
    )


def probe_after_grace(
    host: str, port: int, grace: float = 2.5, timeout: float = 1.0
) -> RunningInstance | None:
    """Probe, and if someone answers, give them `grace` seconds to finish exiting.

    Closing the window and immediately relaunching catches the previous server
    mid-shutdown, still bound while it flushes history. Reporting "already
    running" there would be true for half a second and baffling.
    """
    found = probe(host, port, timeout=timeout)
    if found is None:
        return None
    time.sleep(grace)
    return probe(host, port, timeout=timeout)


def wait_until_ready(
    host: str, port: int, deadline_seconds: float = 20.0, interval: float = 0.15
) -> RunningInstance | None:
    """Poll until a bio-overlay server answers on `port`, or the deadline passes."""
    end = time.monotonic() + deadline_seconds
    while time.monotonic() < end:
        found = probe(host, port, timeout=1.0)
        if found is not None:
            return found
        time.sleep(interval)
    return None


def _probe_host(host: str) -> str:
    """A wildcard bind isn't connectable; probe loopback instead."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


class AlreadyRunningError(Exception):
    """Another bio-overlay server already holds the port we were asked to bind."""

    def __init__(self, running: RunningInstance) -> None:
        self.running = running
        super().__init__(running.describe())
