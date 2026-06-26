# PyInstaller spec — builds a single-file `bio-overlay` executable.
#
# Build from the repo root:
#     pyinstaller packaging/bio-overlay.spec
#
# Output: dist/bio-overlay (macOS/Linux) or dist/bio-overlay.exe (Windows).
# Note: PyInstaller does not cross-compile — build on each target OS (CI does both).

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # SPECPATH = directory of this spec file

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
)
