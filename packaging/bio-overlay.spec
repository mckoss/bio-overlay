# PyInstaller spec — builds a single-file `bio-overlay` executable, plus a
# double-clickable bio-overlay.app bundle on macOS.
#
# Build from the repo root:
#     pyinstaller packaging/bio-overlay.spec
#
# Output: dist/bio-overlay (+ dist/bio-overlay.app on macOS) or
# dist/bio-overlay.exe (Windows).
# Note: PyInstaller does not cross-compile — build on each target OS (CI does both).

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # SPECPATH = directory of this spec file

# Read the version from the package without importing it.
_init = (ROOT / "src" / "bio_overlay" / "__init__.py").read_text()
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _init).group(1)

# Platform icon: .ico for the Windows exe, .icns for the macOS app bundle.
_ico = ROOT / "packaging" / "icon.ico"
_icns = ROOT / "packaging" / "icon.icns"
EXE_ICON = str(_ico) if sys.platform == "win32" and _ico.exists() else None
APP_ICON = str(_icns) if _icns.exists() else None

# Bundle the overlay/ static assets; server._overlay_dir() reads them from
# sys._MEIPASS/overlay at runtime.
datas = [(str(ROOT / "overlay"), "overlay")]
for pkg in ("bleak", "aiohttp"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# Pull in the platform-specific bleak backend (CoreBluetooth / WinRT / BlueZ).
hiddenimports = collect_submodules("bleak")

a = Analysis(
    [str(ROOT / "packaging" / "run_bio_overlay.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="bio-overlay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=EXE_ICON,
)

# On macOS, wrap the executable in a .app bundle so it's double-clickable from
# Finder. The Info.plist Bluetooth usage strings let macOS show the BLE
# permission prompt when the app first scans for straps.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="bio-overlay.app",
        icon=APP_ICON,
        bundle_identifier="com.mckoss.bio-overlay",
        version=VERSION,
        info_plist={
            "CFBundleName": "bio-overlay",
            "CFBundleDisplayName": "bio-overlay",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "NSBluetoothAlwaysUsageDescription":
                "bio-overlay reads heart-rate data from Bluetooth chest straps.",
            "NSBluetoothPeripheralUsageDescription":
                "bio-overlay reads heart-rate data from Bluetooth chest straps.",
        },
    )
