# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for InstaEpi Monitor.
#
# Defender-friendly choices:
#   * onedir build (COLLECT) — NOT onefile. Self-extracting onefile stubs are
#     the most common cause of Defender false positives.
#   * upx=False everywhere — UPX-packed binaries are aggressively flagged.
#   * Embedded version_info metadata — unsigned binaries with no metadata are
#     treated as more suspicious.
#
# Build:  pyinstaller InstaEpiMonitor.spec --noconfirm
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# Bundle Playwright (its Node driver lives inside the package as data files).
# The Chromium browser itself is NOT bundled — at runtime Playwright uses the
# per-user cache in %LOCALAPPDATA%\ms-playwright (installed via
# `playwright install chromium`).
for pkg in ("playwright",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim heavy/unused libs to shrink the build and lower AV heuristics.
        "tkinter", "PyQt5", "PySide2", "PySide6",
        "matplotlib", "scipy", "notebook", "IPython", "jupyter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InstaEpiMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # do NOT compress — Defender flags UPX
    console=False,            # GUI app, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,                # do NOT compress
    upx_exclude=[],
    name="InstaEpiMonitor",
)
