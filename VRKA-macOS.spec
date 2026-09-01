# -*- mode: python ; coding: utf-8 -*-
"""Apple Silicon PyInstaller recipe for the unsigned VRKA app bundle."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all


project_dir = Path(SPECPATH)
datas = []
datas.append((str(project_dir / "assets" / "branding"), "assets/branding"))
datas.append((str(project_dir / "assets" / "fonts"), "assets/fonts"))
datas.append((str(project_dir / "assets" / "browser_protection"), "assets/browser_protection"))
datas.append((str(project_dir / "THIRD_PARTY_NOTICES.md"), "."))
binaries = [
    (str(project_dir / "ffmpeg_bin" / "ffmpeg"), "ffmpeg_bin"),
    (str(project_dir / "ffmpeg_bin" / "ffprobe"), "ffmpeg_bin"),
]
hiddenimports = []

deno_executable = project_dir / "deno_bin" / "deno"
if deno_executable.is_file():
    binaries.append((str(deno_executable), "deno_bin"))

for package_name in ("customtkinter", "curl_cffi", "PIL", "yt_dlp_ejs", "webview"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    [str(project_dir / "vrka_downloader.py")],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VRKA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VRKA",
)

app = BUNDLE(
    collection,
    name="VRKA.app",
    icon=str(project_dir / "assets" / "branding" / "vrka.icns"),
    bundle_identifier="app.vrka.downloader",
    version="3.0.0",
    info_plist={
        "CFBundleDisplayName": "VRKA",
        "CFBundleShortVersionString": "3.0.0",
        "CFBundleVersion": "011",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    },
)
