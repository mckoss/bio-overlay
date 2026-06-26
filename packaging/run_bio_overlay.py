"""Frozen-app entry point for PyInstaller.

Kept separate from the package so the bundled executable has a tiny, explicit
entry that calls the normal CLI.
"""

import multiprocessing

from bio_overlay.cli import main

if __name__ == "__main__":
    # Required so a frozen executable doesn't re-spawn itself on platforms that
    # use spawn() for multiprocessing.
    multiprocessing.freeze_support()
    main()
