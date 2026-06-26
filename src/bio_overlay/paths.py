"""Where config and history files live.

When running from a source checkout, files default to the current directory
(``./config.json``, ``./history/``) — convenient for development. When running
from a packaged executable (PyInstaller sets ``sys.frozen``), there is no useful
"current directory", so files default to a ``Bio-Overlay`` folder under the
user's Documents directory.

An explicit path/flag on the command line always overrides these defaults.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR_NAME = "Bio-Overlay"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def default_data_dir() -> Path:
    """Base directory for config and history."""
    if is_frozen():
        return Path.home() / "Documents" / APP_DIR_NAME
    return Path.cwd()


def default_config_path() -> Path:
    return default_data_dir() / "config.json"


def default_history_dir() -> Path:
    return default_data_dir() / "history"
