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
import html
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
# The setup page's --bg, so the window never flashes white between pages.
PAGE_BACKGROUND = "#14161c"

# How long to wait for the server to come up before giving up on it.
SERVER_START_TIMEOUT_S = 20.0
# How long to let the child flush history and release the port after we close
# its stdin. Only a wedged child ever reaches the end of this.
SERVER_STOP_TIMEOUT_S = 5.0


class WindowError(Exception):
    """The window process can't start; the message is shown to the user."""


def run_window(args, config: AppConfig, config_path: str) -> int:
    """Show the window, start the server behind it, and stop the server on exit.

    The window opens first, on a local loading page, and switches to the setup
    page once the server answers. Starting the server takes a second or two
    (more on a cold launch), and a window that appears only after that delay
    reads as the app not responding.
    """
    try:
        import webview  # noqa: PLC0415 - optional, and slow to import
    except ImportError:
        logger.warning("pywebview is not installed; opening the setup page in a browser")
        return _run_in_browser(args, config, config_path)

    # The pages open each other (overlay preview, Setup, History) with
    # target="_blank", which pywebview hands to the system browser by default.
    # Every such link is to this server, so keep them in the app window.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False

    window = webview.create_window(
        WINDOW_TITLE,
        html=_status_page("Starting bio-overlay…", loading=True),
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=MIN_WINDOW_SIZE,
        background_color=PAGE_BACKGROUND,
    )
    started: list[subprocess.Popen] = []

    def boot() -> None:
        # Runs on pywebview's worker thread once the window is up.
        try:
            url = _start_server(args, config, config_path, started)
        except WindowError as exc:
            # A double-clicked app has no terminal, so the window is the only
            # place this can be said. Closing it quits, as usual.
            logger.error("%s", exc)
            window.load_html(_status_page(str(exc)))
            return
        # A dead server leaves the window showing a page that can never
        # reconnect, which looks like the app is still running. Close it instead.
        threading.Thread(
            target=_close_window_when_child_exits,
            args=(started[0], window),
            name="server-watchdog",
            daemon=True,
        ).start()
        window.load_url(url)

    try:
        # Returns when the last window closes. pywebview sets a regular
        # activation policy on macOS, so this is a normal Dock app.
        webview.start(boot)
    except Exception as exc:  # noqa: BLE001 - never leave the app unlaunchable
        if started:
            raise
        logger.warning("could not open the app window (%s); falling back to a browser", exc)
        return _run_in_browser(args, config, config_path)
    finally:
        if started:
            _stop_server(started[0])
    return 0


def _start_server(
    args, config: AppConfig, config_path: str, started: list[subprocess.Popen]
) -> str:
    """Spawn the server child and wait for it; return the setup page URL.

    The child is appended to `started` as soon as it exists, so the caller stops
    it even if this is interrupted partway.
    """
    existing = instance.probe_after_grace(config.host, config.port)
    if existing is not None:
        raise WindowError(
            f"{existing.describe()} is already running.\n"
            "Quit it before starting another copy — two collectors fight over "
            "the same straps."
        )

    child = _spawn_server(args, config, config_path)
    started.append(child)
    logger.info("started server process (pid %d) on port %d", child.pid, config.port)

    ready = instance.wait_until_ready(
        config.host, config.port, deadline_seconds=SERVER_START_TIMEOUT_S
    )
    if ready is None:
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
    return f"http://{instance._probe_host(config.host)}:{config.port}/config"


def _run_in_browser(args, config: AppConfig, config_path: str) -> int:
    """No webview: start the server, then hand the setup page to a browser."""
    started: list[subprocess.Popen] = []
    try:
        url = _start_server(args, config, config_path, started)
        _browser_fallback(url, started[0])
    finally:
        if started:
            _stop_server(started[0])
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


def _status_page(message: str, *, loading: bool = False) -> str:
    """The page the window shows before (or instead of) the setup page.

    Self-contained HTML, since the server that serves everything else may not
    be up yet. Colors match the setup page, so the switch to it doesn't flash.
    """
    spinner = '<div class="spinner" aria-hidden="true"></div>' if loading else ""
    body_class = ' class="loading"' if loading else ""
    body = html.escape(message).replace("\n", "<br>")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{WINDOW_TITLE}</title>
<style>
  html, body {{ height: 100%; margin: 0; }}
  body {{
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 20px; padding: 0 48px;
    background: {PAGE_BACKGROUND}; color: #e8eaf0; text-align: center;
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  .spinner {{
    width: 36px; height: 36px; border-radius: 50%;
    border: 3px solid rgba(255, 255, 255, 0.12); border-top-color: #ff4d4f;
    animation: spin 0.8s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  p {{ margin: 0; max-width: 34em; }}
  .version {{ color: #7d8394; font-size: 12px; }}
  /* A warm start swaps in the setup page ~0.1s later; showing the spinner
     only after a beat keeps that from flickering. */
  body.loading > * {{ opacity: 0; animation: appear 0.2s 0.3s forwards; }}
  @keyframes appear {{ to {{ opacity: 1; }} }}
</style></head>
<body{body_class}>{spinner}<p>{body}</p><p class="version">v{__version__}</p></body></html>"""


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
