"""The desktop app's window process — it owns the app's lifetime.

Architecture: the window is the parent, the server is its child.

    bio-overlay.app  ->  window process (webview, main thread, Dock icon)
                           └─ bio-overlay run --no-browser --supervised --port N

The server stays a plain Python process with no GUI code in it, so bleak's
CoreBluetooth backend never has to share a thread with a window event loop, and
`bio-overlay run` from a terminal behaves exactly as it always has.

Closing the window ends this process, which closes the pipe it holds as the
child's stdin; the child reads EOF and shuts itself down gracefully. That
indirection is the point: a parent's death does not kill its children on macOS
or Windows, so "the window is gone but the server is still streaming" was a
state the app could previously reach and hide.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import webbrowser

from . import __version__, instance
from .config import AppConfig
from .paths import is_frozen

logger = logging.getLogger(__name__)

WINDOW_TITLE = "bio-overlay"
# Wide enough for the setup page's participant rows and the overlay preview.
WINDOW_SIZE = (1100, 880)
MIN_WINDOW_SIZE = (720, 520)

# How long to wait for the server to come up before giving up on it.
SERVER_START_TIMEOUT_S = 20.0
# How long to let the child flush history and release the port after we close
# its stdin. Only a wedged child ever reaches the end of this.
SERVER_STOP_TIMEOUT_S = 5.0


class WindowError(Exception):
    """The window process can't start; the message is shown to the user."""


def run_window(args, config: AppConfig, config_path: str) -> int:
    """Start the server, show the window, and stop the server on the way out."""
    existing = instance.probe_after_grace(config.host, config.port)
    if existing is not None:
        raise WindowError(
            f"{existing.describe()} is already running.\n"
            "Quit it before starting another copy — two collectors fight over "
            "the same straps."
        )

    child = _spawn_server(args, config, config_path)
    logger.info("started server process (pid %d) on port %d", child.pid, config.port)

    ready = instance.wait_until_ready(
        config.host, config.port, deadline_seconds=SERVER_START_TIMEOUT_S
    )
    if ready is None:
        _stop_server(child)
        raise WindowError(
            f"The bio-overlay server did not start on port {config.port} within "
            f"{SERVER_START_TIMEOUT_S:.0f}s."
        )
    if ready.version != __version__:
        # Only reachable if a stray binary answered first; worth saying out loud
        # rather than silently showing someone else's pages.
        logger.warning(
            "server reports version %s but this window is %s", ready.version, __version__
        )

    url = f"http://{instance._probe_host(config.host)}:{config.port}/config"
    try:
        _show_window(url, child)
    finally:
        _stop_server(child)
    return 0


def _spawn_server(args, config: AppConfig, config_path: str) -> subprocess.Popen:
    """Launch `bio-overlay run` as a child, holding its stdin as a deadman pipe."""
    argv = _server_argv(args, config, config_path)
    logger.debug("spawning server: %s", " ".join(argv))
    try:
        # stdin=PIPE is the whole mechanism: we never write to it, and whatever
        # ends this process — clean quit, crash, Force Quit, kill -9 — closes it.
        return subprocess.Popen(argv, stdin=subprocess.PIPE)
    except OSError as exc:
        raise WindowError(f"Could not start the bio-overlay server: {exc}") from exc


def _server_argv(args, config: AppConfig, config_path: str) -> list[str]:
    # Frozen, sys.executable is the app binary itself, which dispatches on its
    # first argument — so one build serves as both the window and the server.
    base = [sys.executable] if is_frozen() else [sys.executable, "-m", "bio_overlay"]
    argv = base + [
        "run",
        "--no-browser",
        "--supervised",
        # Explicit, so the child cannot land anywhere but where the window looks.
        "--port",
        str(config.port),
        "--config",
        config_path,
    ]
    if getattr(args, "verbose", False):
        argv.insert(1, "--verbose")
    if getattr(args, "no_history", False):
        argv.append("--no-history")
    elif getattr(args, "history_dir", None):
        argv += ["--history-dir", args.history_dir]
    if getattr(args, "respire_experiment", False):
        argv.append("--respire-experiment")
    return argv


def _show_window(url: str, child: subprocess.Popen) -> None:
    """Show the setup page in an embedded webview, or fall back to a browser."""
    try:
        import webview  # noqa: PLC0415 - optional, and slow to import
    except ImportError:
        logger.warning("pywebview is not installed; opening the setup page in a browser")
        _browser_fallback(url, child)
        return

    # The pages open each other (overlay preview, Setup, History) with
    # target="_blank", which pywebview hands to the system browser by default.
    # Every such link is to this server, so keep them in the app window.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=MIN_WINDOW_SIZE,
    )
    # A dead server leaves the window showing a page that can never reconnect,
    # which looks like the app is still running. Close it instead.
    threading.Thread(
        target=_close_window_when_child_exits,
        args=(child, window),
        name="server-watchdog",
        daemon=True,
    ).start()

    try:
        # Returns when the last window closes. pywebview sets a regular
        # activation policy on macOS, so this is a normal Dock app.
        webview.start()
    except Exception as exc:  # noqa: BLE001 - never leave the app unlaunchable
        logger.warning("could not open the app window (%s); falling back to a browser", exc)
        _browser_fallback(url, child)


def _browser_fallback(url: str, child: subprocess.Popen) -> None:
    """No webview: open the default browser and supervise until the server stops.

    Degraded but never broken — closing the browser tab won't stop the app, the
    way it didn't before this change.
    """
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not open a browser: %s", exc)
    print(f"bio-overlay is running. Setup page: {url}")
    print("Press Ctrl-C to stop.")
    try:
        child.wait()
    except KeyboardInterrupt:
        pass


def _close_window_when_child_exits(child: subprocess.Popen, window) -> None:
    child.wait()
    logger.info("server process exited (code %s); closing the window", child.returncode)
    try:
        window.destroy()
    except Exception:  # noqa: BLE001 - the window may already be gone
        pass


def _stop_server(child: subprocess.Popen) -> None:
    """Close the deadman pipe and give the child a moment to shut down cleanly."""
    if child.poll() is not None:
        return
    if child.stdin is not None:
        try:
            # Exiting would close this anyway; doing it here just means the port
            # is free by the time the user can double-click the app again.
            child.stdin.close()
        except OSError:
            pass
    try:
        child.wait(timeout=SERVER_STOP_TIMEOUT_S)
        return
    except subprocess.TimeoutExpired:
        pass
    # A backstop, not the mechanism. The child should already be exiting on EOF;
    # if it is wedged, leaving it behind would recreate the orphaned-server
    # problem this whole design exists to prevent.
    logger.warning("server did not exit on its own after %.0fs; terminating",
                   SERVER_STOP_TIMEOUT_S)
    child.terminate()
    try:
        child.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        child.kill()
