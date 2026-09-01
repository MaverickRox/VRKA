# -*- mode: python ; coding: utf-8 -*-
"""Reproducible Windows x64 PyInstaller recipe for VRKA."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all


project_dir = Path(SPECPATH)
datas = []
# Only bundle the branding assets actually used at runtime (wolf, icons, ico).
# The full branding folder previously included ~2 MB of unused sources:
# canonical PNG, contact sheet, 1024/512 variants, icns, and all *-48.png icons.
branding_dir = project_dir / "assets" / "branding"
datas.append((str(branding_dir / "vrka.ico"), "assets/branding"))
datas.append((str(branding_dir / "vrka-wolf-256.png"), "assets/branding"))
for _p in (branding_dir / "nav").glob("*-32.png"):
    datas.append((str(_p), "assets/branding/nav"))
for _p in (branding_dir / "v2icons").glob("*-32.png"):
    datas.append((str(_p), "assets/branding/v2icons"))
datas.append((str(project_dir / "assets" / "fonts"), "assets/fonts"))
datas.append((str(project_dir / "assets" / "browser_protection"), "assets/browser_protection"))
datas.append((str(project_dir / "third_party" / "media_observer" / "puemos-hls-downloader" / "extension-mv3-chrome-v5.5.0.zip"), "third_party/media_observer/puemos-hls-downloader"))
datas.append((str(project_dir / "THIRD_PARTY_NOTICES.md"), "."))
datas.append((str(project_dir / "vrka_qml" / "qml"), "vrka_qml/qml"))
# FFmpeg/ffprobe binaries are distributed alongside VRKA.exe in ffmpeg_bin/
# (in both the portable directory and installer payload) to avoid 203MB duplication
# inside the single-file exe and eliminate TEMP extraction latency.
binaries = []
hiddenimports = []

deno_executable = project_dir / "deno_bin" / "deno.exe"
if deno_executable.is_file():
    binaries.append((str(deno_executable), "deno_bin"))

# QML build excludes unneeded GUI toolkits at startup (~13 MB saved).
# Keep only networking/browser deps needed for download.
for package_name in ("curl_cffi", "yt_dlp_ejs", "webview"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports
# yt_dlp is now lazy in vrka_downloader (import inside _ensure_yt_dlp) — ensure PyInstaller still bundles it.
hiddenimports += ["yt_dlp", "yt_dlp.extractor", "yt_dlp.version"]

# PySide6 / Qt is handled by PyInstaller's standard hook (rth_pyside6) via
# import tracing from vrka_qml_app -> vrka_qml.app (QtCore/QtGui/QtQml/QtWidgets).
# Using collect_all("PySide6") would bundle 80+ unused Qt modules (Qt3D,
# Charts, Multimedia, WebEngine, etc.) and double the EXE size. Let the hook
# collect only the modules actually imported.

a = Analysis(
    [str(project_dir / "vrka_qml_app.py")],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Large Qt modules not used by VRKA (saves ~80 MB). Import tracing
        # already limits to QtCore/Gui/Qml/Quick/QuickControls2/Widgets/Network;
        # these excludes prevent the PySide6 hook from pulling the rest.
        "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
        "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtDesigner", "PySide6.QtGraphs", "PySide6.QtLocation",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaQuick", "PySide6.QtMultimediaWidgets",
        "PySide6.QtPdf", "PySide6.QtPositioning", "PySide6.QtSensors",
        "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
        "PySide6.QtTextToSpeech", "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets",
        "PySide6.QtWebView", "PySide6.QtNfc", "PySide6.QtHelp",
        "PySide6.QtSql", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
        # Legacy Tk/PIL no longer needed for QML startup (saves ~13 MB + tcl/tk DLLs)
        "tkinter", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageTk",
    ],
    noarchive=False,
    optimize=0,
)

# --- Phase 1: prune unused Qt/QML plugin trees (verified against actual QML: QtQuick, QtQuick.Controls, QtQuick.Layouts, QtQuick.Dialogs, QtQml only) ---
_unused_qml_substrings = (
    "qt3d", "qtcharts", "qtdatavisualization", "qtgraphs",
    "qtlocation", "qtmultimedia", "qtpdf", "qtpositioning",
    "qtsensors", "qtserial", "qtspatialaudio", "qttexttospeech",
    "qtwebchannel", "qtwebengine", "qtwebview", "qtquick3d",
    "qtvirtualkeyboard", "qtbluetooth", "qtnfc", "wavefrontmesh",
    "qml\\qt\\labs\\assetdownloader", "qml\\qtquick\\controls\\fluentwinui3",
    "qml\\qtquick\\controls\\material", "qml\\qtquick\\controls\\universal",
    "qml\\qtquick\\controls\\fusion", "qml\\qtquick\\controls\\imagine",
    "qml\\qtquick\\controls\\ios", "qml\\qtquick\\controls\\macos",
    "qml\\qtquick\\controls\\windows", "translations\\qtbase_",
    "translations\\qtdeclarative_",
)
_unused_bin_substrings = (
    "webengine", "qt6webengine", "qt3d", "qtcharts", "qtpdf", "qt6pdf",
    "qtmultimedia", "qt6multimedia", "qtlocation", "qtpositioning",
    "qtsensors", "qtserialport", "qtspatialaudio", "qttexttospeech",
    "quick3d", "qt6quick3d", "shadertools", "qt6shadertools",
    "opengl32sw", "virtualkeyboard", "bluetooth", "nfc",
)

def _is_unneeded_data(d):
    target = (str(d[0]) + " " + str(d[1])).lower()
    return any(s in target for s in _unused_qml_substrings)

def _is_unneeded_bin(b):
    target = (str(b[0]) + " " + str(b[1])).lower()
    return any(s in target for s in _unused_bin_substrings)

_pruned_datas = [d for d in a.datas if _is_unneeded_data(d)]
_pruned_binaries = [b for b in a.binaries if _is_unneeded_bin(b)]
if _pruned_datas or _pruned_binaries:
    print(f"[VRKA spec] Pruning {len(_pruned_datas)} QML/datas and {len(_pruned_binaries)} binaries...")
    for b in _pruned_binaries[:10]:
        print(f"   Pruned binary: {b[0]}")

a.datas = [d for d in a.datas if not _is_unneeded_data(d)]
a.binaries = [b for b in a.binaries if not _is_unneeded_bin(b)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VRKA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(project_dir / "assets" / "branding" / "vrka.ico")],
    version=str(project_dir / "version_info.txt"),
)
