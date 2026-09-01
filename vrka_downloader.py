"""
VRKA - Video Downloader
================================
VRKA is a focused desktop media downloader built with yt-dlp and
CustomTkinter. It supports a download queue, persistent history, playlists,
cookies, subtitles, SponsorBlock, and advanced/custom yt-dlp commands.

Author: MVRK
Copyright © 2026 MVRK

Requirements (install with pip):
    pip install customtkinter yt-dlp curl_cffi

Also requires FFmpeg to be installed and available on your system PATH.

Run with:
    python vrka_downloader.py
"""

import sys
import os
import re
import json
import math
import hashlib
import time
import uuid
import shlex
import shutil
import queue
import platform
import traceback
import threading
import tempfile
import subprocess
import ctypes
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PureWindowsPath
from dataclasses import dataclass

# Tk/CustomTkinter are legacy presentation only — QML must not import them at startup.
# Detect QML import chain or frozen/CLI helper mode via sys.modules / argv.
_IS_QML_STARTUP = (
    any(k.startswith("vrka_qml") for k in sys.modules)
    or "vrka_qml" in str(sys.argv[0] if sys.argv else "")
    or getattr(sys, "frozen", False)
    or (len(sys.argv) > 1 and sys.argv[1].startswith("__"))
)
if _IS_QML_STARTUP:
    tk = None  # type: ignore
    _filedialog = None  # type: ignore
    _messagebox = None  # type: ignore
    _TK_AVAILABLE = False
    filedialog = None  # type: ignore
    messagebox = None  # type: ignore
else:
    try:
        import tkinter as tk  # noqa: F401
        from tkinter import filedialog as _filedialog  # noqa: F401
        from tkinter import messagebox as _messagebox  # noqa: F401
        _TK_AVAILABLE = True
    except ImportError:
        tk = None  # type: ignore
        _filedialog = None  # type: ignore
        _messagebox = None  # type: ignore
        _TK_AVAILABLE = False
    filedialog = _filedialog  # type: ignore
    messagebox = _messagebox  # type: ignore

from vrka_core import (
    AutomaticFallbackExecutor,
    assemble_browser_capture,
    is_master_manifest,
    Build008TaskAdapter,
    BrowserContextCancelled,
    BrowserFallbackError,
    DirectPathEligibleForFallback,
    ExternalReplayRejected,
    MonitoredProcessRunner,
    ProcessCancelled,
    ProtectedBrowserFallback,
    SubprocessBrowserLauncher,
    TaskCancelled,
)

APP_NAME = "VRKA"
APP_VERSION = "3.0.0"
APP_BUILD = "011"
APP_DISPLAY_VERSION = "3.0.0"
APP_AUTHOR = "MVRK"
APP_COPYRIGHT = "Copyright © 2026 MVRK"
MAX_LOG_LINES = 1000
MAX_HISTORY_ENTRIES = 1000
MAX_HISTORY_FILE_BYTES = 5_000_000
HISTORY_PAGE_SIZE = 50
DEFAULT_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"
MAX_OUTPUT_TEMPLATE_CHARS = 240
MAX_FILENAME_CHARS = 180
MAX_URL_CHARS = 8192
DEFAULT_SUBTITLE_LANGUAGE_PATTERN = "en.*"
LEGACY_SUBTITLE_LANGUAGE_DEFAULT = "en"
DEFAULT_YTDLP_CHANNEL = "Stable"
YTDLP_CHANNELS = ("Stable", "Nightly")
YTDLP_STARTUP_CHECK_SECONDS = 24 * 60 * 60
VRKA_ARCHIVE_FILENAME = "vrka_download_archive.txt"
LEGACY_ARCHIVE_FILENAMES = (
    "seal_archive.txt",
    "seal_download_archive.txt",
    "seal_downloads_archive.txt",
)

UI_QUEUE_INTERVAL_MS = 100
UI_QUEUE_BUSY_INTERVAL_MS = 20
UI_QUEUE_BATCH_LIMIT = 250
PROGRESS_EMIT_INTERVAL_SECONDS = 0.15
PROGRESS_LOG_INTERVAL_SECONDS = 1.0
HISTORY_SEARCH_DEBOUNCE_MS = 180
THEME_TOGGLE_MIDPOINT_MS = 45
THEME_TOGGLE_DURATION_MS = 110

# A before_dl/"transfer start" marker alone is NOT proof of a working
# transfer.  After transfer start the handoff waits (bounded) for sustained
# transfer activity - real staging-byte growth, yt-dlp percentage progress,
# or ffmpeg ``time=`` progression - before the protected browser is allowed
# to close.  A candidate that cannot demonstrate sustained activity within
# this grace window is terminated and the next stabilized candidate is tried
# on the SAME task (browser stays open).
# After uBOL's AddBrowserExtensionAsync completes, its service worker still
# needs a bounded window to register the declarative-net-request rulesets
# (measured: 3 s is not enough, ~10 s is); the requested page is not
# navigated to until that settle elapses so its first document runs under
# registered block rules (fail-open: if the extension is unavailable the
# settle is skipped entirely).
UBOL_DNR_WARMUP_SECONDS = 10.0

TRANSFER_FLOW_GRACE_SECONDS = 30.0

_FFMPEG_TIME_RE = re.compile(r"time=\d+:\d{2}")

def _staging_bytes(path):
    """Total bytes currently on disk under a staging directory (0 when absent)."""
    if not path:
        return 0
    total = 0
    try:
        for entry in Path(path).rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except OSError:
        pass
    return total


APP_DATA_DIR = Path.home() / ".vrka"
LEGACY_APP_DATA_DIR = Path.home() / ".seal_desktop"
HISTORY_FILE = APP_DATA_DIR / "history.json"
SETTINGS_FILE = APP_DATA_DIR / "settings.json"

LOCAL_APP_DATA = Path(
    os.environ.get("LOCALAPPDATA")
    or (Path.home() / "AppData" / "Local" if os.name == "nt" else APP_DATA_DIR)
)
RUNTIME_DIR = LOCAL_APP_DATA / "VRKA" / "runtime"
RUNTIME_STATE_FILE = RUNTIME_DIR / "runtime.json"
BROWSER_SESSION_DIR = LOCAL_APP_DATA / "VRKA" / "browser-session"
STAGING_DIR = LOCAL_APP_DATA / "VRKA" / "staging"


def _write_crash_log(message):
    """Best-effort crash logger. A --windowed frozen .exe has no visible
    console, so without this, any startup failure just makes the app vanish
    with zero feedback. This ensures there's always a file to check."""
    try:
        log_dir = APP_DATA_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "crash_log.txt", "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{message}\n")
    except Exception:
        pass


def migrate_legacy_app_data():
    """Copy compatible Seal Desktop data into VRKA once, without deleting or
    overwriting anything. The legacy directory remains a complete rollback
    copy and an existing VRKA file always wins."""
    migrated = []
    try:
        if not LEGACY_APP_DATA_DIR.is_dir():
            return migrated
        for filename, destination in (
            ("settings.json", SETTINGS_FILE),
            ("history.json", HISTORY_FILE),
        ):
            source = LEGACY_APP_DATA_DIR / filename
            if source.is_file() and not destination.exists():
                APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                migrated.append(filename)
    except Exception:
        _write_crash_log("Legacy data migration failed:\n" + traceback.format_exc())
    return migrated


def _atomic_write_json(path, value):
    """Write JSON through a temporary sibling so an interrupted save cannot
    leave settings or history half-written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as file_handle:
            json.dump(value, file_handle, indent=2)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


if _IS_QML_STARTUP:
    ctk = None  # type: ignore
    _CTK_AVAILABLE = False
else:
    try:
        import customtkinter as ctk  # type: ignore
        _CTK_AVAILABLE = True
    except ImportError:
        ctk = None  # type: ignore
        _CTK_AVAILABLE = False

if not _IS_QML_STARTUP:
    try:
        # CustomTkinter's built-in DPI-scaling tracker polls Windows' DPI APIs via
        # ctypes every 100ms for the ENTIRE lifetime of the app (to catch the rare
        # case of dragging the window to a different-DPI monitor) - this is the
        # actual, measured source of idle CPU usage, not this app's own logic.
        # Slowing that check to every 1.5s still catches a DPI change within a
        # second or two (imperceptible for something this rare) while cutting
        # that ongoing overhead roughly 15x. This is deliberately NOT the same as
        # customtkinter.deactivate_automatic_dpi_awareness() - that documented
        # option turns DPI scaling off entirely, which makes the UI blurry on any
        # display running above 100% scaling. This keeps full crispness.
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
        ScalingTracker.update_loop_interval = 1500
        from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import AppearanceModeTracker
        AppearanceModeTracker.update_loop_interval = 1500
    except Exception:
        pass  # if this internal path ever changes in a future ctk version, just
              # keep the (safe, if slightly more CPU-hungry) default behavior


def _patch_scrollbar_redundant_redraw(scrollbar_class):
    """Replace CTkScrollbar.set with a redraw-skipping version.

    CTkScrollbar.set() unconditionally redraws its canvas and pumps
    update_idletasks on every call, even when the fractional slider
    position did not change.  During window resize the scrollable frames
    re-emit identical fractions on every layout pass, so each resize
    event produced a full scrollbar redraw storm (measured with
    cProfile: ~34% of total resize CPU).  Skipping the redraw when
    (start, end, widget size) are unchanged is visually identical.
    """

    def _scrollbar_set_skip_redundant_redraw(self, start_value, end_value):
        start_value = float(start_value)
        end_value = float(end_value)
        signature = (round(start_value, 9), round(end_value, 9),
                     self._current_width, self._current_height)
        self._start_value = start_value
        self._end_value = end_value
        if signature != getattr(self, "_vrka_last_set_signature", None):
            self._vrka_last_set_signature = signature
            self._draw()

    scrollbar_class.set = _scrollbar_set_skip_redundant_redraw


if not _IS_QML_STARTUP:
    try:
        # See _patch_scrollbar_redundant_redraw: applied to the real
        # CustomTkinter scrollbar class when it is importable.
        from customtkinter.windows.widgets.ctk_scrollbar import CTkScrollbar
        _patch_scrollbar_redundant_redraw(CTkScrollbar)
    except Exception:
        pass  # if this internal path ever changes in a future ctk version, just
              # keep the (correct, if more CPU-hungry) default behavior


def _patch_canvas_pump_coalescing(canvas_class):
    """Coalesce the per-draw update_idletasks pumps of CTk canvases.

    CustomTkinter ends nearly every widget _draw with
    canvas.update_idletasks().  During a window resize dozens of widgets
    redraw, so the full pending geometry queue was force-drained dozens
    of times per resize step (measured: 72 forced pumps per burst,
    ~10% of total resize CPU) where Tk's own idle processing would
    drain it exactly once at the natural point.  Deferring the pump to
    after_idle keeps rendering correct (idle tasks run before every
    repaint) while collapsing the redundant mid-burst drains.
    """
    original_pump = canvas_class.update_idletasks
    if getattr(original_pump, "_vrka_coalesced", False):
        return
    pending = {"scheduled": False}

    def _coalesced_update_idletasks(self):
        if pending["scheduled"]:
            return
        pending["scheduled"] = True

        def _drain():
            pending["scheduled"] = False
            try:
                original_pump(self)
            except Exception:
                pass  # a destroyed canvas must never break the event loop

        try:
            self.after_idle(_drain)
        except Exception:
            pending["scheduled"] = False

    _coalesced_update_idletasks._vrka_coalesced = True
    canvas_class.update_idletasks = _coalesced_update_idletasks


if not _IS_QML_STARTUP:
    try:
        from customtkinter.windows.widgets.core_rendering.ctk_canvas import CTkCanvas
        _patch_canvas_pump_coalescing(CTkCanvas)
    except Exception:
        pass  # if this internal path ever changes in a future ctk version, just
              # keep the (correct, if more CPU-hungry) default behavior
else:
    # QML path keeps patch functions but never applies them
    pass

if _IS_QML_STARTUP:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    _HAS_IMAGETK = False
    yt_dlp = None  # type: ignore
else:
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError:
        Image = None  # type: ignore
        ImageDraw = None  # type: ignore

    try:
        from PIL import ImageTk  # type: ignore
        _HAS_IMAGETK = True
    except Exception:
        _HAS_IMAGETK = False
        _write_crash_log("PIL.ImageTk unavailable - window icon will be skipped, app will "
                          "otherwise run normally.\n" + traceback.format_exc())

    try:
        import yt_dlp  # type: ignore
    except ImportError:
        _write_crash_log("Missing dependency: yt-dlp.\n" + traceback.format_exc())
        print("ERROR: The 'yt-dlp' package is not installed.")
        print("Run this command first:  pip install yt-dlp")
        sys.exit(1)

# Lazy helpers for QML — import only when media operation actually starts
def _ensure_yt_dlp():
    global yt_dlp
    if yt_dlp is not None:
        return yt_dlp
    import yt_dlp as _yt  # type: ignore
    yt_dlp = _yt
    return yt_dlp

def _ensure_pil():
    global Image, ImageDraw
    if Image is not None:
        return Image, ImageDraw
    from PIL import Image as _Image, ImageDraw as _Draw  # type: ignore
    Image, ImageDraw = _Image, _Draw  # type: ignore
    return Image, ImageDraw


# ----------------------------------------------------------------------
# Static lookup tables / paths
# ----------------------------------------------------------------------

QUALITY_MAP = {
    "Best Available": None,
    "2160p (4K)": 2160,
    "1440p (2K)": 1440,
    "1080p (Full HD)": 1080,
    "720p (HD)": 720,
    "480p (SD)": 480,
    "360p": 360,
}

AUDIO_FORMAT_MAP = {
    "MP3 (Compressed)": "mp3",
    "WAV (Uncompressed)": "wav",
    "FLAC (Lossless container)": "flac",
}

MP3_BITRATE_MAP = {
    "320 kbps": "320K",
    "256 kbps": "256K",
    "192 kbps": "192K",
    "128 kbps": "128K",
}

AUDIO_FORMAT_DESCRIPTIONS = {
    "MP3 (Compressed)": "Compressed audio with selectable bitrate.",
    "WAV (Uncompressed)": "Uncompressed output with very large file sizes.",
    "FLAC (Lossless container)": (
        "Lossless container. Converting a lossy web stream to FLAC does not restore "
        "lost quality and may only increase file size."
    ),
}

COOKIE_MODE_MAP = {
    "Disabled": "none",
    "Selected Browser": "browser",
    "cookies.txt File": "file",
    "Verified Session": "session",
}

# -- Visual theme --------------------------------------------------------
# Near-black surfaces, one restrained accent, subtle borders instead of
# shadows for depth, luminance (not boldness) for hierarchy - the language
# shared by Linear/Vercel/Raycast-style dark tools.

COLOR_BG = ("#FFFFFF", "#000000")
COLOR_SIDEBAR = ("#F7F7F8", "#050505")
COLOR_CARD = ("#F7F7F8", "#090909")
COLOR_CARD_ALT = ("#EFEFF1", "#111111")
COLOR_SURFACE_ELEVATED = ("#E8E8EB", "#161616")
COLOR_SURFACE_HOVER = ("#DEDEE3", "#202020")
COLOR_BORDER = ("#DEDEE3", "#242424")
COLOR_BORDER_STRONG = ("#C8C8CF", "#363636")
COLOR_ACCENT = "#8140DC"
COLOR_ACCENT_HOVER = "#9255E5"
COLOR_ACCENT_PRESSED = "#6E31C3"
COLOR_ACCENT_SOFT = ("#F1E8FC", "#180D24")
COLOR_ACCENT_SOFT_HOVER = ("#E8D9FA", "#241236")
COLOR_FOCUS = "#B98AF2"
COLOR_TEXT = ("#141216", "#FAF9FC")
COLOR_TEXT_MUTED = ("#4E4B54", "#C8C4CF")
COLOR_TEXT_DIM = ("#6B6871", "#98939F")
COLOR_TEXT_DISABLED = ("#85818B", "#817C88")
COLOR_TEXT_ON_ACCENT = "#FFFFFF"
COLOR_SUCCESS = "#2BCB77"
COLOR_WARNING = "#E7A93D"
COLOR_ERROR = "#EF5A67"

SIDEBAR_WIDTH = 240
PAGE_PAD_X = 32
PAGE_PAD_Y = 22
CARD_PAD_X = 18
CARD_RADIUS = 12
CONTROL_RADIUS = 8
CONTROL_HEIGHT = 40
PRIMARY_BUTTON_HEIGHT = 46
NAV_BUTTON_HEIGHT = 44
FONT_PAGE_TITLE = 26
FONT_SECTION_TITLE = 16
FONT_BODY = 14
FONT_SMALL = 12
FONT_MICRO = 11

UI_FONT_FAMILY = "Space Mono"
UI_FONT_FALLBACK = (
    "SF Pro Text" if platform.system() == "Darwin"
    else "Segoe UI" if os.name == "nt"
    else "TkDefaultFont"
)
MONO_FONT_FALLBACK = "Menlo" if platform.system() == "Darwin" else "Consolas"
PRODUCTION_FONT_FILES = ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf")
_ACTIVE_UI_FONT_FAMILY = UI_FONT_FALLBACK
_ACTIVE_MONO_FONT_FAMILY = MONO_FONT_FALLBACK
_FONT_REGISTRATION_REPORT = {
    "family": UI_FONT_FAMILY,
    "loaded": False,
    "files": [],
    "fallback": UI_FONT_FALLBACK,
    "scope": "process-private",
}

# -- Hand-drawn icon set ---------------------------------------------------
# No network access is assumed, so icons are drawn with PIL primitives at
# high resolution and downsampled for crisp anti-aliased edges, rather than
# depending on a downloaded icon font. Zero extra files, zero extra installs.

_ICON_GRID = 128
_ICON_CACHE = {}
_BRAND_IMAGE_CACHE = {}


def _pt(fx, fy, g=_ICON_GRID):
    return (fx * g, fy * g)


def _draw_download(d, g, s, c):
    d.line([_pt(.5, .14, g), _pt(.5, .61, g)], fill=c, width=s)
    d.line(
        [_pt(.29, .43, g), _pt(.5, .64, g), _pt(.71, .43, g)],
        fill=c, width=s, joint="curve",
    )
    d.line(
        [_pt(.18, .68, g), _pt(.18, .84, g), _pt(.82, .84, g), _pt(.82, .68, g)],
        fill=c, width=s, joint="curve",
    )


def _draw_list(d, g, s, c):
    for y in (.25, .5, .75):
        cx, cy = _pt(.19, y, g)
        radius = s * .52
        d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=c)
        d.line([_pt(.34, y, g), _pt(.84, y, g)], fill=c, width=s)


def _draw_clock(d, g, s, c):
    d.ellipse([_pt(.14, .14, g), _pt(.86, .86, g)], outline=c, width=s)
    d.line(
        [_pt(.5, .28, g), _pt(.5, .51, g), _pt(.67, .61, g)],
        fill=c, width=s, joint="curve",
    )


def _draw_gear(d, g, s, c):
    points = []
    for index in range(32):
        angle = -math.pi / 2 + index * math.pi / 16
        radius = .43 if index % 4 in (1, 2) else .34
        points.append(
            _pt(
                .5 + radius * math.cos(angle),
                .5 + radius * math.sin(angle),
                g,
            )
        )
    d.line(points + [points[0]], fill=c, width=s, joint="curve")
    d.ellipse([_pt(.37, .37, g), _pt(.63, .63, g)], outline=c, width=s)


def _draw_link(d, g, s, c):
    d.rounded_rectangle([_pt(.12, .34, g), _pt(.58, .66, g)], radius=g * 0.14, outline=c, width=s)
    d.rounded_rectangle([_pt(.42, .34, g), _pt(.88, .66, g)], radius=g * 0.14, outline=c, width=s)


def _draw_sliders(d, g, s, c):
    for x, hy in ((.28, .32), (.5, .62), (.72, .42)):
        d.line([_pt(x, .12, g), _pt(x, .88, g)], fill=c, width=max(2, s - 3))
        cx, cy = _pt(x, hy, g)
        d.ellipse([cx - s * 0.85, cy - s * 0.85, cx + s * 0.85, cy + s * 0.85], fill=c)


def _draw_captions(d, g, s, c):
    d.rounded_rectangle([_pt(.13, .22, g), _pt(.87, .68, g)], radius=g * 0.10, outline=c, width=s)
    d.line([_pt(.26, .38, g), _pt(.74, .38, g)], fill=c, width=max(2, s - 3))
    d.line([_pt(.26, .52, g), _pt(.58, .52, g)], fill=c, width=max(2, s - 3))
    d.polygon([_pt(.30, .68, g), _pt(.30, .84, g), _pt(.46, .68, g)], fill=c)


def _draw_lock(d, g, s, c):
    d.arc([_pt(.25, .10, g), _pt(.75, .60, g)], start=180, end=360, fill=c, width=s)
    d.rounded_rectangle([_pt(.20, .45, g), _pt(.80, .88, g)], radius=g * 0.08, outline=c, width=s)
    d.ellipse([_pt(.465, .61, g), _pt(.535, .68, g)], fill=c)


def _draw_music(d, g, s, c):
    d.ellipse([_pt(.20, .62, g), _pt(.42, .84, g)], outline=c, width=s)
    d.line([_pt(.42, .73, g), _pt(.42, .16, g)], fill=c, width=s)
    d.line([_pt(.42, .16, g), _pt(.74, .30, g)], fill=c, width=s)


def _draw_block(d, g, s, c):
    d.ellipse([_pt(.13, .13, g), _pt(.87, .87, g)], outline=c, width=s)
    d.line([_pt(.24, .24, g), _pt(.76, .76, g)], fill=c, width=s)


def _draw_globe(d, g, s, c):
    thin = max(2, s - 3)
    d.ellipse([_pt(.13, .13, g), _pt(.87, .87, g)], outline=c, width=s)
    d.line([_pt(.5, .13, g), _pt(.5, .87, g)], fill=c, width=thin)
    d.line([_pt(.13, .5, g), _pt(.87, .5, g)], fill=c, width=thin)


def _draw_folder(d, g, s, c):
    d.line([_pt(.13, .30, g), _pt(.32, .30, g), _pt(.40, .20, g), _pt(.62, .20, g), _pt(.62, .30, g)],
           fill=c, width=s, joint="curve")
    d.rounded_rectangle([_pt(.13, .30, g), _pt(.87, .80, g)], radius=g * 0.06, outline=c, width=s)


def _draw_terminal(d, g, s, c):
    d.rounded_rectangle([_pt(.12, .18, g), _pt(.88, .82, g)], radius=g * 0.10, outline=c, width=s)
    d.line([_pt(.28, .38, g), _pt(.42, .5, g), _pt(.28, .62, g)], fill=c, width=max(2, s - 2), joint="curve")
    d.line([_pt(.50, .62, g), _pt(.68, .62, g)], fill=c, width=max(2, s - 2))


def _draw_check(d, g, s, c):
    d.line([_pt(.20, .52, g), _pt(.42, .74, g), _pt(.82, .26, g)], fill=c, width=s, joint="curve")


def _draw_inbox(d, g, s, c):
    d.line([_pt(.16, .28, g), _pt(.16, .78, g), _pt(.84, .78, g), _pt(.84, .28, g)],
           fill=c, width=s, joint="curve")
    d.line([_pt(.16, .28, g), _pt(.36, .28, g), _pt(.42, .46, g), _pt(.58, .46, g), _pt(.64, .28, g),
            _pt(.84, .28, g)], fill=c, width=s, joint="curve")


def _draw_play(d, g, s, c):
    d.rounded_rectangle([_pt(.13, .13, g), _pt(.87, .87, g)], radius=g * 0.16, outline=c, width=s)
    d.polygon([_pt(.40, .32, g), _pt(.40, .68, g), _pt(.68, .5, g)], fill=c)


def _draw_sun(d, g, s, c):
    ray = max(2, s - 4)
    d.ellipse([_pt(.34, .34, g), _pt(.66, .66, g)], outline=c, width=s)
    for angle in range(0, 360, 45):
        a = math.radians(angle)
        d.line(
            [
                _pt(.5 + .25 * math.cos(a), .5 + .25 * math.sin(a), g),
                _pt(.5 + .40 * math.cos(a), .5 + .40 * math.sin(a), g),
            ],
            fill=c,
            width=ray,
        )


def _draw_moon(d, g, s, c):
    d.ellipse([_pt(.18, .14, g), _pt(.82, .86, g)], fill=c)
    d.ellipse([_pt(.40, .07, g), _pt(.91, .68, g)], fill=(0, 0, 0, 0))


_ICON_DRAWERS = {
    "download": _draw_download, "list": _draw_list, "clock": _draw_clock, "gear": _draw_gear,
    "link": _draw_link, "sliders": _draw_sliders, "captions": _draw_captions, "lock": _draw_lock,
    "music": _draw_music, "block": _draw_block, "globe": _draw_globe, "folder": _draw_folder,
    "terminal": _draw_terminal, "check": _draw_check, "inbox": _draw_inbox, "play": _draw_play,
    "sun": _draw_sun, "moon": _draw_moon,
}


def render_icon_image(name, size, color, stroke=11):
    """Renders one of the hand-drawn icons as a PIL RGBA image at `size`px."""
    img = Image.new("RGBA", (_ICON_GRID, _ICON_GRID), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    drawer = _ICON_DRAWERS.get(name)
    if drawer:
        drawer(draw, _ICON_GRID, stroke, color)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def get_icon(name, size=18, color=COLOR_TEXT):
    """Returns a cached CTkImage for the named icon at the given size/color.
    Returns None on any failure (e.g. an environment where image rendering
    is broken) so callers degrade to text-only rather than crashing - a
    missing icon is a cosmetic issue, not a reason for the app not to open."""
    color_key = tuple(color) if isinstance(color, (tuple, list)) else color
    key = (name, size, color_key)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        light_color, dark_color = color if isinstance(color, (tuple, list)) else (color, color)
        light_image = render_icon_image(name, size, light_color)
        dark_image = render_icon_image(name, size, dark_color)
        ctk_img = ctk.CTkImage(
            light_image=light_image, dark_image=dark_image, size=(size, size)
        )
    except Exception:
        _write_crash_log(f"get_icon('{name}') failed:\n{traceback.format_exc()}")
        return None
    _ICON_CACHE[key] = ctk_img
    return ctk_img


def _kind_icon_and_color(mode):
    """Icon name + accent color shown on queue/history rows, by download kind."""
    if mode == "audio":
        return "music", COLOR_SUCCESS
    if mode == "custom":
        return "terminal", COLOR_WARNING
    return "play", COLOR_ACCENT


def ui_font(size=FONT_BODY, weight="normal"):
    """Primary interface font with a native fallback if resource loading fails."""
    return ctk.CTkFont(family=_ACTIVE_UI_FONT_FAMILY, size=size, weight=weight)


def mono_font(size=FONT_SMALL, weight="normal"):
    """Technical/log font. Space Mono is used when the bundled family loaded."""
    return ctk.CTkFont(family=_ACTIVE_MONO_FONT_FAMILY, size=size, weight=weight)


def appearance_color(color, mode=None):
    if not isinstance(color, (tuple, list)):
        return color
    selected_mode = mode or ctk.get_appearance_mode()
    return color[1] if str(selected_mode).lower() == "dark" else color[0]


def _parent_color_token(parent):
    """Resolve the semantic surface token behind a layout-only Tk frame."""
    current = parent
    while current is not None:
        token = getattr(current, "_vrka_bg_token", None)
        if token is not None:
            return token
        try:
            color = current.cget("fg_color")
        except Exception:
            try:
                return current.cget("bg")
            except Exception:
                current = getattr(current, "master", None)
                continue
        if color != "transparent":
            return color
        current = getattr(current, "master", None)
    return COLOR_BG


def layout_frame(parent, bg_color=None, **kwargs):
    """A canvas-free frame for geometry only.

    CustomTkinter frames are retained where they provide a visible surface.
    Plain Tk frames handle invisible layout grouping, avoiding dozens of
    CTkCanvas redraws during native window resizing.
    """
    token = bg_color or _parent_color_token(parent)
    frame = tk.Frame(
        parent,
        bg=appearance_color(token),
        bd=0,
        highlightthickness=0,
        **kwargs,
    )
    frame._vrka_bg_token = token
    return frame


if not _IS_QML_STARTUP and ctk is not None:
    class EfficientCTkTextbox(ctk.CTkTextbox):  # type: ignore
        """CTkTextbox with a slow, visibility-aware scrollbar check.

        CustomTkinter 6 polls every textbox every 200ms forever, even while its
        page is hidden. VRKA only needs a one-second check and skips layout work
        entirely while the textbox is unmapped.
        """

        _scrollbar_update_time = 1000

        def _check_if_scrollbars_needed(self, event=None, continue_loop=False):
            if self.winfo_ismapped():
                super()._check_if_scrollbars_needed(event, continue_loop=False)
            if self._textbox.winfo_exists() and continue_loop is True:
                self.after(
                    self._scrollbar_update_time,
                    lambda: self._check_if_scrollbars_needed(continue_loop=True),
                )
else:
    EfficientCTkTextbox = None  # type: ignore


def get_resource_base():
    """Return the project directory or PyInstaller extraction directory."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parent


def resource_path(relative_path):
    return get_resource_base() / Path(relative_path)


def _register_bundled_fonts():
    """Register VRKA's two production fonts for this process only.

    Windows uses FR_PRIVATE, so nothing is installed into the user's Fonts
    folder or left behind after VRKA exits. macOS uses CoreText's process
    scope. Any failure is non-fatal and selects the documented native
    fallback chain.
    """
    global _ACTIVE_UI_FONT_FAMILY, _ACTIVE_MONO_FONT_FAMILY

    font_paths = [
        resource_path(Path("assets") / "fonts" / filename)
        for filename in PRODUCTION_FONT_FILES
    ]
    report = {
        "family": UI_FONT_FAMILY,
        "loaded": False,
        "files": [str(path) for path in font_paths],
        "fallback": UI_FONT_FALLBACK,
        "scope": "process-private",
    }
    if not all(path.is_file() for path in font_paths):
        report["error"] = "One or more production font files are missing."
        _FONT_REGISTRATION_REPORT.update(report)
        return False

    try:
        if os.name == "nt":
            add_font = ctypes.windll.gdi32.AddFontResourceExW
            add_font.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
            add_font.restype = ctypes.c_int
            loaded = all(add_font(str(path), 0x10, None) > 0 for path in font_paths)
        elif platform.system() == "Darwin":
            core_foundation = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            core_text = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreText.framework/CoreText"
            )
            make_url = core_foundation.CFURLCreateFromFileSystemRepresentation
            make_url.argtypes = (
                ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool,
            )
            make_url.restype = ctypes.c_void_p
            release = core_foundation.CFRelease
            release.argtypes = (ctypes.c_void_p,)
            register_url = core_text.CTFontManagerRegisterFontsForURL
            register_url.argtypes = (
                ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
            )
            register_url.restype = ctypes.c_bool
            results = []
            for path in font_paths:
                encoded = os.fsencode(path)
                url = make_url(None, encoded, len(encoded), False)
                if not url:
                    results.append(False)
                    continue
                error = ctypes.c_void_p()
                try:
                    results.append(bool(register_url(url, 1, ctypes.byref(error))))
                finally:
                    release(url)
            loaded = all(results)
        else:
            loaded = False
            report["error"] = "Dynamic bundled-font registration is unsupported on this platform."
    except Exception as exc:
        loaded = False
        report["error"] = f"{type(exc).__name__}: {exc}"

    if loaded:
        _ACTIVE_UI_FONT_FAMILY = UI_FONT_FAMILY
        _ACTIVE_MONO_FONT_FAMILY = UI_FONT_FAMILY
    report["loaded"] = loaded
    _FONT_REGISTRATION_REPORT.update(report)
    return loaded


def configure_typography_defaults():
    """Apply the active family and readable defaults without per-widget IO."""
    try:
        theme = ctk.ThemeManager.theme
        theme_font = theme["CTkFont"]
        theme_font["family"] = _ACTIVE_UI_FONT_FAMILY
        theme_font["size"] = FONT_BODY
        theme_font["weight"] = "normal"

        readable_text = list(COLOR_TEXT)
        readable_helper = list(COLOR_TEXT_DIM)
        readable_disabled = list(COLOR_TEXT_DISABLED)
        for widget_name in (
            "CTkLabel", "CTkEntry", "CTkCheckBox", "CTkSwitch",
            "CTkRadioButton", "CTkOptionMenu", "CTkComboBox",
            "CTkTextbox", "DropdownMenu",
        ):
            widget_theme = theme.get(widget_name, {})
            if "text_color" in widget_theme:
                widget_theme["text_color"] = readable_text
            if "text_color_disabled" in widget_theme:
                widget_theme["text_color_disabled"] = readable_disabled
        for widget_name in ("CTkButton", "CTkSegmentedButton"):
            widget_theme = theme.get(widget_name, {})
            if "text_color_disabled" in widget_theme:
                widget_theme["text_color_disabled"] = readable_disabled
        theme.get("CTkEntry", {})["placeholder_text_color"] = readable_helper
    except Exception:
        pass


def get_font_registration_report():
    return dict(_FONT_REGISTRATION_REPORT)


_register_bundled_fonts()


def load_brand_image(size):
    """Load the canonical production wolf without inventing alternate faces.

    Every production size is generated from one vector master. If an exact
    runtime raster is missing, resize the canonical 1024px master; if the
    entire asset family is missing, return a transparent placeholder so the
    app remains usable without displaying an inconsistent substitute mark.
    Build 007 deliberately has no alternate compact face geometry.
    """
    key = ("pil", size)
    cached = _BRAND_IMAGE_CACHE.get(key)
    if cached is not None:
        return cached.copy()
    filename = f"vrka-wolf-{size}.png"
    path = resource_path(Path("assets") / "branding" / filename)
    try:
        with Image.open(path) as source:
            image = source.convert("RGBA")
        if image.size != (size, size):
            image = image.resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        master_path = resource_path(Path("assets") / "branding" / "vrka-wolf-1024.png")
        try:
            with Image.open(master_path) as source:
                image = source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            _write_crash_log(f"Brand size fallback used for {path}; canonical master loaded instead.")
        except Exception:
            image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            _write_crash_log(
                f"Brand assets unavailable; transparent non-fatal fallback used for {path}:\n"
                f"{traceback.format_exc()}"
            )
    _BRAND_IMAGE_CACHE[key] = image
    return image.copy()


def get_brand_ctk_image(size=32):
    key = ("ctk", size)
    cached = _BRAND_IMAGE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        image = load_brand_image(size)
        cached = ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))
        _BRAND_IMAGE_CACHE[key] = cached
        return cached
    except Exception:
        _write_crash_log(f"CTk brand image failed (non-fatal):\n{traceback.format_exc()}")
        return None


def build_app_icon_image(size=256):
    """Return the production wolf emblem for window/taskbar icon use."""
    return load_brand_image(size)

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------

class DownloadCanceled(Exception):
    """Raised internally to unwind a yt-dlp download when the user cancels."""
    pass


def parse_time_to_seconds(time_str):
    """Convert 'HH:MM:SS', 'MM:SS', or plain seconds into a float number
    of seconds. Returns None if the field is blank or invalid."""
    time_str = (time_str or "").strip()
    if not time_str:
        return None
    parts = time_str.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def build_video_format(height, prefer_60fps):
    """Build a yt-dlp format-selector string for the chosen quality/fps.

    Four tiers, in order of preference:
      1. Requested height at 60fps (best case, if the source has it)
      2. Requested height at whatever framerate actually exists (the
         common case - most YouTube videos are only encoded at 30fps,
         even at 4K, and that's still far better than falling back to a
         much lower resolution just because 60fps wasn't available)
      3. Best pre-muxed single format under the height cap (last resort -
         on YouTube this never exceeds 1080p, since nothing higher ever
         ships pre-combined, so this must stay last, not second)
      4. Unfiltered best (only when NO format carries resolution metadata,
         e.g. protected HLS streams list resolution "unknown"; the capped
         tiers match nothing then, and this is the only usable format)
    """
    height_filter = f"[height<={height}]" if height else ""
    tiers = []
    if prefer_60fps:
        tiers.append(f"bestvideo{height_filter}[fps>=60]+bestaudio")
    tiers.append(f"bestvideo{height_filter}+bestaudio")
    tiers.append(f"best{height_filter}")
    tiers.append("best")
    return "/".join(tiers)


def parse_rate_limit(text):
    """Convert '500K' / '2M' / '1G' style strings into bytes-per-second."""
    text = (text or "").strip().upper()
    if not text:
        return None
    try:
        if text.endswith("K"):
            return int(float(text[:-1]) * 1024)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1024 * 1024)
        if text.endswith("G"):
            return int(float(text[:-1]) * 1024 * 1024 * 1024)
        return int(float(text))
    except ValueError:
        return None


def validate_media_url(value):
    """Accept bounded HTTP(S) media addresses only."""
    url = str(value or "").strip()
    if not url:
        raise ValueError("Paste a media URL.")
    if len(url) > MAX_URL_CHARS:
        raise ValueError("The URL is too long.")
    if any(ord(character) < 32 for character in url):
        raise ValueError("The URL contains invalid control characters.")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("Use a complete http:// or https:// media URL.")
    return url


def validate_output_template(value):
    """Keep yt-dlp templates relative to the selected output directory."""
    template = str(value or "").strip() or DEFAULT_OUTPUT_TEMPLATE
    if len(template) > MAX_OUTPUT_TEMPLATE_CHARS:
        raise ValueError(
            f"The filename template must be {MAX_OUTPUT_TEMPLATE_CHARS} characters or fewer."
        )
    if template == "-" or any(ord(character) < 32 for character in template):
        raise ValueError("The filename template contains an unsafe value.")

    windows_path = PureWindowsPath(template)
    if (
        windows_path.is_absolute()
        or windows_path.drive
        or template.startswith(("/", "\\"))
    ):
        raise ValueError("The filename template must stay inside the selected output folder.")

    components = re.split(r"[\\/]+", template)
    if any(component in ("", ".", "..") for component in components):
        raise ValueError("The filename template contains an unsafe path component.")
    if any(
        component.rstrip(" .").upper().split(".", 1)[0]
        in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }
        for component in components
        if "%(" not in component
    ):
        raise ValueError("The filename template uses a reserved Windows device name.")
    return template


_BLOCKED_CUSTOM_OPTIONS = {
    "-o", "--output",
    "-P", "--paths",
    "--config-locations",
    "--plugin-dirs",
    "--exec",
    "--exec-before-download",
    "--external-downloader",
    "--external-downloader-args",
    "--ffmpeg-location",
    "--batch-file",
    "--load-info-json",
    "--print-to-file",
    "--write-pages",
    "--force-overwrites",
    "--no-part",
    "--no-windows-filenames",
    "--no-check-certificates",
    "--prefer-insecure",
    "--enable-file-urls",
}


def validate_custom_ytdlp_arguments(arguments):
    """Reject options that escape VRKA's process, path, and TLS controls."""
    for argument in arguments:
        text = str(argument)
        option = text.split("=", 1)[0]
        blocked = option in _BLOCKED_CUSTOM_OPTIONS
        blocked = blocked or (
            option.startswith("-o") and option != "--"
        ) or (
            option.startswith("-P") and option != "--"
        )
        if blocked:
            raise ValueError(
                f"The custom option {option!r} is not allowed. "
                "VRKA controls execution, output paths, FFmpeg, and overwrite behavior."
            )
    return list(arguments)


def _safe_remove_staging_dir(path):
    """Delete one UUID-named staging directory without leaving the staging root."""
    candidate = Path(path)
    try:
        root = STAGING_DIR.resolve(strict=False)
        if candidate.parent.resolve(strict=False) != root:
            return False
        uuid.UUID(candidate.name)
        if candidate.is_symlink():
            candidate.unlink()
        elif candidate.exists():
            shutil.rmtree(candidate)
        return True
    except (OSError, ValueError):
        return False


def open_path(path):
    """Open a file or folder with the OS-default handler."""
    try:
        if not path:
            return
        if platform.system() == "Windows":
            os.startfile(path)  # noqa
        elif platform.system() == "Darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])
    except Exception:
        pass


def get_bundled_ffmpeg_dir():
    """Return a folder containing ffmpeg.exe/ffprobe.exe if one is bundled
    next to the app (either beside the .exe, beside the .py script, or inside the PyInstaller
    .exe via --add-binary), otherwise None to fall back to the system PATH."""
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(exe_dir, "ffmpeg_bin")
        if os.path.isfile(os.path.join(candidate, exe_name)):
            return candidate
    candidate = os.path.join(get_resource_base(), "ffmpeg_bin")
    if os.path.isfile(os.path.join(candidate, exe_name)):
        return candidate
    return None


def _find_aria2c():
    """Locate an aria2c binary for the optional transport backend: a bundled
    copy next to the app first, then the system PATH.  Returns the path or
    ``None`` (backend stays dormant)."""
    exe_name = "aria2c.exe" if os.name == "nt" else "aria2c"
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        bundled = os.path.join(exe_dir, "aria2c_bin", exe_name)
        if os.path.isfile(bundled):
            return bundled
    bundled = os.path.join(get_resource_base(), "aria2c_bin", exe_name)
    if os.path.isfile(bundled):
        return bundled
    return shutil.which(exe_name)


def get_bundled_deno_dir():
    """Return the bundled Deno runtime directory when packaging included it.
    yt-dlp uses Deno for modern YouTube challenge solving."""
    executable = "deno.exe" if os.name == "nt" else "deno"
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(exe_dir, "deno_bin")
        if os.path.isfile(os.path.join(candidate, executable)):
            return candidate
    candidate = os.path.join(get_resource_base(), "deno_bin")
    if os.path.isfile(os.path.join(candidate, executable)):
        return candidate
    return None


def configure_bundled_runtime_path():
    """Make a packaged Deno visible to this process and any self-invoked
    custom-command process without changing the user's permanent PATH."""
    deno_dir = get_bundled_deno_dir()
    if not deno_dir:
        return None
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if deno_dir not in path_parts:
        os.environ["PATH"] = deno_dir + os.pathsep + os.environ.get("PATH", "")
    return deno_dir


def configure_windows_app_identity():
    """Give Windows a stable identity for taskbar grouping and Alt+Tab art."""
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VRKA.Downloader")
        return True
    except Exception:
        _write_crash_log("Windows AppUserModelID setup failed (non-fatal):\n" + traceback.format_exc())
        return False


def migrate_subtitle_language_setting(settings):
    """Migrate only the untouched legacy `en` default to the regex-aware
    `en.*` pattern. Every genuinely customized value is preserved."""
    if not isinstance(settings, dict):
        return settings, False
    migrated = dict(settings)
    if migrated.get("sub_langs") == LEGACY_SUBTITLE_LANGUAGE_DEFAULT:
        migrated["sub_langs"] = DEFAULT_SUBTITLE_LANGUAGE_PATTERN
        return migrated, True
    return migrated, False


def migrate_audio_settings(settings):
    """Preserve 1.x audio choices after the clearer 2.0 labels are introduced."""
    if not isinstance(settings, dict):
        return settings, False
    migrated = dict(settings)
    replacements = {
        "FLAC (Lossless)": "FLAC (Lossless container)",
        "WAV (Lossless)": "WAV (Uncompressed)",
    }
    previous = migrated.get("audio_format")
    if previous in replacements:
        migrated["audio_format"] = replacements[previous]
        migrated.setdefault("mp3_bitrate", "320 kbps")
        return migrated, True
    return migrated, False

def migrate_cookie_settings(settings):
    """Translate 1.x cookie labels without persisting an authenticated session."""
    if not isinstance(settings, dict):
        return settings, False
    migrated = dict(settings)
    changed = False
    old_mode = migrated.get("cookie_mode")
    mode_replacements = {
        "None": "Disabled",
        "From Browser": "Selected Browser",
        "From File": "cookies.txt File",
        "Browser": "Selected Browser",
        "File": "cookies.txt File",
    }
    if old_mode in mode_replacements:
        migrated["cookie_mode"] = mode_replacements[old_mode]
        changed = True
    elif old_mode == "Verified Session":
        migrated["cookie_mode"] = "Disabled"
        changed = True
    browser = migrated.get("cookie_browser")
    browser_names = {
        "chrome": "Chrome",
        "edge": "Edge",
        "firefox": "Firefox",
        "brave": "Brave",
    }
    if isinstance(browser, str) and browser.lower() in browser_names:
        normalized = browser_names[browser.lower()]
        if normalized != browser:
            migrated["cookie_browser"] = normalized
            changed = True
    migrated.setdefault("cookie_profile", "")
    return migrated, changed

def normalize_subtitle_message(message):
    """Avoid categorical claims when yt-dlp cannot retrieve a matching track."""
    text = str(message)
    lowered = text.lower()
    unavailable_phrases = (
        "no subtitles for the requested languages",
        "does not have subtitles",
        "doesn't have subtitles",
        "no automatic captions",
        "requested subtitles are not available",
    )
    if any(phrase in lowered for phrase in unavailable_phrases):
        return "No matching or downloadable subtitle track was retrieved for the selected language pattern."
    return text


def build_self_invocation():
    """Returns the command prefix needed to re-run THIS program as a
    subprocess, correctly in both cases:
      - Running as a plain script: sys.executable is python.exe, so we
        also need to pass the script's own path.
      - Running as a frozen PyInstaller .exe: sys.executable IS this .exe,
        so no extra path is needed (and there's no separate Python to call
        "-m" on - that's what previously caused a second app window to
        open instead of actually running yt-dlp/pip)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(__file__)]


def is_frozen():
    return getattr(sys, "frozen", False)


@dataclass(frozen=True)
class YTDLPBackend:
    """The exact yt-dlp command selected for one operation."""

    source: str
    command: tuple
    version: str
    path: str = ""


_YTDLP_UPDATE_LOCK = threading.Lock()


def _runtime_paths():
    suffix = ".exe" if os.name == "nt" else ""
    return {
        "active": RUNTIME_DIR / f"yt-dlp{suffix}",
        "previous": RUNTIME_DIR / f"yt-dlp.previous{suffix}",
        "download": RUNTIME_DIR / f".yt-dlp.download{suffix}",
    }


def _read_runtime_state():
    try:
        with open(RUNTIME_STATE_FILE, "r", encoding="utf-8") as file_handle:
            value = json.load(file_handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_runtime_state(**changes):
    state = _read_runtime_state()
    state.update(changes)
    state["schema"] = 1
    _atomic_write_json(RUNTIME_STATE_FILE, state)
    return state


def _run_hidden(command, timeout=30):
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(
        list(command), capture_output=True, text=True, timeout=timeout, **kwargs
    )


def _valid_windows_executable_header(path):
    if os.name != "nt":
        return True
    try:
        with open(path, "rb") as file_handle:
            return file_handle.read(2) == b"MZ"
    except OSError:
        return False


def validate_ytdlp_binary(path, expected_version=None):
    """Reject HTML/error downloads and prove the binary can execute."""
    candidate = Path(path)
    if not candidate.is_file() or candidate.stat().st_size < 1_000_000:
        return False, "", "The downloaded file is missing or unexpectedly small."
    if not _valid_windows_executable_header(candidate):
        return False, "", "The downloaded file is not a Windows executable."
    try:
        version_result = _run_hidden([str(candidate), "--version"], timeout=20)
        version = (version_result.stdout or "").strip().splitlines()[0]
        if version_result.returncode != 0 or not re.fullmatch(r"[0-9][0-9A-Za-z._+-]*", version):
            return False, "", "The candidate did not return a valid yt-dlp version."
        if expected_version and version != str(expected_version):
            return False, version, (
                f"The candidate reports {version}, not the expected {expected_version}."
            )
        help_result = _run_hidden([str(candidate), "--help"], timeout=20)
        if help_result.returncode != 0 or "yt-dlp" not in (help_result.stdout or "").lower():
            return False, version, "The candidate failed its command-line help check."
        return True, version, ""
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"


def _bundled_ytdlp_version():
    _ensure_yt_dlp()
    version_module = getattr(yt_dlp, "version", None)
    return str(
        getattr(version_module, "__version__", None)
        or getattr(yt_dlp, "__version__", None)
        or "bundled"
    )


def resolve_ytdlp_backend():
    _ensure_yt_dlp()
    """Prefer a validated managed binary and safely fall back to the bundle."""
    active = _runtime_paths()["active"]
    if active.is_file():
        valid, version, _reason = validate_ytdlp_binary(active)
        if valid:
            return YTDLPBackend("managed", (str(active),), version, str(active))
    return YTDLPBackend(
        "bundled",
        tuple(build_self_invocation() + ["__ytdlp_cli__"]),
        _bundled_ytdlp_version(),
        "",
    )


def active_ytdlp_summary():
    _ensure_yt_dlp()
    backend = resolve_ytdlp_backend()
    return {
        "source": backend.source,
        "version": backend.version,
        "path": backend.path,
        "command": list(backend.command),
    }


def _github_release_api(channel):
    if str(channel).lower() == "nightly":
        return "https://api.github.com/repos/yt-dlp/yt-dlp-nightly-builds/releases/latest"
    return "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"


def _urlopen(request, timeout=30):
    return urllib.request.urlopen(request, timeout=timeout)


def _read_url(url, timeout=30, max_bytes=5_000_000):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"VRKA/{APP_VERSION}",
            "Accept": "application/vnd.github+json, text/plain, */*",
        },
    )
    with _urlopen(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("The server response exceeded the safe size limit.")
        return data, dict(response.headers.items())


def fetch_ytdlp_release(channel=DEFAULT_YTDLP_CHANNEL):
    _ensure_yt_dlp()
    """Read current release metadata only from yt-dlp's official repositories."""
    raw, _headers = _read_url(_github_release_api(channel))
    payload = json.loads(raw.decode("utf-8"))
    assets = {
        item.get("name"): item.get("browser_download_url")
        for item in payload.get("assets", [])
        if item.get("name") and item.get("browser_download_url")
    }
    binary_name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
    binary_url = assets.get(binary_name)
    checksum_url = assets.get("SHA2-256SUMS")
    version = str(payload.get("tag_name") or "").lstrip("v")
    if not version or not binary_url or not checksum_url:
        raise ValueError("The official release metadata is missing required assets.")
    return {
        "channel": "Nightly" if str(channel).lower() == "nightly" else "Stable",
        "version": version,
        "binary_name": binary_name,
        "binary_url": binary_url,
        "checksum_url": checksum_url,
        "release_url": payload.get("html_url", ""),
    }


def check_ytdlp_update(channel=DEFAULT_YTDLP_CHANNEL):
    _ensure_yt_dlp()
    release = fetch_ytdlp_release(channel)
    backend = resolve_ytdlp_backend()
    available = release["version"] != backend.version or backend.source != "managed"
    _save_runtime_state(
        channel=release["channel"],
        last_check=int(time.time()),
        available_version=release["version"],
        release_url=release["release_url"],
    )
    return {**release, "available": available, "active": active_ytdlp_summary()}


def _checksum_from_manifest(text, filename):
    for line in text.splitlines():
        pieces = line.strip().replace("*", " ").split()
        if len(pieces) >= 2 and pieces[-1] == filename:
            digest = pieces[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                return digest
    raise ValueError(f"No SHA-256 entry was published for {filename}.")


def _download_binary(url, destination, max_bytes=150_000_000):
    request = urllib.request.Request(
        url, headers={"User-Agent": f"VRKA/{APP_VERSION}", "Accept": "application/octet-stream"}
    )
    with _urlopen(request, timeout=60) as response:
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "text/html" in content_type:
            raise ValueError("The server returned an HTML page instead of yt-dlp.")
        written = 0
        with open(destination, "wb") as file_handle:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                written += len(block)
                if written > max_bytes:
                    raise ValueError("The yt-dlp download exceeded the safe size limit.")
                file_handle.write(block)
            file_handle.flush()
            os.fsync(file_handle.fileno())
    return written


def install_ytdlp_update(channel=DEFAULT_YTDLP_CHANNEL):
    _ensure_yt_dlp()
    """Download, checksum, execute-test, and atomically activate an official build."""
    if not _YTDLP_UPDATE_LOCK.acquire(blocking=False):
        raise RuntimeError("Another yt-dlp update is already running.")
    paths = _runtime_paths()
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        release = fetch_ytdlp_release(channel)
        manifest_raw, _headers = _read_url(release["checksum_url"])
        expected_sha = _checksum_from_manifest(
            manifest_raw.decode("utf-8", "replace"), release["binary_name"]
        )
        temporary = paths["download"]
        if temporary.exists():
            temporary.unlink()
        _download_binary(release["binary_url"], temporary)
        actual_sha = hashlib.sha256(temporary.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise ValueError(
                f"SHA-256 verification failed (expected {expected_sha}, got {actual_sha})."
            )
        valid, version, reason = validate_ytdlp_binary(
            temporary, expected_version=release["version"]
        )
        if not valid:
            raise ValueError(f"The downloaded yt-dlp build failed validation: {reason}")
        active = paths["active"]
        previous = paths["previous"]
        if active.exists():
            if previous.exists():
                previous.unlink()
            os.replace(active, previous)
        try:
            os.replace(temporary, active)
        except Exception:
            if previous.exists() and not active.exists():
                os.replace(previous, active)
            raise
        _save_runtime_state(
            channel=release["channel"],
            managed_version=version,
            managed_sha256=actual_sha,
            last_check=int(time.time()),
            last_success=int(time.time()),
            release_url=release["release_url"],
        )
        return {
            "status": "installed",
            "channel": release["channel"],
            "version": version,
            "sha256": actual_sha,
            "path": str(active),
        }
    finally:
        try:
            if paths["download"].exists():
                paths["download"].unlink()
        except OSError:
            pass
        _YTDLP_UPDATE_LOCK.release()


def rollback_ytdlp_update():
    _ensure_yt_dlp()
    paths = _runtime_paths()
    active = paths["active"]
    previous = paths["previous"]
    if not previous.is_file():
        raise FileNotFoundError("No previous managed yt-dlp build is available.")
    valid, version, reason = validate_ytdlp_binary(previous)
    if not valid:
        raise ValueError(f"The rollback build is invalid: {reason}")
    displaced = RUNTIME_DIR / (".yt-dlp.displaced.exe" if os.name == "nt" else ".yt-dlp.displaced")
    try:
        if displaced.exists():
            displaced.unlink()
        if active.exists():
            os.replace(active, displaced)
        os.replace(previous, active)
        if displaced.exists():
            os.replace(displaced, previous)
    except Exception:
        if displaced.exists() and not active.exists():
            os.replace(displaced, active)
        raise
    _save_runtime_state(managed_version=version, last_rollback=int(time.time()))
    return {"status": "rolled_back", "version": version, "path": str(active)}


def restore_bundled_ytdlp():
    _ensure_yt_dlp()
    """Deactivate, but retain, the managed build so rollback remains possible."""
    paths = _runtime_paths()
    active = paths["active"]
    previous = paths["previous"]
    if active.exists():
        if previous.exists():
            previous.unlink()
        os.replace(active, previous)
    _save_runtime_state(managed_version="", restored_bundled=int(time.time()))
    return {
        "status": "bundled",
        "version": _bundled_ytdlp_version(),
        "path": "",
    }


def should_check_ytdlp_on_startup(settings, now=None):
    _ensure_yt_dlp()
    if not isinstance(settings, dict) or not settings.get("ytdlp_check_on_startup", False):
        return False
    state = _read_runtime_state()
    last_check = int(state.get("last_check") or 0)
    return int(now if now is not None else time.time()) - last_check >= YTDLP_STARTUP_CHECK_SECONDS


def migrate_download_archive(output_folder):
    """Merge legacy Seal archives into the VRKA archive without deleting them."""
    folder = Path(output_folder)
    target = folder / VRKA_ARCHIVE_FILENAME
    candidates = [target] + [folder / name for name in LEGACY_ARCHIVE_FILENAMES]
    records = []
    seen = set()
    migrated_from = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            record = line.strip()
            if record and record not in seen:
                seen.add(record)
                records.append(record)
        if candidate != target:
            migrated_from.append(candidate.name)
            backup = candidate.with_name(candidate.name + ".vrka-migration-backup")
            if not backup.exists():
                shutil.copy2(candidate, backup)
    if migrated_from:
        folder.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                "".join(record + "\n" for record in records), encoding="utf-8"
            )
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return target, migrated_from


MEDIA_RESOURCE_PATTERN = re.compile(
    r"(?i)(?:\.m3u8(?:[?#]|$)|\.mpd(?:[?#]|$)|\.(?:mp4|webm|m4v|mov|m4a|mp3|aac|ogg|opus|wav|flac)(?:[?#]|$))"
)
BROWSER_OBSERVATION_LIMIT = 512
BROWSER_EVENT_QUEUE_LIMIT = 1024
BROWSER_CANDIDATE_LIMIT = 50
AUDIO_MEDIA_EXTENSIONS = {
    ".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".weba",
}
VIDEO_MEDIA_EXTENSIONS = {".m4v", ".mov", ".mp4", ".ogv", ".webm"}
MEDIA_SEGMENT_EXTENSIONS = {".m4s", ".ts"}
HANDOFF_HEADER_NAMES = {
    "authorization", "origin", "referer", "user-agent",
    "x-video-expiration", "x-video-ip", "x-video-token",
}
SENSITIVE_HANDOFF_HEADER_NAMES = {
    "authorization", "cookie", "proxy-authorization",
    "x-video-expiration", "x-video-ip", "x-video-token",
}

# The protected browser has exactly one content-filtering authority: the
# bundled uBlock Origin Lite extension (installed before the requested page's
# first document request).  No VRKA-side ad/popup/tracker host lists, resource
# rules, or DOM cosmetics exist; media/session path markers below are used only
# for media-candidate classification (never for blocking).
PROTECTED_SESSION_PATH_MARKERS = (
    "/auth/", "/authorize", "/cdn-cgi/", "/challenge", "/login", "/oauth",
    "/player", "/verify", "api.php",
)
GENERIC_MEDIA_TITLES = {"master", "playlist", "index", "manifest"}


def _case_insensitive_header(headers, name, default=""):
    wanted = str(name).lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return str(value)
    return default


def _handoff_headers(headers):
    result = {}
    for key, value in (headers or {}).items():
        if str(key).lower() in HANDOFF_HEADER_NAMES and value not in (None, ""):
            result[str(key)] = str(value)
    return result


def media_candidate_url(candidate):
    if isinstance(candidate, dict):
        return str(candidate.get("url") or "").strip()
    return str(candidate or "").strip()


def media_candidate_headers(candidate):
    if not isinstance(candidate, dict):
        return {}
    return _handoff_headers(candidate.get("headers") or {})


def is_generic_media_title(title):
    value = Path(str(title or "").strip()).stem.lower()
    return value in GENERIC_MEDIA_TITLES


def candidate_needs_fallback_title(candidate):
    if isinstance(candidate, dict) and candidate.get("probe_title"):
        return is_generic_media_title(candidate["probe_title"])
    path = urllib.parse.urlparse(media_candidate_url(candidate)).path
    return is_generic_media_title(Path(path).stem)


def browser_fallback_title(page_title, source_url, timestamp=None):
    """Create a collision-resistant title only for generic browser media."""
    value = str(page_title or "").strip()
    if not value or is_generic_media_title(value):
        value = urllib.parse.urlparse(str(source_url or "")).hostname or "VRKA media"
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")[:120] or "VRKA media"
    suffix = timestamp or time.strftime("%Y%m%d-%H%M%S")
    return f"{value} - {suffix}"


def _media_observation_score(record):
    url = media_candidate_url(record)
    if not url.startswith(("http://", "https://")):
        return 0, ""
    parsed = urllib.parse.urlparse(url)
    extension = Path((parsed.path or "").lower()).suffix.lower()
    content_type = str((record or {}).get("content_type") or "").lower().split(";", 1)[0].strip()
    if extension == ".m3u8" or content_type in (
        "application/vnd.apple.mpegurl", "application/x-mpegurl",
    ):
        return 180, "HLS"
    if extension == ".mpd" or content_type == "application/dash+xml":
        return 175, "DASH"
    if extension in VIDEO_MEDIA_EXTENSIONS or content_type.startswith("video/"):
        return 140, "Video"
    if extension in AUDIO_MEDIA_EXTENSIONS or content_type.startswith("audio/"):
        return 130, "Audio"
    if extension in MEDIA_SEGMENT_EXTENSIONS:
        return 25, "Segment"
    return 0, ""


def _protected_browser_request(url, record=None):
    score, _kind = _media_observation_score(record or {"url": url})
    if score:
        return True
    lowered_path = (urllib.parse.urlparse(str(url)).path or "").lower()
    return any(marker in lowered_path for marker in PROTECTED_SESSION_PATH_MARKERS)


def should_offer_browser_verification(options, category):
    """Offer a fresh browser session when a cached verified candidate is stale."""
    options = options or {}
    if options.get("cookie_mode") != "session":
        return category in ("cloudflare", "cookies", "unsupported")
    return bool(options.get("session_media_candidates")) and category in (
        "cloudflare", "cookies", "expired", "http", "unknown", "unsupported",
    )


def classify_browser_request(record):
    """Classify one browser request for media-candidate ranking.

    No ad/tracker classification exists here: uBOL is the content filter and
    the candidate pipeline must keep every real media request (media-shaped
    URLs, session/auth paths, the requested page's own CDN) intact.
    """
    url = media_candidate_url(record)
    score, kind = _media_observation_score(record or {})
    return {
        "url": url,
        "score": score,
        "kind": kind,
        "protected": _protected_browser_request(url, record),
    }


_SEGMENT_CODEC_SHAPE_RE = re.compile(
    r"(?:^|[_.-])(?:h264|h265|hevc|avc1?|aac|mp4a|mpeg4|vp9|opus|seg(?:ment)?|chunk|frag(?:ment)?|part|piece|slice)[_.-]?\d{1,6}(?:[_.-]|$)",
    re.I,
)
_SEGMENT_INIT_SHAPE_RE = re.compile(
    r"(?:^|[_.-])(?:h264|h265|hevc|avc1?|aac|mp4a|mpeg4|vp9|opus)[_.-]init[_.-]",
    re.I,
)
# Sequence-numbered media served without a codec marker
# (``name_<n>_<token>_<epoch>.mp4`` from HLS/CDN segment pipelines).  The
# digit run must be delimiter-bounded on both sides AND followed by another
# ``_``-separated field: resolution suffixes (``1080p``), year/version names
# (``movie_2024_1080p``, ``party_2015_trailer``) and leading numeric stream ids
# (``123456_240p.m3u8``) do not match.
_SEGMENT_SEQUENCE_SHAPE_RE = re.compile(r"[_.-]\d{1,8}_[A-Za-z0-9]+_[A-Za-z0-9]", re.I)


def _segment_shaped(url):
    """True when a URL's path stem has HLS/DASH segment or init-fragment shape
    (codec-marked, init, generic sequence-numbered, or a recognized segment
    suffix).  Such URLs are children of a manifest, never standalone media."""
    parsed = urllib.parse.urlparse(str(url))
    path = (parsed.path or "").lower()
    stem = Path(path).stem
    if path.endswith(tuple(MEDIA_SEGMENT_EXTENSIONS)):
        return True
    return bool(
        _SEGMENT_CODEC_SHAPE_RE.search(stem)
        or _SEGMENT_INIT_SHAPE_RE.search(stem)
        or _SEGMENT_SEQUENCE_SHAPE_RE.search(stem)
    )


def _manifest_stems(records):
    """Return ``(manifest_url, path_stem)`` pairs for observed HLS/DASH manifests."""
    stems = []
    for record in records:
        _score, kind = _media_observation_score(record)
        if kind not in ("HLS", "DASH"):
            continue
        path = urllib.parse.urlparse(media_candidate_url(record)).path
        stem = Path(path).stem
        if stem:
            stems.append((media_candidate_url(record), stem))
    stems.sort(key=lambda item: -len(item[1]))
    return stems


def _segment_parent_url(url, manifest_stems):
    """Return the manifest a sequence-numbered codec segment belongs to."""
    parsed = urllib.parse.urlparse(str(url))
    path = parsed.path or ""
    stem = Path(path).stem
    if not stem or not _segment_shaped(url):
        return ""
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    # Strict match first: the segment carries the manifest's rendition suffix
    # (``225371326_240p_h264_287_...`` under ``225371326_240p.m3u8``).
    for manifest_url, manifest_stem in manifest_stems:
        if stem == manifest_stem or not stem.startswith(manifest_stem + "_"):
            continue
        manifest_path = urllib.parse.urlparse(manifest_url).path or ""
        manifest_directory = (
            manifest_path.rsplit("/", 1)[0] if "/" in manifest_path else ""
        )
        if directory == manifest_directory:
            return manifest_url
    # Loose match: the segment omits the rendition suffix but shares the
    # stream id (``259842905_182_<token>_<epoch>.mp4`` vs ``259842905_240p.m3u8``).
    segment_prefix = stem.split("_", 1)[0] if "_" in stem else ""
    if segment_prefix:
        for manifest_url, manifest_stem in manifest_stems:
            if stem == manifest_stem:
                continue
            manifest_prefix = (
                manifest_stem.split("_", 1)[0] if "_" in manifest_stem else ""
            )
            if segment_prefix != manifest_prefix:
                continue
            manifest_path = urllib.parse.urlparse(manifest_url).path or ""
            manifest_directory = (
                manifest_path.rsplit("/", 1)[0] if "/" in manifest_path else ""
            )
            if directory == manifest_directory:
                return manifest_url
    return ""


def rank_media_candidates(records):
    """Merge request/response evidence, reject junk, and rank bounded candidates."""
    merged = {}
    order = 0
    for value in records or []:
        record = dict(value) if isinstance(value, dict) else {"url": value}
        url = media_candidate_url(record)
        if not url.startswith(("http://", "https://")):
            continue
        existing = merged.get(url)
        if existing is None:
            existing = {"url": url, "_order": order, "headers": {}}
            order += 1
            merged[url] = existing
        for name in ("content_type", "content_length", "status", "source"):
            if record.get(name) not in (None, ""):
                existing[name] = record[name]
        existing["headers"].update(_handoff_headers(record.get("headers") or {}))

    manifest_stems = _manifest_stems(merged.values())
    candidates = []
    for record in merged.values():
        classification = classify_browser_request(record)
        if classification["score"] <= 0:
            continue
        # A segment-shaped URL served with a media extension (e.g. HLS
        # ``name_<n>_<token>_<epoch>.mp4``) is a child of a manifest, never a
        # standalone video, even when the segment shape carries no codec
        # marker.  Reclassify it so the parent-or-reject logic below applies.
        if classification["kind"] == "Video" and _segment_shaped(record["url"]):
            classification["kind"] = "Segment"
            classification["score"] = 25
        parent_url = _segment_parent_url(record["url"], manifest_stems)
        if classification["kind"] == "Segment" and not parent_url:
            continue
        if parent_url:
            # Sequence-numbered codec segments belong to a captured manifest;
            # keep them with their parent so the core's segment reasoning can
            # collapse them, and rank them below all standalone media.
            record["segment_parent_url"] = parent_url
            classification["kind"] = "Segment"
            classification["score"] = 25
        record.update(classification)
        candidates.append(record)
    # A generic master/rendition-selector manifest (``master.m3u8``,
    # ``playlist.m3u8``, ``manifest.mpd``) lets the normal downloader choose
    # the best available quality instead of a fixed rendition playlist.  Prefer
    # it over variant playlists so a low-quality rendition observed first does
    # not win merely by order.
    for item in candidates:
        if item.get("kind") in ("HLS", "DASH") and is_master_manifest(item["url"]):
            item["score"] = int(item.get("score") or 0) + 8
    candidates.sort(key=lambda item: (-int(item.get("score") or 0), int(item["_order"])))
    for item in candidates:
        item.pop("_order", None)
    return candidates[:BROWSER_CANDIDATE_LIMIT]


def filter_media_candidates(urls):
    """Return unique HTTP(S) media/manifest resources in ranked order."""
    return [item["url"] for item in rank_media_candidates(urls)]


def media_candidate_label(index, candidate):
    candidate_url = media_candidate_url(candidate)
    if isinstance(candidate, dict) and candidate.get("kind"):
        kind = str(candidate["kind"])
    else:
        _score, kind = _media_observation_score({"url": candidate_url})
        kind = kind or "Direct media"
    host = urllib.parse.urlparse(candidate_url).hostname or "media host"
    return f"{index:02d} · {kind} · {host}"
def _browser_cookie_rows(cookie_objects, page_url):
    rows = []
    fallback_domain = urllib.parse.urlparse(page_url).hostname or ""
    for cookie in cookie_objects or []:
        try:
            morsels = list(cookie.values())
        except Exception:
            morsels = []
        for morsel in morsels:
            domain = str(morsel["domain"] or fallback_domain)
            path = str(morsel["path"] or "/")
            secure = str(morsel["secure"] or "").lower() in ("true", "1")
            expires_text = str(morsel["expires"] or "0")
            try:
                expires = int(float(expires_text))
            except ValueError:
                expires = 0
            rows.append({
                "domain": domain,
                "include_subdomains": domain.startswith("."),
                "path": path,
                "secure": secure,
                "expires": expires,
                "name": str(morsel.key),
                "value": str(morsel.value),
            })
    return rows


def write_netscape_cookie_file(cookie_rows, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File", "# Created for this VRKA session only."]
    for row in cookie_rows or []:
        domain = str(row.get("domain") or "").replace("\t", "")
        name = str(row.get("name") or "").replace("\t", "")
        value = str(row.get("value") or "").replace("\t", "")
        if not domain or not name:
            continue
        include = "TRUE" if row.get("include_subdomains") else "FALSE"
        secure = "TRUE" if row.get("secure") else "FALSE"
        lines.append(
            "\t".join((
                domain,
                include,
                str(row.get("path") or "/"),
                secure,
                str(int(row.get("expires") or 0)),
                name,
                value,
            ))
        )
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def cleanup_task_session_cookie(task):
    """Delete this task's browser-session cookie file, if one exists.

    Session cookie files are written only under BROWSER_SESSION_DIR with the
    ``task-`` name prefix; any other path is never touched.
    """
    session_cookie_file = task.options.get("session_cookie_file") if hasattr(
        task, "options"
    ) else None
    if not session_cookie_file:
        return
    try:
        cookie_path = Path(session_cookie_file)
        if cookie_path.parent == BROWSER_SESSION_DIR and cookie_path.name.startswith("task-"):
            cookie_path.unlink()
    except OSError:
        pass


def _webview2_runtime_candidate_dirs():
    """Ordered candidate folders for the WebView2 runtime, most preferred
    first.

    Preference matters for browser-extension support: browser extensions only
    work on the full Evergreen / fixed-version runtime, NOT on the inbox OS
    component.  So the Evergreen installs (Program Files (x86), LocalAppData)
    are preferred, the vendor-bundled copy next, and the inbox component is
    the LAST resort (it cannot host extensions but is still a real Chromium
    runtime, unlike MSHTML).
    """
    candidates = []
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    for base in (program_files_x86, os.environ.get("LocalAppData") or ""):
        application_dir = os.path.join(base, "Microsoft", "EdgeWebView", "Application")
        try:
            version_dirs = sorted(
                (
                    entry.path for entry in os.scandir(application_dir)
                    if entry.is_dir() and entry.name[0].isdigit()
                ),
                reverse=True,
            )
        except OSError:
            version_dirs = []
        candidates.extend(version_dirs)
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    candidates.append(
        os.path.join(program_files, "Common Files", "Adobe", "Microsoft", "EdgeWebView")
    )
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    candidates.append(os.path.join(system_root, "System32", "Microsoft-Edge-WebView"))
    return candidates


def _find_webview2_runtime_folder():
    """Locate an installed WebView2 runtime folder when the standard registry
    detection misses it (Evergreen installs, vendor-bundled copies, inbox OS
    component).  Returns the folder containing ``msedgewebview2.exe`` or
    ``None``.  Evergreen runtimes are preferred because they can host browser
    extensions."""
    if os.name != "nt":
        return None
    for folder in _webview2_runtime_candidate_dirs():
        if folder and os.path.isfile(os.path.join(folder, "msedgewebview2.exe")):
            return folder
    return None


UBOL_EXTENSION_DIRNAME = "ubol"
BROWSER_EXT_DIR = LOCAL_APP_DATA / "VRKA" / "browser-ext"


def _bundled_ubol_zip():
    """Return the bundled uBlock Origin Lite extension archive, or None."""
    try:
        candidate = resource_path("assets/browser_protection/ubol.zip")
        return candidate if candidate.is_file() else None
    except Exception:
        return None


def _prepare_ubol_extension_dir():
    """Extract the bundled uBOL extension to a stable, versioned runtime
    directory once.  AddBrowserExtensionAsync requires the unpacked folder to
    persist (changing its content removes the extension from the profile), so
    the destination is keyed by a hash of the archive and never reused across
    content changes.  Returns the folder containing ``manifest.json`` or None.
    """
    try:
        archive = _bundled_ubol_zip()
        if archive is None:
            return None
        import hashlib
        import zipfile
        with open(archive, "rb") as fh:
            digest = hashlib.sha1(fh.read()).hexdigest()[:10]
        dest = BROWSER_EXT_DIR / ("%s-%s" % (UBOL_EXTENSION_DIRNAME, digest))
        marker = dest / "manifest.json"
        if marker.is_file():
            return str(dest)
        BROWSER_EXT_DIR.mkdir(parents=True, exist_ok=True)
        # Prune stale copies from older bundled archives (best effort).
        try:
            for old in BROWSER_EXT_DIR.glob("%s-*" % UBOL_EXTENSION_DIRNAME):
                if old.is_dir() and old != dest:
                    import shutil
                    shutil.rmtree(old, ignore_errors=True)
        except Exception:
            pass
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                zf.extract(member, str(dest))
        return str(dest) if marker.is_file() else None
    except Exception:
        return None


def _bundled_observer_zip():
    """Pinned observer archive: bundled (frozen) first, repo tree fallback."""
    try:
        from vrka_core.media_observer import OBSERVER_ARTIFACT_FILENAME, OBSERVER_DIRNAME
        candidate = resource_path(
            "third_party/media_observer/%s/%s" % (OBSERVER_DIRNAME, OBSERVER_ARTIFACT_FILENAME))
        if candidate.is_file():
            return candidate
    except Exception:
        pass
    try:
        from vrka_core.media_observer import artifact_zip_path
        candidate = artifact_zip_path()
        return candidate if candidate.is_file() else None
    except Exception:
        return None


def _prepare_media_observer():
    """Prepare the pinned third-party media-observer extension (a passive,
    read-only media sensor) alongside uBOL.  Fail-open: any problem returns
    an error marker and the protected browser continues without it."""
    try:
        from vrka_core.media_observer import MediaObserverAdapter
        archive = _bundled_observer_zip()
        if archive is None:
            return {"installed": False, "dir": "", "version": "",
                    "error": "pinned observer artifact unavailable",
                    "runtime_installed": False, "enabled": None, "id": ""}
        adapter = MediaObserverAdapter(
            artifacts_root=str(Path(archive).parent),
            runtime_dir=str(BROWSER_EXT_DIR))
        info = adapter.install()
        info.setdefault("runtime_installed", False)
        info.setdefault("enabled", None)
        info.setdefault("id", "")
        return info
    except Exception as exc:
        return {"installed": False, "dir": "", "version": "",
                "error": "%s: %s" % (type(exc).__name__, exc),
                "runtime_installed": False, "enabled": None, "id": ""}


def _patch_pywebview_extension_support():
    """Make pywebview create the WebView2 environment explicitly with browser
    extensions enabled, so uBOL can be installed into the protected browser.

    WebView2 requires ``AreBrowserExtensionsEnabled`` to be set on the
    environment BEFORE it is created, and the environment must be created
    explicitly and passed to ``EnsureCoreWebView2Async`` (the implicit
    creation-properties path returns ERROR_NOT_SUPPORTED / "Class not
    registered" for extensions).  This reimplements pywebview's EdgeChrome
    init with that one change; on any failure the stock behavior is kept.
    """
    try:
        import importlib
        _ec = importlib.import_module("webview.platforms.edgechromium")
    except Exception:
        return False
    if getattr(_ec, "_vrka_extension_support_applied", False):
        return True
    try:
        from System.Threading.Tasks import TaskScheduler
        from System.Drawing import Color
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2Environment,
            CoreWebView2EnvironmentOptions,
        )
        from Microsoft.Web.WebView2.WinForms import (
            CoreWebView2CreationProperties,
            WebView2,
        )

        webview_settings = _ec.webview_settings
        state = _ec._state

        def _init_with_extensions(self, form, window, cache_dir):
            # Mirror pywebview's stock __init__ so nothing else changes.
            self.pywebview_window = window
            self.webview = WebView2()
            props = CoreWebView2CreationProperties()
            runtime_path = webview_settings.get("WEBVIEW2_RUNTIME_PATH")
            if runtime_path:
                if not os.path.isabs(runtime_path):
                    runtime_path = os.path.join(_ec.get_app_root(), runtime_path)
                if os.path.exists(runtime_path):
                    props.BrowserExecutableFolder = runtime_path
                else:
                    _ec.logger.warning(
                        "Custom WebView2 runtime path does not exist: %s. Using system WebView2." % runtime_path
                    )
            props.UserDataFolder = cache_dir
            self.user_data_folder = props.UserDataFolder
            props.set_IsInPrivateModeEnabled(state["private_mode"])
            props.AdditionalBrowserArguments = "--disable-features=ElasticOverscroll"
            if webview_settings.get("ALLOW_FILE_URLS"):
                props.AdditionalBrowserArguments += " --allow-file-access-from-files"
            if webview_settings.get("REMOTE_DEBUGGING_PORT") is not None:
                props.AdditionalBrowserArguments += (
                    " --remote-debugging-port=%s" % webview_settings["REMOTE_DEBUGGING_PORT"]
                )
            self.webview.CreationProperties = props
            self.form = form
            form.Controls.Add(self.webview)
            self.js_results = {}
            self.js_result_semaphore = _ec.Semaphore(0)
            self.webview.Dock = _ec.WinForms.DockStyle.Fill
            self.webview.BringToFront()
            self.webview.CoreWebView2InitializationCompleted += self.on_webview_ready
            self.webview.NavigationStarting += self.on_navigation_start
            self.webview.NavigationCompleted += self.on_navigation_completed
            self.webview.WebMessageReceived += self.on_script_notify
            self.syncContextTaskScheduler = TaskScheduler.FromCurrentSynchronizationContext()
            background = window.background_color.lstrip("#")
            self.webview.DefaultBackgroundColor = Color.FromArgb(
                255,
                int(background[0:2], 16),
                int(background[2:4], 16),
                int(background[4:6], 16),
            )
            if window.transparent:
                self.webview.DefaultBackgroundColor = Color.Transparent
            self.url = None
            self.ishtml = False
            self.html = _ec.DEFAULT_HTML

            # Browser extensions require an explicitly-created environment
            # with AreBrowserExtensionsEnabled set BEFORE the environment is
            # created.  pywebview never sets CoreWebView2EnvironmentOptions,
            # so the stock implicit-environment path cannot enable extensions
            # (the resulting environment reports ERROR_NOT_SUPPORTED for
            # AddBrowserExtensionAsync).  Create the environment explicitly
            # (it completes on a threadpool thread in a few milliseconds; the
            # bounded wait below never pumps the WinForms message loop, so it
            # cannot re-enter window construction) and hand it to the control
            # exactly where stock hands None.  The control defers the actual
            # controller creation until its handle exists (proven stock
            # behavior), and never touch the control from a background thread
            # (Control.Invoke before the message loop runs deadlocks).  On any
            # failure the stock EnsureCoreWebView2Async(None) path is kept.
            env_options = CoreWebView2EnvironmentOptions()
            env_options.AreBrowserExtensionsEnabled = True
            runtime_folder = None
            if getattr(props, "BrowserExecutableFolder", None):
                runtime_folder = str(props.BrowserExecutableFolder)
            try:
                env_task = CoreWebView2Environment.CreateAsync(
                    runtime_folder, str(props.UserDataFolder), env_options
                )
                deadline = time.time() + 60
                while not env_task.IsCompleted and time.time() < deadline:
                    time.sleep(0.02)
                if (env_task.IsCompleted and not env_task.IsFaulted
                        and not env_task.IsCanceled):
                    self.webview.EnsureCoreWebView2Async(env_task.Result)
                else:
                    self.webview.EnsureCoreWebView2Async(None)
            except Exception:
                self.webview.EnsureCoreWebView2Async(None)

        _ec.EdgeChrome.__init__ = _init_with_extensions
        _ec._vrka_extension_support_applied = True
        return True
    except Exception:
        return False


def _numeric_stream_ids(url):
    """Return numeric stream ids found in a URL path (e.g. ``/hls/171550991/``
    or ``b-hls-06/242330696/``).  Many CDNs address each rendition stream by a
    numeric id; two streams never share one, so the id identifies the stream
    generically across its manifest and segment URLs."""
    path = urllib.parse.urlparse(str(url or "")).path
    found = set()
    for component in path.split("/"):
        if re.fullmatch(r"\d{5,}", component):
            found.add(component)
    return found


def mark_widget_candidates(candidates, first_seen_seq, widget_cluster, widget_stream_ids,
                           current_seq, cluster_now, widgets_still_visible):
    """Mark autoplay-widget candidates ``user_started=False`` and extend the
    widget stream-id set.

    A candidate is a sidebar/widget stream when it was first observed before
    the interaction-wait began, or its numeric stream id was seen while the
    widget signature was in the DOM.  New widget instances can re-render with
    NEW numeric stream ids after a server/player interaction (the sidebar cams
    outlive the player iframe), so stream ids keep being collected while the
    widget signature remains visible - even after a large player appears.
    The requested player's stream (a nested frame) has no numeric stream id
    and never matches.  Segments inherit their manifest's interaction status.
    """
    for item in candidates:
        url = str(item.get("url") or "")
        if not url:
            continue
        first = first_seen_seq.get(url)
        if first is None:
            first = current_seq
            first_seen_seq[url] = first
        item["first_seen_seq"] = first
        if cluster_now or (widget_cluster["seen"] and widgets_still_visible):
            # While the widget cluster is visible the observed streams are the
            # sidebar/widget media itself (the requested player is elsewhere
            # and not yet emitting media), so remember them as widget streams.
            # The large-player condition is deliberately NOT required once the
            # cluster has been seen: the cams outlive the player iframe and
            # keep emitting under new ids.
            widget_stream_ids.update(_numeric_stream_ids(url))
        # ``first <= first_seq`` only proves a candidate was present in the
        # initial widget cluster when the URL itself is widget-shaped (a
        # numeric CDN stream id).  A GENERIC MASTER manifest observed in the
        # same snapshot (e.g. ``playlist.m3u8`` on a site that exposes the
        # requested episode's master without interaction) is the requested
        # media, NOT a widget, even when it is observed before any user
        # interaction - it must keep the default user_started=True.
        if (
            widget_cluster["seen"]
            and _numeric_stream_ids(url)
            and first <= int(widget_cluster["first_seq"] or 0)
        ):
            # Present since before any later capture could reflect user
            # interaction - an autoplay widget stream, not the requested
            # media.  The requested player's stream (a nested frame) first
            # appears later and keeps the default user_started=True.
            item["user_started"] = False
        elif (
            widget_cluster["seen"]
            and widget_stream_ids
            and (_numeric_stream_ids(url) & widget_stream_ids)
        ):
            # A later-observed URL belonging to a stream that was already
            # emitting media while the widget cluster was visible is still a
            # widget candidate (widgets keep requesting new segments
            # throughout the session).
            item["user_started"] = False
    # Segments inherit their manifest's interaction status: a widget
    # manifest's segments are widget segments even when the segment URL was
    # first observed in a later capture.
    false_marked = {
        str(item.get("url") or "")
        for item in candidates if item.get("user_started") is False
    }
    for item in candidates:
        parent = str(item.get("segment_parent_url") or "")
        if parent and parent in false_marked:
            item["user_started"] = False
    return widget_stream_ids


def enrich_candidates_with_player_state(candidates, players, observations,
                                        session_start):
    """Associate candidates with observable player-state evidence.

    Mature download managers distinguish the media a user actually activated
    from background streams by correlating network evidence with player state.
    The core ranker already consumes ``playing``/``sustained playback``/
    dimensions/duration/``request_count``/``observed_offset``; this function
    supplies those fields from what the capture can genuinely observe:

    - a candidate whose URL matches an accessible media element inherits that
      element's real playback state (never assumed), duration, and size;
    - every candidate receives its observation count and the offset of its
      first observation relative to the session start, so the core's timing
      and stability signals operate on real data;
    - candidates with no matching accessible element (cross-origin players)
      are left untouched: absence of DOM access is NOT evidence of idleness.

    Pure function; no WebView or I/O dependency.
    """
    players_by_src = {}
    for p in players or []:
        src = str(p.get("src") or "").strip()
        if src.startswith(("http://", "https://")):
            players_by_src[src] = p

    counts = {}
    first_seen = {}
    for obs in observations or []:
        url = media_candidate_url(obs)
        if not url:
            continue
        counts[url] = counts.get(url, 0) + 1
        ts = obs.get("first_seen_ts")
        if isinstance(ts, (int, float)) and ts > 0:
            prev = first_seen.get(url)
            if prev is None or ts < prev:
                first_seen[url] = float(ts)

    for item in candidates:
        url = media_candidate_url(item)
        player = players_by_src.get(url)
        if player is not None:
            ready = int(player.get("readyState") or 0)
            item["playing"] = bool(
                not player.get("paused", True) and ready >= 3
                and (player.get("currentSrc") or player.get("src"))
            )
            duration = player.get("duration")
            if isinstance(duration, (int, float)) and duration > 0:
                item["duration_seconds"] = round(float(duration), 3)
            rect = player.get("rect") or {}
            try:
                width, height = int(rect.get("w") or 0), int(rect.get("h") or 0)
            except (TypeError, ValueError):
                width = height = 0
            if width >= 16 and height >= 16:
                item["width"], item["height"] = width, height
        if url in counts:
            item["request_count"] = max(
                int(item.get("request_count") or 0), counts[url])
        ts = first_seen.get(url)
        if ts is not None:
            item["observed_offset"] = round(
                max(0.0, min(600.0, ts - float(session_start or 0.0))), 3)
    return candidates


def _observation_retention_class(record):
    """Retention priority for one raw observation under memory pressure.

    Higher classes are evicted LAST:
      0 - general page resources (scripts, images, XHR noise)
      1 - media segments (children of a manifest)
      2 - direct video/audio resources
      3 - HLS/DASH manifests (master or media playlist)

    A manifest fetched once must outlive hundreds of later segment URLs so
    that ``_segment_parent_url`` can keep linking its children during long
    interactive sessions; losing it orphans every segment and silently
    destroys the candidate.
    """
    score, kind = _media_observation_score(record)
    if score <= 0:
        return 0
    if kind == "Segment":
        return 1
    if kind in ("Video", "Audio"):
        return 2
    return 3


def evict_observations_beyond_limit(store, limit):
    """Bound an observation store without discarding active media lineage.

    Eviction victim = lowest ``(retention_class, insertion order)``:
    unrelated page resources go first, then segments, then direct media;
    manifests are retained longest.  Ties break to the oldest entry.  The
    store remains bounded by ``limit`` at all times.
    """
    while len(store) > limit:
        victim = min(
            store,
            key=lambda url: (_observation_retention_class(store[url]),),
        )
        store.pop(victim)


def run_browser_verification_helper(start_url, result_path, *, protected=False):
    """Run isolated pywebview capture with bounded, event-driven request observation.

    The protected browser has exactly one content filter: the bundled uBOL
    extension, installed before the requested page's first document request.
    No VRKA-side ad/popup/tracker filtering exists; the native layer only
    keeps page-created windows inside the session and installs uBOL.
    """
    result_path = Path(result_path)
    profile_path = result_path.with_suffix(".profile")
    profile_path.mkdir(parents=True, exist_ok=True)
    session_started_at = time.time()
    payload = {
        "ok": False,
        "page_url": start_url,
        "page_title": "",
        "user_agent": "",
        "referer": start_url,
        "origin": "",
        "cookies": [],
        "media_candidates": [],
        "drm_detected": False,
        "observed_request_count": 0,
        "rejected_junk_count": 0,
        "dropped_request_count": 0,
        "blocked_popup_count": 0,
        "contained_popup_count": 0,
        "blocked_navigation_count": 0,
        "blocked_popup_urls": [],
        "contained_popup_urls": [],
        "blocked_navigation_urls": [],
        "navigation_log": [],
        "player_state": [],
        "interactive_elements": [],
        "dom_overlays": [],
        "popup_guard": "settings-only",
        "error": "",
    }
    browser_events = queue.Queue(maxsize=BROWSER_EVENT_QUEUE_LIMIT)
    observed_requests = {}
    observed_lock = threading.Lock()
    observation_stats = {"dropped": 0}
    # Per-URL capture sequence when the URL was FIRST observed, and the
    # autoplay-widget-cluster state: "the page autoplays several SMALL
    # top-document videos with no large visible player".  On such pages the
    # first-observed media belongs to the sidebar/widget cluster (the
    # requested player is a nested/cross-origin frame and only appears after
    # the user interacts), so those early candidates must not out-rank the
    # media that appears later.  Generic, evidence-based: no site-specific
    # rules, nothing fabricated when metadata is absent.
    first_seen_seq = {}
    widget_cluster = {"seen": False, "first_seq": None}
    # Numeric stream ids observed while the autoplay widget cluster is visible
    # (small top-document videos, no large player).  Any later candidate
    # belonging to one of those streams is a widget candidate even when its
    # URL was only first observed in a later capture (widgets keep requesting
    # segments throughout the session), so it must never end the interaction
    # wait as if it were the user-started requested media.
    widget_stream_ids = set()
    popup_stats = {
        "blocked": 0, "contained": 0, "blocked_navigation": 0,
        "native_guard_installed": False, "guard_error": "",
        "blocked_urls": [], "contained_urls": [], "blocked_navigation_urls": [],
        "navigation_log": [],
        "ubol": None, "ubol_error": "", "ubol_dir": None,
    }
    # Set when uBOL installation completes (or is unavailable/failed); the
    # requested page is not navigated to until this fires, so its first
    # document request runs under the filter.
    ubol_ready = threading.Event()

    def _append_bounded(items, value, limit=12):
        items.append(value)
        if len(items) > limit:
            del items[0]

    def merge_observation(record):
        url = media_candidate_url(record)
        if not url.startswith(("http://", "https://")):
            return
        with observed_lock:
            existing = observed_requests.pop(url, {"url": url, "headers": {}})
            for name in ("content_type", "content_length", "method", "status", "source"):
                if record.get(name) not in (None, ""):
                    existing[name] = record[name]
            existing["headers"].update(_handoff_headers(record.get("headers") or {}))
            existing.setdefault("first_seen_ts", time.time())
            # Retention cache: computed once per merge so eviction under
            # pressure never re-parses URLs (see evict_observations_beyond_limit).
            existing["_mscore"], existing["_mkind"] = _media_observation_score(existing)
            observed_requests[url] = existing
            evict_observations_beyond_limit(observed_requests,
                                            BROWSER_OBSERVATION_LIMIT)

    def observation_worker():
        while True:
            item = browser_events.get()
            try:
                if item is None:
                    return
                if item.get("_flush"):
                    item["_flush"].set()
                else:
                    merge_observation(item)
            finally:
                browser_events.task_done()

    observation_thread = threading.Thread(
        target=observation_worker,
        name="vrka-browser-observer",
        daemon=True,
    )
    observation_thread.start()

    def enqueue_observation(record):
        try:
            browser_events.put_nowait(record)
        except queue.Full:
            observation_stats["dropped"] += 1

    def flush_observations(timeout=1.5):
        completed = threading.Event()
        try:
            browser_events.put({"_flush": completed}, timeout=0.25)
            completed.wait(timeout)
        except queue.Full:
            pass

    try:
        import webview

        # Defense in depth: pywebview must never hand page-created windows to
        # the user's default browser, even before the native guard is attached.
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False

        # The WebView2 runtime ships as an inbox OS component on modern
        # Windows (System32\Microsoft-Edge-WebView) and is sometimes bundled
        # by other vendors, but pywebview only detects the classic EdgeUpdate
        # registry registration.  When the standard detection would silently
        # fall back to the deprecated MSHTML engine (which cannot run the
        # ES6 capture scripts), point pywebview at the discovered runtime
        # folder explicitly.  Harmless when the runtime is already registered
        # (the setting is only consumed as the browser executable folder).
        try:
            if not webview.settings["WEBVIEW2_RUNTIME_PATH"] and os.name == "nt":
                _runtime_folder = _find_webview2_runtime_folder()
                if _runtime_folder:
                    webview.settings["WEBVIEW2_RUNTIME_PATH"] = _runtime_folder
        except (KeyError, TypeError):
            pass

        # Generic first-line content filtering (uBlock Origin Lite) requires
        # browser extensions, which require an explicitly-created WebView2
        # environment with extensions enabled BEFORE the window exists.
        # Patch pywebview and resolve the bundled extension now; installation
        # into the profile happens in the guard below.  Everything degrades
        # gracefully when extensions are unavailable.
        _patch_pywebview_extension_support()
        ubol_dir = _prepare_ubol_extension_dir()
        popup_stats["ubol_dir"] = ubol_dir
        popup_stats["ubol_error"] = (
            "" if ubol_dir else "bundled uBOL extension unavailable"
        )
        observer_info = _prepare_media_observer()
        popup_stats["observer"] = observer_info

        # The window opens on a blank page; the requested URL is loaded only
        # after the session guard (and uBOL, when available) is ready, so the
        # target site's first document request runs under the filter.  No
        # post-install reload is needed because the target has not loaded yet.
        window = webview.create_window(
            "VRKA Browser Verification — close this window when the media is ready",
            url="about:blank",
            width=1100,
            height=760,
            min_size=(760, 520),
        )

        def install_webview2_session_guard():
            """Attach the native session guard and install uBOL.

            There is exactly one content-filtering authority: uBOL.  The
            native layer only (a) keeps page-created windows inside the
            protected session (they are marked handled, never handed to the
            user's browser), and (b) installs uBOL and signals ``ubol_ready``
            so the requested page is not navigated to before filtering is
            active.  No ad/popup/tracker host lists, resource rules, or DOM
            cosmetics exist here.
            """
            if popup_stats["native_guard_installed"] or os.name != "nt":
                return popup_stats["native_guard_installed"]
            try:
                browser_view = window.gui.BrowserView.instances.get(window.uid)
                if browser_view is None:
                    raise RuntimeError("WebView2 browser view is unavailable.")
                from System import Action

                def install_on_ui_thread():
                    browser = browser_view.browser
                    # The browser view can exist before the WebView2 control is
                    # attached; poll-and-retry instead of failing the guard.
                    if browser.webview is None:
                        return False
                    core = browser.webview.CoreWebView2

                    def handle_new_window(sender, args):
                        # Security-only firewall: page-created windows never
                        # escape the protected session.  Every request is
                        # marked handled BEFORE any further processing so no
                        # popup window is ever created; what is ad traffic is
                        # uBOL's decision (its DNR rules), never a VRKA
                        # classification.
                        args.set_Handled(True)
                        try:
                            uri = str(getattr(args, "Uri", "") or "")
                            if not uri and hasattr(args, "get_Uri"):
                                uri = str(args.get_Uri() or "")
                            _append_bounded(popup_stats["navigation_log"], {
                                "url": uri, "event": "popup", "action": "block",
                                "ts": time.time(),
                            }, limit=64)
                            popup_stats["blocked"] += 1
                            _append_bounded(popup_stats["blocked_urls"], uri)
                        except Exception:
                            popup_stats["blocked"] += 1

                    def record_navigation_start(sender, args):
                        # Evidence-only: every top-level navigation attempt is
                        # recorded with its URL and time.  Blocking is
                        # delegated to uBOL (its DNR main_frame rules); VRKA
                        # never cancels navigation from a host list.
                        try:
                            uri = str(getattr(args, "Uri", "") or "")
                            if not uri and hasattr(args, "get_Uri"):
                                uri = str(args.get_Uri() or "")
                            _append_bounded(popup_stats["navigation_log"], {
                                "url": uri, "event": "navigation", "action": "allow",
                                "ts": time.time(),
                            }, limit=64)
                        except Exception:
                            pass

                    try:
                        core.NewWindowRequested -= browser.on_new_window_request
                    except Exception:
                        pass
                    browser._vrka_popup_handler = handle_new_window
                    browser._vrka_navigation_handler = record_navigation_start
                    core.NewWindowRequested += browser._vrka_popup_handler
                    browser.webview.NavigationStarting += browser._vrka_navigation_handler
                    popup_stats["native_guard_installed"] = True
                    popup_stats["guard_error"] = ""

                    # Install uBOL (the single content-filter authority) into
                    # the profile.  The caller waits for ``ubol_ready`` before
                    # navigating to the requested page; any failure is
                    # recorded and navigation still proceeds (fail-open).
                    if ubol_dir and not popup_stats.get("ubol"):
                        try:
                            from System import Action as _NetAction
                            from System.Threading.Tasks import Task as _NetTask
                            from Microsoft.Web.WebView2.Core import CoreWebView2BrowserExtension

                            ext_task = core.Profile.AddBrowserExtensionAsync(ubol_dir)

                            def _ubol_installed(task):
                                try:
                                    if task.IsFaulted or task.IsCanceled:
                                        popup_stats["ubol_error"] = (
                                            "uBOL install failed (extension support unavailable?)"
                                        )
                                        return
                                    ext = task.Result
                                    detail = {"installed": True}

                                    def _read_metadata():
                                        # The extension object's metadata (Id /
                                        # IsEnabled / Name) reads through a raw
                                        # COM interface that fails with
                                        # E_NOINTERFACE from a non-UI thread on
                                        # this SDK/runtime pairing.  Read it on
                                        # the UI thread (best-effort; the
                                        # install already succeeded, so a
                                        # metadata failure never defeats the
                                        # extension).
                                        try:
                                            for prop, attr in (
                                                ("id", "Id"),
                                                ("enabled", "IsEnabled"),
                                                ("name", "Name"),
                                            ):
                                                try:
                                                    value = getattr(ext, attr)
                                                    if value is not None:
                                                        detail[prop] = str(value)
                                                except Exception:
                                                    detail[prop + "_error"] = (
                                                        "metadata unreadable"
                                                    )
                                        except Exception:
                                            detail["metadata_error"] = "unreadable"

                                    try:
                                        if browser_view.InvokeRequired:
                                            browser_view.Invoke(Action(_read_metadata))
                                        else:
                                            _read_metadata()
                                    except Exception:
                                        _read_metadata()
                                    popup_stats["ubol"] = detail
                                except Exception as ext_exc:
                                    popup_stats["ubol_error"] = (
                                        f"{type(ext_exc).__name__}: {ext_exc}"
                                    )
                                finally:
                                    try:
                                        ubol_ready.set()
                                    except Exception:
                                        pass

                            ext_task.ContinueWith(
                                _NetAction[_NetTask[CoreWebView2BrowserExtension]](
                                    _ubol_installed
                                )
                            )
                        except Exception as ubol_exc:
                            popup_stats["ubol_error"] = (
                                f"{type(ubol_exc).__name__}: {ubol_exc}"
                            )
                            ubol_ready.set()
                    else:
                        ubol_ready.set()

                    # Additive read-only media observer (never gates
                    # navigation; fail-open; result recorded for status).
                    observer_info = popup_stats.get("observer") or {}
                    if observer_info.get("installed") and observer_info.get("dir"):
                        try:
                            from System import Action as _ObsAction
                            from System.Threading.Tasks import Task as _ObsTask
                            from Microsoft.Web.WebView2.Core import CoreWebView2BrowserExtension as _ObsExt

                            def _observer_installed(task):
                                try:
                                    if task.IsFaulted or task.IsCanceled:
                                        observer_info["error"] = "observer install failed"
                                    else:
                                        observer_info["runtime_installed"] = True
                                        try:
                                            observer_info["id"] = str(getattr(task.Result, "Id", "") or "")
                                            observer_info["enabled"] = bool(getattr(task.Result, "IsEnabled", False))
                                        except Exception:
                                            pass
                                except Exception as exc:
                                    observer_info["error"] = "%s: %s" % (type(exc).__name__, exc)

                            obs_task = core.Profile.AddBrowserExtensionAsync(observer_info["dir"])
                            obs_task.ContinueWith(
                                _ObsAction[_ObsTask[_ObsExt]](_observer_installed)
                            )
                        except Exception as obs_exc:
                            observer_info["error"] = "%s: %s" % (type(obs_exc).__name__, obs_exc)
                    return True
                if browser_view.InvokeRequired:
                    browser_view.Invoke(Action(install_on_ui_thread))
                else:
                    install_on_ui_thread()
                return bool(popup_stats["native_guard_installed"])
            except Exception as exc:
                popup_stats["guard_error"] = f"{type(exc).__name__}: {exc}"
                ubol_ready.set()
                return False

        def capture_request(request):
            enqueue_observation({
                "url": str(getattr(request, "url", "") or ""),
                "method": str(getattr(request, "method", "") or ""),
                "headers": _handoff_headers(getattr(request, "headers", {}) or {}),
                "source": "request",
            })

        def capture_response(response):
            headers = dict(getattr(response, "headers", {}) or {})
            enqueue_observation({
                "url": str(getattr(response, "url", "") or ""),
                "status": int(getattr(response, "status_code", 0) or 0),
                "content_type": _case_insensitive_header(headers, "content-type"),
                "content_length": _case_insensitive_header(headers, "content-length"),
                "source": "response",
            })

        # pywebview exposes WebView2 request/response events from the very first
        # navigation. Synchronous dispatch only enqueues a tiny record, avoiding
        # pywebview's default thread-per-request behavior; classification remains
        # on the single observer worker above.
        window.events.request_sent._should_lock = True
        window.events.response_received._should_lock = True
        window.events.request_sent += capture_request
        window.events.response_received += capture_response

        def install_observer(*_event_args):
            try:
                install_webview2_session_guard()
                window.evaluate_js(
                    """
                    (() => {
                      const documents = [];
                      const visit = (doc) => {
                        if (!doc || documents.includes(doc)) return;
                        documents.push(doc);
                        try {
                          doc.querySelectorAll('iframe').forEach((frame) => {
                            try { visit(frame.contentDocument); } catch (_) {}
                          });
                        } catch (_) {}
                      };
                      visit(document);
                      documents.forEach((doc) => {
                        try {
                          doc.defaultView.__vrkaEncryptedMediaUsed = false;
                          const watch = (node) => {
                            if (!node || node.__vrkaWatched) return;
                            node.__vrkaWatched = true;
                            node.addEventListener('encrypted', () => {
                              doc.defaultView.__vrkaEncryptedMediaUsed = true;
                            }, {capture: true});
                          };
                          doc.querySelectorAll('video,audio').forEach(watch);
                        } catch (_) {}
                      });
                      return true;
                    })();
                    """
                )
                # No VRKA DOM ad containment exists: uBOL is the content
                # filter and the page must render as it would under
                # Chrome + uBOL (the player must never be hidden by a VRKA
                # heuristic).
            except Exception:
                pass

        capture_started = threading.Event()
        allow_close = threading.Event()
        payload_written = threading.Event()
        capture_lock = threading.Lock()
        capture_seq = [0]
        session_done = threading.Event()
        media_capture_holder = {"capture": None, "error": ""}

        def start_media_capture():
            """Ensure bounded media body capture is attached to the running
            protected browser.

            Capture is attached SESSION-WIDE (at first use, before the
            player's first fetch) because fMP4 init fragments are typically
            served from the HTTP cache on later loads and would otherwise be
            invisible to the response events forever.  The captured bytes
            are spilled to disk under a session key and are deleted with the
            episode unless the browser-context transfer consumes them.  The
            TRANSFER itself still activates only after ExternalReplayRejected
            - capture is a passive, bounded sensor of the session the user
            is already watching."""
            if media_capture_holder["capture"] is not None:
                return True
            if os.name != "nt":
                return False
            try:
                from System import Action
                from vrka_core.browser_capture import MediaBodyCapture
                browser_view = window.gui.BrowserView.instances.get(window.uid)
                if browser_view is None:
                    media_capture_holder["error"] = "browser view unavailable"
                    return False
                objects_dir = result_path.parent / (
                    "media-objects-" + result_path.stem.replace("browser-", ""))
                holder = {"capture": None, "error": ""}

                def attach_on_ui_thread():
                    try:
                        browser = browser_view.browser
                        if browser.webview is None:
                            holder["error"] = "webview control not ready"
                            return
                        core = browser.webview.CoreWebView2
                        if core is None:
                            holder["error"] = "CoreWebView2 not ready"
                            return
                        capture = MediaBodyCapture(core, objects_dir)
                        if capture.attach():
                            holder["capture"] = capture
                        else:
                            holder["error"] = "attach failed"
                    except Exception as exc:
                        holder["error"] = f"{type(exc).__name__}: {exc}"

                if browser_view.InvokeRequired:
                    browser_view.Invoke(Action(attach_on_ui_thread))
                else:
                    attach_on_ui_thread()
                if holder["capture"] is None:
                    media_capture_holder["error"] = (
                        holder["error"] or "attach produced no capture")
                    return False
                media_capture_holder["capture"] = holder["capture"]
                return True
            except Exception as exc:
                media_capture_holder["error"] = f"{type(exc).__name__}: {exc}"
                return False

        def ensure_session_capture_when_ready():
            """Attach the session-wide capture as soon as CoreWebView2
            exists, before the requested page is navigated, so the player's
            very first fetch (init fragments included) is observable."""
            if os.name != "nt" or not protected:
                return
            deadline = time.time() + 90
            while time.time() < deadline and not session_done.is_set():
                if start_media_capture():
                    return
                time.sleep(0.5)

        threading.Thread(
            target=ensure_session_capture_when_ready,
            name="vrka-session-capture", daemon=True,
        ).start()

        def capture_session(manual_closed=False):
            with capture_lock:
                return _capture_locked(manual_closed=manual_closed)

        def _capture_locked(manual_closed=False):
            try:
                data = window.evaluate_js(
                    """
                    (() => {
                      const documents = [];
                      const visit = (doc) => {
                        if (!doc || documents.includes(doc)) return;
                        documents.push(doc);
                        try {
                          doc.querySelectorAll('iframe').forEach((frame) => {
                            try { visit(frame.contentDocument); } catch (_) {}
                          });
                        } catch (_) {}
                      };
                      visit(document);
                      const resources = [];
                      let drm = false;
                      const players = [];
                      documents.forEach((doc) => {
                        try {
                          doc.querySelectorAll('video,audio,source').forEach((node) => {
                            if (node.currentSrc) resources.push(node.currentSrc);
                            if (node.src) resources.push(node.src);
                          });
                          doc.querySelectorAll('video,audio').forEach((node) => {
                            if (players.length >= 4) return;
                            let rect = null;
                            try {
                              const r = node.getBoundingClientRect();
                              rect = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                            } catch (_) {}
                            players.push({
                              tag: node.tagName.toLowerCase(),
                              src: node.currentSrc || node.src || "",
                              paused: node.paused,
                              readyState: node.readyState,
                              networkState: node.networkState,
                              currentTime: Math.round(node.currentTime * 10) / 10,
                              duration: isFinite(node.duration) ? Math.round(node.duration * 10) / 10 : -1,
                              rect: rect
                            });
                          });
                          doc.defaultView.performance.getEntriesByType('resource')
                            .forEach((entry) => resources.push(entry.name));
                          drm = drm || Boolean(doc.defaultView.__vrkaEncryptedMediaUsed);
                        } catch (_) {}
                      });
                      const interactive = [];
                      const seenInter = new Set();
                      const collect = (node) => {
                        if (interactive.length >= 48) return;
                        let text = "";
                        let aria = "";
                        try {
                          text = (node.innerText || node.textContent || "").trim().replace(/\\s+/g, " ").slice(0, 40);
                        } catch (_) {}
                        try {
                          aria = String(node.getAttribute && (node.getAttribute('aria-label') || node.getAttribute('title') || node.getAttribute('data-title') || "") || "").trim().slice(0, 40);
                        } catch (_) {}
                        if (!text && !aria) return;
                        let rect = null;
                        try {
                          const r = node.getBoundingClientRect();
                          if (r.width < 8 || r.height < 8) return;
                          rect = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                        } catch (_) { return; }
                        const key = node.tagName + "|" + text + "|" + aria;
                        if (seenInter.has(key)) return;
                        seenInter.add(key);
                        interactive.push({ tag: node.tagName.toLowerCase(), text: text, aria: aria, rect: rect });
                      };
                      documents.forEach((doc) => {
                        try {
                          doc.querySelectorAll('button, a[href], [role=button], [role=tab], [aria-label], [title], select, input[type=button], input[type=submit]').forEach((node) => {
                            if (interactive.length >= 48) return;
                            try { collect(node); } catch (_) {}
                          });
                        } catch (_) {}
                      });
                      const overlays = [];
                      documents.forEach((doc) => {
                        try {
                          doc.querySelectorAll('div, section, aside, iframe').forEach((node) => {
                            if (overlays.length >= 16) return;
                            let rect = null;
                            let pos = "";
                            try {
                              const r = node.getBoundingClientRect();
                              if (r.width < 40 || r.height < 30) return;
                              if (r.width > innerWidth * 0.92 && r.height > innerHeight * 0.92) return;
                              rect = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                              pos = getComputedStyle(node).position;
                            } catch (_) { return; }
                            if (pos !== 'fixed' && pos !== 'absolute' && pos !== 'sticky') return;
                            if (node.querySelector && node.querySelector('video')) return;
                            let frameHost = "";
                            if (node.tagName === 'IFRAME') {
                              try { frameHost = new URL(node.src).host; } catch (_) {}
                            }
                            const cls = String(node.className || "").slice(0, 60);
                            const id = String(node.id || "").slice(0, 60);
                            overlays.push({ tag: node.tagName.toLowerCase(), id: id, cls: cls, pos: pos, rect: rect, frameHost: frameHost });
                          });
                        } catch (_) {}
                      });
                      const iframes = [];
                      try {
                        document.querySelectorAll('iframe').forEach((frame) => {
                          try {
                            const r = frame.getBoundingClientRect();
                            if (r.width > 200 && r.height > 150) {
                              iframes.push({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) });
                            }
                          } catch (_) {}
                        });
                      } catch (_) {}
                      iframes.sort((a, b) => b.w * b.h - a.w * a.h);
                      return {
                        url: location.href,
                        title: document.title || "",
                        userAgent: navigator.userAgent,
                        view: { w: innerWidth, h: innerHeight },
                        resources,
                        drm,
                        players,
                        iframes,
                        interactive,
                        overlays
                      };
                    })();
                    """
                ) or {}
                for resource_url in data.get("resources") or []:
                    enqueue_observation({"url": resource_url, "source": "dom"})
                flush_observations()
                with observed_lock:
                    observations = [
                        {k: v for k, v in item.items()
                         if k not in ("_mscore", "_mkind")}
                        for item in observed_requests.values()
                    ]
                page_url = str(data.get("url") or start_url)
                # No VRKA-side observation filtering exists: every observed
                # request is ranked and uBOL is the only content filter.
                candidates = rank_media_candidates(observations)
                # Player-state association: real playback evidence from
                # accessible media elements plus per-URL observation counts
                # and timing offsets for the core's stability signals.
                enrich_candidates_with_player_state(
                    candidates, data.get("players") or [], observations,
                    session_started_at,
                )
                # Diagnostics: media-shaped responses that classification
                # could not score (extensionless video/audio/octet-stream).
                # Counted, never promoted: no blind binary acceptance.
                unclassified_media = 0
                unclassified_hosts = set()
                for obs in observations:
                    if _media_observation_score(obs)[0] > 0:
                        continue
                    ctype = str(obs.get("content_type") or "").split(";")[0] \
                        .strip().lower()
                    path = urllib.parse.urlparse(
                        media_candidate_url(obs)).path.lower()
                    if (ctype.startswith(("video/", "audio/"))
                            or ctype == "application/octet-stream") \
                            and not Path(path).suffix:
                        unclassified_media += 1
                        host = urllib.parse.urlparse(
                            media_candidate_url(obs)).hostname or ""
                        if host:
                            unclassified_hosts.add(host)
                # --- autoplay-widget-cluster detection + candidate lineage ---
                current_seq = capture_seq[0] + 1
                view = data.get("view") or {}
                view_w = max(1, int(view.get("w") or 1100))
                view_h = max(1, int(view.get("h") or 760))
                players_now = data.get("players") or []
                # Presence-based (not playback-based) so the very first snapshot
                # catches the widgets before they start playing: several SMALL
                # video elements with NO large visible player means the visible
                # DOM media is sidebar/widget media and the requested player is
                # elsewhere (a nested/cross-origin frame).
                small_videos = [
                    p for p in players_now
                    if (p.get("rect") or {}).get("w")
                    and (p.get("rect") or {}).get("h")
                    and p["rect"]["w"] * p["rect"]["h"] < 0.25 * view_w * view_h
                ]
                large_videos = [
                    p for p in players_now
                    if (p.get("rect") or {}).get("w")
                    and (p.get("rect") or {}).get("h")
                    and p["rect"]["w"] * p["rect"]["h"] >= 0.25 * view_w * view_h
                ]
                cluster_now = len(small_videos) >= 2 and not large_videos
                if cluster_now and not widget_cluster["seen"]:
                    widget_cluster["seen"] = True
                    widget_cluster["first_seq"] = current_seq
                # Widget stream-id collection stays active while the widget
                # signature remains in the DOM.  Once the cluster has been
                # seen, a SINGLE remaining small widget video is still widget
                # context (the cams flicker between 1-2 visible during ad
                # rotation), so collection continues while any small widget
                # video is visible - not just while two or more are present.
                # The sidebar cams persist and can re-render with NEW numeric
                # stream ids, and they would otherwise escape as
                # ``user_started`` and end the interaction wait early.  The
                # requested player's stream (a nested frame, large player) has
                # no numeric stream id, so it can never match.
                widgets_still_visible = len(small_videos) >= 1
                mark_widget_candidates(
                    candidates, first_seen_seq, widget_cluster, widget_stream_ids,
                    current_seq, cluster_now, widgets_still_visible,
                )
                # Nothing is rejected by a VRKA filter layer (uBOL owns that);
                # the count stays for evidence that the pipeline kept every
                # observed request.
                rejected = 0
                parsed_page = urllib.parse.urlparse(page_url)
                origin = f"{parsed_page.scheme}://{parsed_page.netloc}" if parsed_page.netloc else ""
                # Build the new capture state atomically and only then commit
                # it, so a mid-update exception (e.g. a slow cookie API call)
                # can never leave the payload half-old/half-new with stale
                # fields (such as a missing uBOL extension record).
                new_payload = {
                    "ok": True,
                    "page_url": page_url,
                    "page_title": str(data.get("title") or ""),
                    "user_agent": str(data.get("userAgent") or ""),
                    "referer": page_url,
                    "origin": origin,
                    "cookies": _browser_cookie_rows(window.get_cookies(), page_url),
                    "media_candidates": candidates,
                    "autoplay_widget_page": bool(widget_cluster["seen"]),
                    "view_size": {"w": view_w, "h": view_h},
                    "drm_detected": bool(data.get("drm")),
                    "observed_request_count": len(observations),
                    "unclassified_media_count": unclassified_media,
                    "unclassified_media_hosts": sorted(unclassified_hosts)[:6],
                    "rejected_junk_count": rejected,
                    "dropped_request_count": observation_stats["dropped"],
                    "blocked_popup_count": popup_stats["blocked"],
                    "contained_popup_count": popup_stats["contained"],
                    "blocked_navigation_count": popup_stats["blocked_navigation"],
                    "popup_guard": (
                        "webview2-native"
                        if popup_stats["native_guard_installed"]
                        else "settings-only"
                    ),
                    "popup_guard_error": popup_stats["guard_error"],
                    "ubol_extension": popup_stats.get("ubol"),
                    "ubol_error": popup_stats.get("ubol_error", ""),
                    "ubol_dir": popup_stats.get("ubol_dir"),
                    "observer_extension": popup_stats.get("observer") or {},
                    "blocked_popup_urls": popup_stats["blocked_urls"][-8:],
                    "contained_popup_urls": popup_stats["contained_urls"][-8:],
                    "blocked_navigation_urls": popup_stats["blocked_navigation_urls"][-8:],
                    "captured_dom_urls": sorted(set(
                        str(u) for u in (data.get("resources") or [])
                        if isinstance(u, str) and u.startswith(("http://", "https://"))
                    ))[:40],
                    "navigation_log": popup_stats["navigation_log"][-64:],
                    "player_state": data.get("players") or [],
                    "interactive_elements": data.get("interactive") or [],
                }
                payload.update(new_payload)
            except Exception as exc:
                # A capture failure after an earlier capture already succeeded
                # is teardown noise (e.g. a late evaluate_js racing the closing
                # window): it must not poison evidence that was already good.
                if not payload.get("ok"):
                    payload["error"] = f"{type(exc).__name__}: {exc}"
            # Media body-capture state is independent of the page JS: it
            # must land even when evaluate_js fails mid-navigation (the
            # capture reload) or during teardown races.
            payload["media_capture"] = (
                media_capture_holder["capture"].snapshot()
                if media_capture_holder["capture"] is not None else None)
            payload["media_capture_error"] = media_capture_holder.get("error", "")
            capture_seq[0] += 1
            payload["capture_seq"] = capture_seq[0]
            if manual_closed:
                payload["manual_closed"] = True
            _atomic_write_json(result_path, payload)
            if payload.get("ok"):
                payload_written.set()
            return True

        def capture_and_close(manual_closed=False):
            try:
                capture_session(manual_closed=manual_closed)
            finally:
                allow_close.set()
                try:
                    window.destroy()
                except Exception:
                    pass

        def handle_closing(*_event_args):
            # Protected sessions capture only on demand (a media-playable
            # signal or an explicit capture command), never as a side effect of
            # closing the window. A close before any on-demand capture is a
            # premature manual close: the session is still snapshotted for
            # diagnostics but flagged so the app refuses a close-triggered
            # handoff. Once a capture exists or the app committed the handoff,
            # closing is allowed freely.
            if allow_close.is_set() or (protected and payload_written.is_set()):
                return True
            if not capture_started.is_set():
                capture_started.set()
                threading.Thread(
                    target=capture_and_close,
                    kwargs={"manual_closed": bool(protected)},
                    daemon=True,
                ).start()
            return False

        def start_stdin_worker():
            if not protected:
                return

            def serve_stdin():
                try:
                    stream = sys.stdin
                    if stream is None or not hasattr(stream, "readline"):
                        return
                    while True:
                        command = stream.readline()
                        if not command:
                            return
                        command = command.strip().lower()
                        if command == "capture":
                            capture_session()
                        elif command == "mediacapture":
                            start_media_capture()
                            capture_session()
                        elif command == "commit":
                            # Always write a final live snapshot before closing so
                            # the payload carries the complete navigation/URL
                            # evidence for the whole session, not only the state
                            # at the first on-demand capture.
                            capture_session()
                            allow_close.set()
                            try:
                                window.destroy()
                            except Exception:
                                pass
                        elif command == "cancel":
                            allow_close.set()
                            try:
                                window.destroy()
                            except Exception:
                                pass
                        elif command.startswith("click "):
                            # QA-harness-only: reproduce a user click on the
                            # element whose visible text/aria contains the given
                            # label, in the top document or a same-origin frame.
                            # Normal product usage never sends this command.
                            label = command[len("click "):].strip()[:64]
                            if label:
                                try:
                                    window.evaluate_js(
                                        """
                                        (() => {
                                          const label = %s;
                                          const docs = [];
                                          const visit = (doc) => {
                                            if (!doc || docs.includes(doc)) return;
                                            docs.push(doc);
                                            try {
                                              doc.querySelectorAll('iframe').forEach((frame) => {
                                                try { visit(frame.contentDocument); } catch (_) {}
                                              });
                                            } catch (_) {}
                                          };
                                          visit(document);
                                          const needle = label.toLowerCase();
                                          for (const doc of docs) {
                                            try {
                                              const nodes = doc.querySelectorAll(
                                                'button, a[href], [role=button], [role=tab], [aria-label], [title]'
                                              );
                                              for (const node of nodes) {
                                                let text = "";
                                                let aria = "";
                                                try {
                                                  text = (node.innerText || node.textContent || "").trim().replace(/\\s+/g, " ");
                                                } catch (_) {}
                                                try {
                                                  aria = String(node.getAttribute && (node.getAttribute('aria-label') || node.getAttribute('title') || "") || "").trim();
                                                } catch (_) {}
                                                if (!text && !aria) continue;
                                                const hay = (text + " " + aria).toLowerCase();
                                                if (hay.indexOf(needle) === -1) continue;
                                                try {
                                                  const r = node.getBoundingClientRect();
                                                  if (r.width < 4 || r.height < 4) continue;
                                                  node.scrollIntoView({block: 'center'});
                                                  node.click();
                                                  return { clicked: true, tag: node.tagName.toLowerCase(), text: text.slice(0, 40), rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)} };
                                                } catch (_) {}
                                              }
                                            } catch (_) {}
                                          }
                                          return { clicked: false };
                                        })();
                                        """ % json.dumps(label)
                                    )
                                except Exception:
                                    pass
                        elif command.startswith("clickclass "):
                            # QA-harness-only: reproduce a user click on the
                            # element with the given CSS class in the top
                            # document or a same-origin frame (e.g. a
                            # DIV-based play overlay with no text/aria).  The
                            # full mouse sequence is dispatched so inline
                            # onclick handlers receive a real event.
                            class_name = command[len("clickclass "):].strip()[:64]
                            if class_name and re.match(r"^[A-Za-z0-9_-]+$", class_name):
                                try:
                                    window.evaluate_js(
                                        """
                                        (() => {
                                          const cls = %s;
                                          const docs = [];
                                          const visit = (doc) => {
                                            if (!doc || docs.includes(doc)) return;
                                            docs.push(doc);
                                            try {
                                              doc.querySelectorAll('iframe').forEach((frame) => {
                                                try { visit(frame.contentDocument); } catch (_) {}
                                              });
                                            } catch (_) {}
                                          };
                                          visit(document);
                                          const needle = '.' + cls;
                                          for (const doc of docs) {
                                            try {
                                              const nodes = doc.querySelectorAll(needle);
                                              for (const node of nodes) {
                                                try {
                                                  const r = node.getBoundingClientRect();
                                                  if (r.width < 4 || r.height < 4) continue;
                                                  const seq = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
                                                  for (const type of seq) {
                                                    try {
                                                      node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: doc.defaultView }));
                                                    } catch (_) {}
                                                  }
                                                  try { node.click(); } catch (_) {}
                                                  return { clicked: true, tag: node.tagName.toLowerCase(), cls: cls, rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)} };
                                                } catch (_) {}
                                              }
                                            } catch (_) {}
                                          }
                                          return { clicked: false };
                                        })();
                                        """ % json.dumps(class_name)
                                    )
                                except Exception:
                                    pass
                        elif command == "clickvideo":
                            # QA-harness-only: reproduce a user click on the
                            # largest visible <video> element (the generic
                            # play/pause gesture of HTML players) in the top
                            # document or a same-origin frame.  The full
                            # mouse sequence is dispatched so player handlers
                            # receive real events.  Normal product usage
                            # never sends this command.
                            try:
                                window.evaluate_js(
                                    """
                                    (() => {
                                      const docs = [];
                                      const visit = (doc) => {
                                        if (!doc || docs.includes(doc)) return;
                                        docs.push(doc);
                                        try {
                                          doc.querySelectorAll('iframe').forEach((frame) => {
                                            try { visit(frame.contentDocument); } catch (_) {}
                                          });
                                        } catch (_) {}
                                      };
                                      visit(document);
                                      let best = null;
                                      let bestArea = 0;
                                      for (const doc of docs) {
                                        try {
                                          doc.querySelectorAll('video').forEach((node) => {
                                            try {
                                              const r = node.getBoundingClientRect();
                                              const area = r.width * r.height;
                                              if (r.width < 16 || r.height < 16) return;
                                              if (area > bestArea) { bestArea = area; best = node; }
                                            } catch (_) {}
                                          });
                                        } catch (_) {}
                                      }
                                      if (!best) { return { clicked: false }; }
                                      try { best.scrollIntoView({block: 'center'}); } catch (_) {}
                                      const seq = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
                                      for (const type of seq) {
                                        try {
                                          best.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true }));
                                        } catch (_) {}
                                      }
                                      try { best.click(); } catch (_) {}
                                      try { best.play && best.play().catch(() => {}); } catch (_) {}
                                      return { clicked: true, tag: 'video' };
                                    })();
                                    """
                                )
                            except Exception:
                                pass
                except Exception:
                    pass

            threading.Thread(
                target=serve_stdin, name="vrka-browser-stdin", daemon=True,
            ).start()

        def start_media_watcher():
            if not protected:
                return

            def watch():
                try:
                    while not allow_close.is_set() and not payload_written.is_set() \
                            and not session_done.is_set():
                        try:
                            playable = bool(window.evaluate_js(
                                """
                                (() => {
                                  const documents = [];
                                  const visit = (doc) => {
                                    if (!doc || documents.includes(doc)) return;
                                    documents.push(doc);
                                    try {
                                      doc.querySelectorAll('iframe').forEach((frame) => {
                                        try { visit(frame.contentDocument); } catch (_) {}
                                      });
                                    } catch (_) {}
                                  };
                                  visit(document);
                                  let playable = false;
                                  documents.forEach((doc) => {
                                    try {
                                      const page = doc.defaultView;
                                      if (!page.__vrkaMediaPlayable) page.__vrkaMediaPlayable = false;
                                      const mark = () => { page.__vrkaMediaPlayable = true; };
                                      doc.querySelectorAll('video,audio').forEach((node) => {
                                        if (node.__vrkaPlayableWatched) return;
                                        node.__vrkaPlayableWatched = true;
                                        ['canplay', 'playing', 'loadeddata'].forEach((event) => {
                                          try { node.addEventListener(event, mark); } catch (_) {}
                                        });
                                        try {
                                          if (node.readyState >= 3 && (node.currentSrc || node.src)) mark();
                                        } catch (_) {}
                                      });
                                      playable = playable || Boolean(page.__vrkaMediaPlayable);
                                    } catch (_) {}
                                  });
                                  return playable;
                                })();
                                """
                            ))
                        except Exception:
                            playable = False
                        if playable and not payload_written.is_set():
                            capture_session()
                            return
                        time.sleep(0.5)
                except Exception:
                    pass

            threading.Thread(
                target=watch, name="vrka-media-watcher", daemon=True,
            ).start()

        window.events.loaded += install_observer
        window.events.closing += handle_closing
        if protected:
            start_stdin_worker()
            start_media_watcher()

        # Install the native session guard (new-window firewall + uBOL) as
        # soon as the WebView2 browser view exists, then navigate to the
        # requested page only after uBOL is ready: the target site's first
        # document request must run under the filter.  The loaded-event install
        # remains as a fallback for views that appear later.  Fail-open by
        # design (each attempt is exception-safe; navigation proceeds even if
        # the guard or uBOL never becomes ready).
        def navigate_to_requested_page():
            try:
                browser_view = window.gui.BrowserView.instances.get(window.uid)
                if browser_view is None:
                    window.load_url(start_url)
                    return
                from System import Action

                def _go():
                    try:
                        window.load_url(start_url)
                    except Exception:
                        pass

                if browser_view.InvokeRequired:
                    browser_view.Invoke(Action(_go))
                else:
                    _go()
            except Exception:
                try:
                    window.load_url(start_url)
                except Exception:
                    pass

        def install_session_guard_when_ready():
            deadline = time.time() + 90
            installed = False
            while (
                time.time() < deadline
                and not installed
                and not allow_close.is_set()
            ):
                try:
                    if install_webview2_session_guard():
                        installed = True
                except Exception:
                    pass
                if not installed:
                    time.sleep(0.25)
            if allow_close.is_set():
                return
            # Bounded uBOL readiness gate: the requested page is not navigated
            # to until the extension is installed (or its install failed /
            # is unavailable, both of which also set the event).  After the
            # install completes, its service worker still needs a moment to
            # register the declarative-net-request rulesets; without this
            # settle the FIRST document could load before the block rules are
            # live.  Bounded and fail-open: navigation always proceeds.
            if ubol_dir:
                ubol_ready.wait(timeout=60)
                if popup_stats.get("ubol"):
                    time.sleep(UBOL_DNR_WARMUP_SECONDS)
            navigate_to_requested_page()

        threading.Thread(
            target=install_session_guard_when_ready,
            name="vrka-session-guard", daemon=True,
        ).start()

        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(profile_path),
        )
        if not result_path.exists():
            payload["error"] = "The verification window closed before session capture completed."
            _atomic_write_json(result_path, payload)
    except Exception as exc:
        # Teardown noise (e.g. a late API call racing the closing window) must
        # never overwrite evidence that already captured successfully.
        if not payload.get("ok"):
            payload["error"] = f"{type(exc).__name__}: {exc}"
            _atomic_write_json(result_path, payload)
    finally:
        session_done.set()
        try:
            browser_events.put_nowait(None)
        except queue.Full:
            pass
        observation_thread.join(timeout=2)
    return 0 if payload.get("ok") else 1

def run_protected_browser_helper(start_url, result_path):
    """CLI helper used only by automatic same-task Browser Fallback."""
    return run_browser_verification_helper(start_url, result_path, protected=True)

def sanitize_command_for_log(command):
    """Render a command for the activity log while masking common secrets."""
    sensitive_options = {
        "--cookies", "--username", "--password", "--video-password", "--proxy",
        "--netrc-location", "--client-certificate", "--client-certificate-key",
        "--client-certificate-password",
    }
    sanitized = []
    hide_next = False
    header_next = False
    for argument in command:
        text = str(argument)
        if hide_next:
            sanitized.append("<redacted>")
            hide_next = False
            continue
        if header_next:
            name, separator, _value = text.partition(":")
            if separator and name.strip().lower() in SENSITIVE_HANDOFF_HEADER_NAMES:
                sanitized.append(name + ":<redacted>")
            else:
                sanitized.append(text)
            header_next = False
            continue
        if text in sensitive_options:
            sanitized.append(text)
            hide_next = True
            continue
        if text == "--add-header":
            sanitized.append(text)
            header_next = True
            continue
        matched = next((option for option in sensitive_options if text.startswith(option + "=")), None)
        sanitized.append(f"{matched}=<redacted>" if matched else text)
    if os.name == "nt":
        return subprocess.list2cmdline(sanitized)
    return shlex.join(sanitized)

def control_value(owner, attribute, default=None):
    """Read a UI control defensively for migration/tests and partial startup recovery."""
    control = getattr(owner, attribute, None)
    try:
        return control.get()
    except Exception:
        return default


class YTDLPCommandError(Exception):
    def __init__(self, message, category="unknown", output="", prior_categories=()):
        super().__init__(message)
        self.category = category
        self.output = output
        self.prior_categories = tuple(prior_categories or ())


BROWSER_RECOVERABLE_DIRECT_CATEGORIES = frozenset({
    "cloudflare", "cookies", "expired", "http",
})


TERMINAL_DIRECT_CATEGORIES = frozenset({"drm", "impersonation"})


_GENERIC_EXTRACTOR_FETCH_MARKERS = (
    "falling back on generic information extractor",
    "downloading webpage",
    "extracting information",
)


def _unsupported_failure_fetched_a_page(exc):
    """True when an Unsupported-URL failure still fetched a real page.

    yt-dlp reports ``ERROR: Unsupported URL`` both for genuinely invalid
    input and for real JS-driven pages whose media the generic extractor
    cannot read - the latter is exactly the browser-fallback case.  The
    generic extractor visibly fetched/parsed the page in that case
    (``Falling back on generic information extractor``, ``Downloading
    webpage``, ``Extracting information``); without that evidence the
    failure stays terminal.
    """
    output = str(getattr(exc, "output", "") or "").lower()
    return any(marker in output for marker in _GENERIC_EXTRACTOR_FETCH_MARKERS)


_TRANSFER_STARTED_MARKERS = ("__vrka_title__", "[download] destination:")


def _transfer_failure_after_resolution(exc):
    """True when the direct run already resolved the media and began a real
    transfer before failing.

    The standard command prints ``before_dl:__VRKA_TITLE__...`` (and/or
    ``[download] Destination:``) the moment yt-dlp resolves the media and
    starts the actual download.  A failure that follows that marker is a
    post-extraction transfer failure (e.g. the media CDN returns HTTP 403
    for a YouTube video that was already resolved), NOT a page-access
    failure: it must never automatically route the task to Browser Fallback
    merely because it is enabled.  Direct controls (YouTube/X/Instagram)
    stay on the direct path and recover with their own retry rules.
    """
    output = str(getattr(exc, "output", "") or "").lower()
    return any(marker in output for marker in _TRANSFER_STARTED_MARKERS)


def direct_failure_is_browser_recoverable(exc):
    """True when a fast direct-path failure can be recovered by Browser Fallback.

    A failure after the requested media was resolved and a real transfer
    began is never a page-access failure and never fallback-eligible.
    """
    if _transfer_failure_after_resolution(exc):
        return False
    """True when a fast direct-path failure can be recovered by Browser Fallback.

    Generic recovery classification: categories that prove the URL is a real,
    browser-reachable page (Cloudflare challenge, cookie wall, HTTP-level
    rejection, expired media address) are eligible.  ``unsupported`` is
    eligible when the same attempt chain saw a browser-relevant first error
    (e.g. HTTP 403 then ``Unsupported URL`` after the impersonation retry) OR
    when the generic extractor visibly fetched the page before giving up (a
    JS-driven page that returned HTTP 200 but has no extractable media).  A
    bare ``Unsupported URL`` with no fetch evidence - genuinely invalid input
    - stays terminal, as do ``drm`` and impersonation-mechanism failures.
    """
    category = getattr(exc, "category", "unknown")
    if category in TERMINAL_DIRECT_CATEGORIES:
        return False
    if category in BROWSER_RECOVERABLE_DIRECT_CATEGORIES:
        return True
    if category == "unsupported" and _unsupported_failure_fetched_a_page(exc):
        return True
    prior = tuple(getattr(exc, "prior_categories", ()) or ())
    return bool(prior) and any(
        item in BROWSER_RECOVERABLE_DIRECT_CATEGORIES for item in prior
    )


def classify_download_error(message):
    """Classify common site failures without claiming more than the log proves."""
    text = str(message or "")
    lowered = text.lower()
    rules = (
        ("drm", ("drm protected", "protected by drm", "digital rights management")),
        ("cloudflare", ("cloudflare", "cf-chl-", "just a moment...", "attention required")),
        ("impersonation", ("impersonate", "curl_cffi", "unsupported impersonation target")),
        (
            "cookies",
            (
                "cookies-from-browser",
                "sign in to confirm",
                "login required",
                "could not find chrome",
                "could not find edge",
                "could not find firefox",
                "could not find brave",
                "could not copy chrome cookie database",
                "could not copy edge cookie database",
                "could not copy firefox cookie database",
                "could not copy brave cookie database",
                "database is locked",
                "failed to decrypt",
                "cookie decryption",
                "browser must be closed",
                "no useful cookies",
                "no cookies",
            ),
        ),
        ("expired", ("url has expired", "expired url", "signature has expired")),
        ("timeout", ("timed out", "timeout", "read operation timed out")),
        ("unsupported", ("unsupported url", "no suitable extractor", "not a valid url")),
        (
            "http",
            (
                "http error",
                "403 forbidden",
                "unable to download webpage",
                "connection reset",
            ),
        ),
    )
    for category, needles in rules:
        if any(needle in lowered for needle in needles):
            return category
    return "unknown"


def format_download_error(message):
    category = classify_download_error(message)
    guidance = {
        "drm": (
            "This media appears to be DRM-protected. VRKA will not bypass DRM; "
            "use a lawful non-DRM source."
        ),
        "cloudflare": (
            "The site returned a Cloudflare verification response. Browser "
            "impersonation, cookies, or the on-demand verification window may help."
        ),
        "impersonation": (
            "The selected browser impersonation target is unavailable in this yt-dlp build."
        ),
        "cookies": (
            "The site appears to require an authenticated browser session or valid cookies."
        ),
        "expired": "The media address appears to have expired. Refresh the page and try again.",
        "timeout": "The site did not respond in time. Check the connection and try again.",
        "unsupported": "This address is not supported by the active yt-dlp build.",
        "http": "The website rejected or interrupted the request.",
    }
    return category, guidance.get(category, str(message))


def probe_failure_overridden_by_browser_observation(bundle, category):
    """True when the live browser already proved this exact media URL works.

    The strongest possible validation evidence is the protected browser
    itself having fetched this URL with HTTP 200 moments ago under the full
    session context.  When a replay probe then fails with a context-bound
    category (Cloudflare challenge, expired signature, transient HTTP
    rejection), the observation outweighs the replay: proceed to the real
    transfer, whose existing start/flow gates still reject dead URLs.
    """
    try:
        observed = int(getattr(bundle, "observed_status", 0) or 0)
    except (TypeError, ValueError):
        observed = 0
    return observed == 200 and category in ("cloudflare", "expired", "http")


def external_replay_rejected_by_server(override_credit, transfer_error):
    """True when a browser-credited candidate was refused by the media
    server's edge during the external transfer replay itself.

    The override above proves the candidate is browser-accessible; a
    context-bound transfer category then proves the independent HTTP
    client cannot reproduce the browser-authenticated request.  That is a
    TRANSFER limitation and must be classified as such instead of decaying
    the candidate into generic invalidity."""
    if not override_credit:
        return False
    category = str(getattr(transfer_error, "category", "") or "")
    return category in ("cloudflare", "expired", "http")


def _has_cli_option(arguments, *option_names):
    names = set(option_names)
    return any(
        argument in names
        or any(str(argument).startswith(name + "=") for name in names)
        for argument in arguments
    )


def _append_request_context_arguments(arguments, opts, candidate=None):
    """Attach the minimum browser/session context required by a media request."""
    candidate_headers = media_candidate_headers(candidate)
    if not candidate_headers and opts.get("resolved_media_headers"):
        candidate_headers = _handoff_headers(opts.get("resolved_media_headers") or {})
    use_session_defaults = opts.get("cookie_mode") == "session"

    user_agent = _case_insensitive_header(candidate_headers, "user-agent")
    referer = _case_insensitive_header(candidate_headers, "referer")
    origin = _case_insensitive_header(candidate_headers, "origin")
    if use_session_defaults:
        user_agent = user_agent or str(opts.get("session_user_agent") or "")
        referer = referer or str(opts.get("session_referer") or "")
        origin = origin or str(opts.get("session_origin") or "")
    if user_agent:
        arguments += ["--user-agent", user_agent]
    if referer:
        arguments += ["--referer", referer]
    if origin:
        arguments += ["--add-header", "Origin:" + origin]

    excluded = {"user-agent", "referer", "origin"}
    for name, value in candidate_headers.items():
        if str(name).lower() not in excluded and value not in (None, ""):
            arguments += ["--add-header", f"{name}:{value}"]
    return arguments

def _standard_ytdlp_arguments(
    task,
    output_folder,
    format_override=None,
    download_section=None,
    extra_arguments=None,
):
    """One authoritative translation from VRKA controls to yt-dlp CLI flags."""
    opts = task.options
    template = validate_output_template(opts.get("output_template"))
    resolved_title = str(opts.get("resolved_media_title") or "").strip()
    if resolved_title and "%(title)s" in template:
        template = template.replace("%(title)s", resolved_title)
    title_marker = "__VRKA_TITLE__"
    output_marker = "__VRKA_OUTPUT__"
    args = [
        "--ignore-config",
        "--newline",
        "--progress",
        "--progress-delta", "0.2",
        "--print", f"before_dl:{title_marker}%(title)s",
        "--print", f"after_move:{output_marker}%(filepath)s",
        "--no-overwrites",
        "--part",
        "--trim-filenames", str(MAX_FILENAME_CHARS),
        "-P", f"home:{output_folder}",
        "-P", f"temp:{opts.get('_staging_dir') or STAGING_DIR / str(uuid.uuid4())}",
        "-o", template,
    ]
    if os.name == "nt":
        args.append("--windows-filenames")

    if opts.get("is_playlist"):
        if str(opts.get("playlist_start") or "").isdigit():
            args += ["--playlist-start", str(int(opts["playlist_start"]))]
        if str(opts.get("playlist_end") or "").isdigit():
            args += ["--playlist-end", str(int(opts["playlist_end"]))]
    else:
        args.append("--no-playlist")

    if task.mode == "audio":
        codec = AUDIO_FORMAT_MAP.get(opts.get("audio_format"), "mp3")
        args += ["-f", format_override or "bestaudio/best", "--extract-audio", "--audio-format", codec]
        if codec == "mp3":
            bitrate = MP3_BITRATE_MAP.get(opts.get("mp3_bitrate"), "320K")
            args += ["--audio-quality", bitrate]
        else:
            args += ["--audio-quality", "0"]
        if opts.get("embed_thumbnail"):
            args.append("--embed-thumbnail")
        if opts.get("embed_metadata"):
            args.append("--embed-metadata")
    else:
        height = QUALITY_MAP.get(opts.get("quality"))
        format_selector = format_override or build_video_format(
            height, opts.get("fps60", False)
        )
        args += ["-f", format_selector, "--merge-output-format", "mp4"]
        if opts.get("embed_metadata"):
            args.append("--embed-metadata")

    if opts.get("download_subs"):
        args.append("--write-subs")
        if opts.get("auto_captions"):
            args.append("--write-auto-subs")
        args += [
            "--sub-langs",
            opts.get("sub_langs") or DEFAULT_SUBTITLE_LANGUAGE_PATTERN,
        ]
        if opts.get("embed_subs") and task.mode != "audio":
            args.append("--embed-subs")

    cookie_mode = opts.get("cookie_mode", "none")
    if cookie_mode == "browser" and opts.get("cookie_browser"):
        browser_spec = str(opts["cookie_browser"]).lower()
        profile = str(opts.get("cookie_profile") or "").strip()
        if profile:
            browser_spec += ":" + profile
        args += ["--cookies-from-browser", browser_spec]
    elif cookie_mode == "file" and opts.get("cookie_file"):
        args += ["--cookies", str(opts["cookie_file"])]
    elif cookie_mode == "session" and opts.get("session_cookie_file"):
        args += ["--cookies", str(opts["session_cookie_file"])]
    _append_request_context_arguments(args, opts)

    if opts.get("proxy"):
        args += ["--proxy", str(opts["proxy"])]
    if opts.get("rate_limit"):
        args += ["--limit-rate", str(opts["rate_limit"])]
    if opts.get("force_ipv4"):
        args.append("--force-ipv4")
    if opts.get("restrict_filenames"):
        args.append("--restrict-filenames")
    if opts.get("format_sort"):
        args += ["--format-sort", str(opts["format_sort"])]

    if opts.get("use_archive"):
        archive_path, migrated = migrate_download_archive(output_folder)
        args += ["--download-archive", str(archive_path)]
        opts["_archive_migrated_from"] = migrated

    if opts.get("sponsorblock"):
        categories = opts.get("sponsorblock_categories") or "sponsor"
        args += ["--sponsorblock-remove", str(categories)]

    impersonation = str(opts.get("impersonation") or "Automatic").lower()
    target = {
        "chrome": "chrome",
        "firefox": "firefox",
        "safari": "safari",
    }.get(impersonation)
    if target:
        args += ["--impersonate", target]
    elif str(opts.get("_needs_impersonation") or "").lower() in ("chrome", "firefox", "safari"):
        # Reproduce the request-impersonation context the direct path
        # discovered for this site on the browser-handoff transfer, so a
        # Cloudflare-protected requested-media CDN does not fail with a bare
        # 403 after the browser has already proven the media is accessible.
        args += ["--impersonate", str(opts["_needs_impersonation"]).lower()]
        args += ["--extractor-args", "generic:impersonate"]

    if opts.get("allow_remote_components", True):
        args += ["--remote-components", "ejs:github"]

    ffmpeg_dir = get_bundled_ffmpeg_dir()
    if ffmpeg_dir:
        args += ["--ffmpeg-location", ffmpeg_dir]

    # FDM-style concurrency for fragmented media (HLS/DASH): yt-dlp's native
    # ``--concurrent-fragments`` downloads several fragments in parallel, which
    # is the transport-layer speedup for browser-fallback HLS masters.  Default
    # 4, bounded to 8; ignored by yt-dlp for non-fragmented transfers.
    try:
        concurrent = int(opts.get("concurrent_fragments") or 4)
    except (TypeError, ValueError):
        concurrent = 4
    concurrent = max(1, min(8, concurrent))
    if concurrent >= 2:
        args += ["--concurrent-fragments", str(concurrent)]

    # Optional aria2c transport backend for compatible workloads.  Engaged only
    # when the user opts in AND a real aria2c binary is discoverable; otherwise
    # the native yt-dlp transport (with concurrent fragments above) is used.
    if str(opts.get("transport_backend") or "").lower() == "aria2c":
        aria2c = _find_aria2c()
        if aria2c:
            args += [
                "--downloader", "aria2c",
                "--downloader-args",
                "aria2c:max-concurrent-downloads=1:min-split-size=1M:split=4:max-connection-per-server=4",
            ]

    if download_section:
        args += ["--download-sections", download_section, "--force-keyframes-at-cuts"]

    if extra_arguments:
        i = 0
        while i < len(extra_arguments):
            item = extra_arguments[i]
            if item == "--impersonate" and i + 1 < len(extra_arguments):
                val = extra_arguments[i + 1]
                if "--impersonate" not in args:
                    args += ["--impersonate", val]
                i += 2
                continue
            elif item == "--extractor-args" and i + 1 < len(extra_arguments):
                val = extra_arguments[i + 1]
                if val not in args:
                    args += ["--extractor-args", val]
                i += 2
                continue
            else:
                args.append(item)
                i += 1
    args.append(opts.get("resolved_media_url") or task.url)
    return args


def build_standard_ytdlp_command(
    task,
    output_folder,
    format_override=None,
    download_section=None,
    extra_arguments=None,
):
    backend = resolve_ytdlp_backend()
    arguments = _standard_ytdlp_arguments(
        task,
        output_folder,
        format_override=format_override,
        download_section=download_section,
        extra_arguments=extra_arguments,
    )
    return backend, list(backend.command) + arguments

def candidate_satisfies_task_mode(candidate, task_mode):
    """A handoff candidate must be self-contained for its task mode.

    Video tasks need BOTH audio and video in the resolved transfer: handing
    a video-only variant manifest produced silent files, and an audio-only
    manifest would produce no picture.  Audio tasks need audio only.
    Returns ``(ok, reason)`` with a redaction-safe reason string.
    """
    if task_mode != "video":
        return True, ""
    acodec = str((candidate or {}).get("probe_acodec") or "none").strip().lower()
    vcodec = str((candidate or {}).get("probe_vcodec") or "none").strip().lower()
    if acodec in ("", "none"):
        return False, "no-audio"
    if vcodec in ("", "none"):
        return False, "no-video"
    return True, ""


def candidate_probe_height(candidate):
    """Requested-candidate height from the probe, when resolvable."""
    try:
        value = int(float((candidate or {}).get("probe_height") or 0))
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def build_candidate_probe_command(task, candidate):
    """Build a bounded metadata-only probe without exposing session values to logs."""
    backend = resolve_ytdlp_backend()
    opts = task.options
    command = list(backend.command) + [
        "--ignore-config",
        "--simulate",
        "--no-playlist",
        "--no-warnings",
        "--print", "__VRKA_CANDIDATE__%(format_id)s|%(resolution)s|%(ext)s|"
                   "%(tbr)s|%(acodec)s|%(vcodec)s|%(height)s",
        "--print", "__VRKA_CANDIDATE_TITLE__%(title)s",
        "--print", "__VRKA_CANDIDATE_DURATION__%(duration_string)s",
    ]
    cookie_mode = opts.get("cookie_mode", "none")
    if cookie_mode == "browser" and opts.get("cookie_browser"):
        browser_spec = str(opts["cookie_browser"]).lower()
        profile = str(opts.get("cookie_profile") or "").strip()
        if profile:
            browser_spec += ":" + profile
        command += ["--cookies-from-browser", browser_spec]
    elif cookie_mode == "file" and opts.get("cookie_file"):
        command += ["--cookies", str(opts["cookie_file"])]
    elif cookie_mode == "session" and opts.get("session_cookie_file"):
        command += ["--cookies", str(opts["session_cookie_file"])]
    _append_request_context_arguments(command, opts, candidate)

    if opts.get("proxy"):
        command += ["--proxy", str(opts["proxy"])]
    if opts.get("force_ipv4"):
        command.append("--force-ipv4")
    impersonation = str(opts.get("impersonation") or "Automatic").lower()
    if impersonation in ("chrome", "firefox", "safari"):
        command += ["--impersonate", impersonation]
    elif str(opts.get("_needs_impersonation") or "").lower() in ("chrome", "firefox", "safari"):
        # The direct extraction path already discovered this site requires
        # browser request impersonation (e.g. Cloudflare).  Reproduce the same
        # legitimate browser context when validating the browser-handoff
        # candidate, otherwise a Cloudflare-protected requested-media CDN
        # would fail validation with a bare 403 and the fallback could
        # substitute an unrelated playable stream.
        command += ["--impersonate", str(opts["_needs_impersonation"]).lower()]
        command += ["--extractor-args", "generic:impersonate"]
    command.append(media_candidate_url(candidate))
    return backend, command

def build_custom_ytdlp_command(task, output_folder):
    """Custom mode shares the same selected runtime and essential safe defaults."""
    opts = task.options
    backend = resolve_ytdlp_backend()
    try:
        arguments = validate_custom_ytdlp_arguments(
            shlex.split(opts.get("custom_command", ""))
        )
    except ValueError as exc:
        raise ValueError(f"Could not parse custom command: {exc}") from exc

    command = list(backend.command) + [
        "--ignore-config",
        "--newline",
        "--progress",
        "--progress-delta", "0.2",
        "--print", "before_dl:__VRKA_TITLE__%(title)s",
        "--print", "after_move:__VRKA_OUTPUT__%(filepath)s",
        "--no-overwrites",
        "--part",
        "--trim-filenames", str(MAX_FILENAME_CHARS),
        "-P", f"home:{output_folder}",
        "-P", f"temp:{opts.get('_staging_dir') or STAGING_DIR / str(uuid.uuid4())}",
        "-o", validate_output_template(opts.get("output_template")),
    ]
    if os.name == "nt":
        command.append("--windows-filenames")
    if opts.get("allow_remote_components", True) and not _has_cli_option(
        arguments, "--remote-components"
    ):
        command += ["--remote-components", "ejs:github"]
    ffmpeg_dir = get_bundled_ffmpeg_dir()
    if ffmpeg_dir:
        command += ["--ffmpeg-location", ffmpeg_dir]
    command += arguments
    command.append(task.url)
    return backend, command


@dataclass
class DownloadTask:
    id: str
    url: str
    mode: str  # 'video' | 'audio' | 'custom'
    options: dict
    status: str = "queued"  # queued, downloading, completed, error, canceled
    progress: float = 0.0
    title: str = ""
    output_path: str = ""
    error: str = ""
    process: object = None
    stage: str = "Waiting"
    speed: str = ""
    eta: str = ""


class QueueLogger:
    """A yt-dlp compatible logger that pushes messages into the UI queue,
    prefixed with the task's title/url for readability in a shared log."""

    def __init__(self, ui_queue, task):
        self.ui_queue = ui_queue
        self.task = task

    def _label(self):
        return self.task.title or self.task.url

    def debug(self, msg):
        if msg.startswith("[debug] "):
            return
        self.ui_queue.put(("log", f"[{self._label()}] {normalize_subtitle_message(msg)}"))

    def info(self, msg):
        self.ui_queue.put(("log", f"[{self._label()}] {normalize_subtitle_message(msg)}"))

    def warning(self, msg):
        self.ui_queue.put(("log", f"[{self._label()}] WARNING: {normalize_subtitle_message(msg)}"))

    def error(self, msg):
        self.ui_queue.put(("log", f"[{self._label()}] ERROR: {msg}"))


def make_progress_hook(ui_queue, task, cancel_event):
    """Creates a yt-dlp progress_hook bound to a specific task."""

    def hook(d):
        if cancel_event.is_set():
            raise DownloadCanceled()

        status = d.get("status")
        info_dict = d.get("info_dict") or {}
        title = info_dict.get("title")
        if title and not task.title:
            task.title = title
            ui_queue.put(("task_title", task.id, title))

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                percent = downloaded / total
                task.progress = percent
                ui_queue.put(("task_progress", task.id, percent))
                percent_str = f"{percent * 100:.1f}%"
            else:
                percent_str = "..."
            speed = d.get("speed")
            speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "N/A"
            ui_queue.put(("log", f"[{task.title or task.url}] {percent_str} | {speed_str}"))
        elif status == "finished":
            task.output_path = d.get("filename", task.output_path)
            ui_queue.put(("log", f"[{task.title or task.url}] Post-processing (merge/convert/trim)..."))

    return hook


def make_postprocessor_hook(ui_queue, task):
    """Tracks the true final output path once yt-dlp's own postprocessors
    (audio extraction, metadata, thumbnail embedding, etc.) have finished.
    The progress_hook only knows the pre-postprocessing filename, so this
    is what makes local trimming target the actual final file."""

    def hook(d):
        if d.get("status") == "finished":
            info = d.get("info_dict") or {}
            filepath = info.get("filepath") or d.get("filename")
            if filepath:
                task.output_path = filepath

    return hook


# ----------------------------------------------------------------------
# Main application — Tk UI is legacy; QML uses pure backend via EngineHost.
# ----------------------------------------------------------------------

_VRKABase = ctk.CTk if (not _IS_QML_STARTUP and ctk is not None) else object  # type: ignore

class VRKADownloader(_VRKABase):  # type: ignore
    def __init__(self):
        if _IS_QML_STARTUP:
            # Minimal backend shim for QML delegation — no Tk window.
            self.ui_queue = queue.Queue()
            self.tasks = []  # type: ignore
            self.tasks_lock = threading.Lock()
            self.cancel_events = {}
            self.output_folder = os.path.join(os.path.expanduser("~"), "Downloads")
            self._verified_session = {}
            self._browser_verification_process = None
            self._pending_browser_retry_url = ""
            self.history = []  # type: ignore
            self.tasks = []  # type: ignore
            return
        configure_typography_defaults()
        super().__init__()  # type: ignore
        try:
            self.option_add("*Font", f"{{{_ACTIVE_UI_FONT_FAMILY}}} {FONT_BODY}")
        except Exception:
            pass

        self.title(f"{APP_NAME} - Video Downloader")
        self.geometry("1240x820")
        self.minsize(1020, 700)
        # Note: deliberately NOT calling self.attributes("-alpha", ...) here.
        # An earlier version did, to counteract shell mods like Windhawk's
        # "Translucent Windows" forcing transparency - but setting ANY alpha
        # value (even fully-opaque 1.0) makes Windows treat this as a layered
        # window, which can make minimize/maximize/restore animations choppy
        # since it can bypass the normal hardware-accelerated DWM path on some
        # configurations. That's a bad trade for a benefit only a small subset
        # of users needed, and those users have a better fix already: exclude
        # VRKA.exe in the shell mod's own per-app settings.

        self._apply_window_icon()

        self.ui_queue = queue.Queue()
        self._log_line_count = 0
        self._closing = False
        self._history_filter_after_id = None
        self._last_history_filter = None
        self._history_view_dirty = True
        self._history_visible_limit = HISTORY_PAGE_SIZE
        self._deferred_log_messages = []
        self._theme_animation_after_ids = []
        self._current_page = None
        self.output_folder = os.path.join(os.path.expanduser("~"), "Downloads")

        self.tasks = []
        self.tasks_lock = threading.Lock()
        self.cancel_events = {}
        self._shutdown_event = threading.Event()
        self._queue_wakeup = threading.Event()
        self.task_widgets = {}
        self._verified_session = {}
        self._browser_verification_process = None
        self._pending_browser_retry_url = ""

        self._migrated_files = migrate_legacy_app_data()
        startup_settings = self.load_settings()
        self._startup_appearance_mode = (
            "Light"
            if str(startup_settings.get("appearance_mode", "Dark")).lower() == "light"
            else "Dark"
        )
        ctk.set_appearance_mode(self._startup_appearance_mode)
        self.history = self.load_history()

        self.build_ui()
        self._refresh_stats()
        self.apply_settings(startup_settings)

        if self._migrated_files:
            migrated_names = ", ".join(self._migrated_files)
            self.ui_queue.put(("log", f"Imported existing Seal Desktop data: {migrated_names}"))

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._ui_queue_after_id = self.after(UI_QUEUE_INTERVAL_MS, self.process_ui_queue)
        self._ffmpeg_after_id = self.after(400, self.check_ffmpeg)

        self._protected_browser_launcher = SubprocessBrowserLauncher(
            BROWSER_SESSION_DIR,
            self._protected_browser_command,
        )
        self._core_adapter = Build008TaskAdapter(
            APP_DATA_DIR / "build010_tasks.json",
            self._resolve_core_task,
            self._execute_core_task,
            self.ui_queue,
            visible=self._show_core_task,
            history=self.add_history_entry,
            auto_start=False,
        )
        self._core_adapter.restore_existing()
        self._core_adapter.scheduler.start()
        # The durable core scheduler is the single queue worker for build010.
        self._queue_worker_thread = None
        if should_check_ytdlp_on_startup(startup_settings):
            channel = startup_settings.get("ytdlp_channel", DEFAULT_YTDLP_CHANNEL)
            self._set_runtime_controls(False)
            threading.Thread(
                target=self._startup_runtime_update,
                args=(channel,),
                daemon=True,
            ).start()

    def _apply_window_icon(self):
        """Install the canonical wolf without adding another timer.

        CustomTkinter schedules its own Windows icon callback during CTk
        construction. Keeping this helper as the implementation for both the
        immediate call and the overridden callback prevents CTk's stock icon
        from replacing VRKA's icon roughly 200 ms after startup.
        """
        if os.name == "nt":
            try:
                ico_path = resource_path(Path("assets") / "branding" / "vrka.ico")
                if ico_path.is_file():
                    self.iconbitmap(default=str(ico_path))
            except Exception:
                _write_crash_log(f"Window ICO fallback failed (non-fatal):\n{traceback.format_exc()}")
        if not _HAS_IMAGETK:
            return
        try:
            if not hasattr(self, "_app_icon_photo"):
                self._app_icon_photo = ImageTk.PhotoImage(build_app_icon_image(256))
            self.iconphoto(True, self._app_icon_photo)
        except Exception:
            _write_crash_log(f"Window icon setup failed (non-fatal):\n{traceback.format_exc()}")

    def _windows_set_titlebar_icon(self):
        self._apply_window_icon()

    def _terminate_process_tree(self, process):
        """Stop only the tracked helper tree; never scan or kill unrelated browsers."""
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                process.terminate()
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
    def _on_close(self):
        if self._closing:
            return
        self._closing = True
        self._shutdown_event.set()
        self._queue_wakeup.set()
        adapter = getattr(self, "_core_adapter", None)
        if adapter is not None:
            adapter.shutdown(timeout=1.5)
        for event in self.cancel_events.values():
            event.set()
        with self.tasks_lock:
            active_processes = [task.process for task in self.tasks if task.process]
        for process in active_processes:
            self._terminate_process_tree(process)
        self._terminate_process_tree(
            getattr(self, "_browser_verification_process", None)
        )
        worker = getattr(self, "_queue_worker_thread", None)
        worker_is_alive = getattr(worker, "is_alive", None)
        if (
            worker
            and callable(worker_is_alive)
            and worker_is_alive()
            and worker is not threading.current_thread()
        ):
            worker.join(timeout=1.5)
        for after_id in (
            getattr(self, "_ui_queue_after_id", None),
            getattr(self, "_ffmpeg_after_id", None),
            getattr(self, "_history_filter_after_id", None),
            *getattr(self, "_theme_animation_after_ids", []),
        ):
            if after_id:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
        for sensitive_file in (
            BROWSER_SESSION_DIR / "verified-session-cookies.txt",
        ):
            try:
                sensitive_file.unlink()
            except OSError:
                pass
        self.save_settings()
        self.destroy()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self):
        self.configure(fg_color=COLOR_BG)

        root_container = layout_frame(self, bg_color=COLOR_BG)
        root_container.pack(fill="both", expand=True)
        root_container.rowconfigure(0, weight=1)
        root_container.columnconfigure(1, weight=1)

        self._build_sidebar(root_container)

        self.content_area = layout_frame(root_container, bg_color=COLOR_BG)
        self.content_area.grid(row=0, column=1, sticky="nsew")

        self.pages = {}
        for name in ("Download", "Queue", "History", "Settings"):
            page = layout_frame(self.content_area, bg_color=COLOR_BG)
            self.pages[name] = page

        self._build_download_tab(self.pages["Download"])
        self._build_queue_tab(self.pages["Queue"])
        self._build_history_tab(self.pages["History"])
        self._build_settings_tab(self.pages["Settings"])

        self.show_page("Download")

    def _build_sidebar(self, parent):
        sidebar = ctk.CTkFrame(parent, width=SIDEBAR_WIDTH, fg_color=COLOR_SIDEBAR, corner_radius=0,
                               border_width=0)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        ctk.CTkFrame(sidebar, fg_color=COLOR_BORDER, width=1).place(relx=1.0, rely=0, relheight=1)

        # Sidebar groups stay CTk-backed because transparent CTk children can
        # repaint incompletely when their immediate parent is a native Tk
        # frame. This small, fixed set of canvases prevents intermittent
        # missing brand/nav/footer regions without restoring heavy page-wide
        # nested CTk containers.
        brand = ctk.CTkFrame(sidebar, fg_color="transparent", corner_radius=0, border_width=0)
        brand.pack(fill="x", padx=22, pady=(28, 10))
        self.sidebar_brand_image = get_brand_ctk_image(48)
        ctk.CTkLabel(brand, text="", image=self.sidebar_brand_image).pack(side="left", padx=(0, 12))
        title_box = ctk.CTkFrame(brand, fg_color="transparent", corner_radius=0, border_width=0)
        title_box.pack(side="left")
        ctk.CTkLabel(title_box, text=APP_NAME, font=ui_font(22, "bold"),
                     text_color=COLOR_TEXT).pack(anchor="w")

        ctk.CTkFrame(sidebar, fg_color=COLOR_BORDER, height=1).pack(fill="x", padx=20, pady=(17, 17))
        ctk.CTkLabel(sidebar, text="NAVIGATION", font=mono_font(FONT_MICRO, "bold"),
                     text_color=COLOR_TEXT_DIM).pack(anchor="w", padx=22, pady=(0, 8))

        nav_items = [
            ("Download", "download"),
            ("Queue", "list"),
            ("History", "clock"),
            ("Settings", "gear"),
        ]
        self.nav_buttons = {}
        self.nav_icon_names = {}
        self.nav_indicators = {}
        for name, icon_name in nav_items:
            nav_row = ctk.CTkFrame(
                sidebar, height=NAV_BUTTON_HEIGHT, fg_color="transparent",
                corner_radius=0, border_width=0,
            )
            nav_row.pack(fill="x", padx=12, pady=3)
            nav_row.pack_propagate(False)
            indicator = ctk.CTkFrame(nav_row, width=3, height=22, corner_radius=2,
                                     fg_color=COLOR_SIDEBAR)
            indicator.pack(side="left", padx=(0, 5))
            btn = ctk.CTkButton(
                nav_row, text=f"  {name}", anchor="w", height=NAV_BUTTON_HEIGHT, corner_radius=CONTROL_RADIUS,
                font=ui_font(FONT_BODY), fg_color="transparent", text_color=COLOR_TEXT_MUTED,
                hover_color=COLOR_SURFACE_HOVER, image=get_icon(icon_name, 16, COLOR_TEXT_MUTED),
                command=lambda n=name: self.show_page(n),
            )
            btn.pack(side="left", fill="x", expand=True)
            self.nav_buttons[name] = btn
            self.nav_icon_names[name] = icon_name
            self.nav_indicators[name] = indicator

        footer = ctk.CTkFrame(
            sidebar, fg_color=COLOR_SIDEBAR, corner_radius=0, border_width=0,
        )
        footer.pack(side="bottom", fill="x", padx=20, pady=16)
        self.uplink_status_label = ctk.CTkLabel(
            footer, text="● UPLINK READY", font=mono_font(FONT_MICRO, "bold"),
            text_color=COLOR_SUCCESS, justify="left", anchor="w")
        self.uplink_status_label.pack(fill="x", padx=12, pady=(11, 0))
        ctk.CTkLabel(
            footer, text=f"v{APP_VERSION}  /  BUILD {APP_BUILD}", font=mono_font(FONT_MICRO, "bold"),
            text_color=COLOR_TEXT_DIM, justify="left", anchor="w").pack(fill="x", padx=12, pady=(2, 9))
        layout_frame(footer, bg_color=COLOR_BORDER, height=1).pack(fill="x", pady=(0, 8))
        self.sidebar_stats_label = ctk.CTkLabel(
            footer, text="", font=mono_font(FONT_MICRO), text_color=COLOR_TEXT_MUTED,
            justify="left", anchor="w")
        self.sidebar_stats_label.pack(fill="x", padx=12, pady=(0, 11))

        theme_row = ctk.CTkFrame(
            sidebar, fg_color=COLOR_SIDEBAR, corner_radius=0, border_width=0,
        )
        theme_row.pack(side="bottom", fill="x", padx=20, pady=(0, 4))
        starts_light = getattr(self, "_startup_appearance_mode", "Dark") == "Light"
        self.theme_var = tk.BooleanVar(value=starts_light)
        mode_name = "Light" if starts_light else "Dark"
        self.theme_button = ctk.CTkButton(
            theme_row, text=f"{mode_name} mode", width=148, height=38,
            image=get_icon("sun" if starts_light else "moon", 17, COLOR_TEXT_MUTED),
            compound="left", command=self._toggle_theme,
            font=ui_font(FONT_SMALL), anchor="w",
            corner_radius=CONTROL_RADIUS, fg_color="transparent",
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT_MUTED,
        )
        self.theme_button.pack(anchor="w")

    def _walk_widgets(self, root):
        yield root
        for child in root.winfo_children():
            yield from self._walk_widgets(child)

    def _apply_theme(self, mode):
        mode = "Light" if str(mode).lower() == "light" else "Dark"
        for widget in self._walk_widgets(self):
            token = getattr(widget, "_vrka_bg_token", None)
            if token is not None:
                try:
                    widget.configure(bg=appearance_color(token, mode))
                except Exception:
                    pass
        ctk.set_appearance_mode(mode)
        if hasattr(self, "theme_var"):
            self.theme_var.set(mode == "Light")
        if hasattr(self, "theme_button"):
            self.theme_button.configure(
                text=f"{mode} mode",
                image=get_icon("sun" if mode == "Light" else "moon", 17, COLOR_TEXT_MUTED),
                state="normal",
            )
        if hasattr(self, "log_textbox"):
            try:
                self.log_textbox._textbox.tag_configure(
                    "info", foreground=appearance_color(COLOR_TEXT_MUTED, mode)
                )
                self.log_textbox._textbox.tag_configure("warning", foreground=COLOR_WARNING)
                self.log_textbox._textbox.tag_configure("error", foreground=COLOR_ERROR)
            except Exception:
                pass

    def _toggle_theme(self):
        if self._theme_animation_after_ids:
            return
        current = "Light" if self.theme_var.get() else "Dark"
        target = "Dark" if current == "Light" else "Light"
        self.theme_button.configure(
            state="disabled",
            image=get_icon("sun" if current == "Light" else "moon", 17, COLOR_TEXT_DIM),
        )
        midpoint_id = self.after(
            THEME_TOGGLE_MIDPOINT_MS,
            lambda: self.theme_button.configure(
                text=f"{target} mode",
                image=get_icon("sun" if target == "Light" else "moon", 17, COLOR_TEXT_DIM),
            ),
        )
        finish_id = self.after(
            THEME_TOGGLE_DURATION_MS,
            lambda: self._finish_theme_toggle(target),
        )
        self._theme_animation_after_ids = [midpoint_id, finish_id]

    def _finish_theme_toggle(self, target):
        self._theme_animation_after_ids = []
        self._apply_theme(target)
        self.save_settings()

    def show_page(self, name):
        page = self.pages.get(name)
        if page is None:
            return
        previous = self.pages.get(self._current_page)
        if previous is not None and previous is not page and previous.winfo_manager():
            previous.place_forget()
        if not page.winfo_manager():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._current_page = name
        for pname, btn in self.nav_buttons.items():
            icon_name = self.nav_icon_names[pname]
            if pname == name:
                btn.configure(fg_color=COLOR_ACCENT_SOFT, hover_color=COLOR_ACCENT_SOFT_HOVER,
                              text_color=COLOR_TEXT, image=get_icon(icon_name, 16, COLOR_ACCENT_HOVER))
                self.nav_indicators[pname].configure(fg_color=COLOR_ACCENT)
            else:
                btn.configure(fg_color="transparent", hover_color=COLOR_SURFACE_HOVER,
                              text_color=COLOR_TEXT_MUTED,
                              image=get_icon(icon_name, 16, COLOR_TEXT_MUTED))
                self.nav_indicators[pname].configure(fg_color=COLOR_SIDEBAR)
        if name == "Queue":
            self._sync_queue_view()
            self._flush_deferred_logs()
        elif name == "History" and self._history_view_dirty:
            self._rebuild_history_list(self.history_search_entry.get(), force=True)
            self._history_view_dirty = False
        self._refresh_stats()

    def _refresh_stats(self):
        with self.tasks_lock:
            queued = sum(1 for t in self.tasks if t.status == "queued")
            downloading = sum(1 for t in self.tasks if t.status == "downloading")
            completed = sum(1 for t in self.tasks if t.status == "completed")
        if self._current_page == "Download" and hasattr(self, "stat_card_labels"):
            self.stat_card_labels["queued"].configure(text=str(queued))
            self.stat_card_labels["downloading"].configure(text=str(downloading))
            self.stat_card_labels["completed"].configure(text=str(completed))
            self.stat_card_labels["history"].configure(text=str(len(self.history)))
        if hasattr(self, "sidebar_stats_label"):
            self.sidebar_stats_label.configure(
                text=f"{queued:02d}  QUEUED    {downloading:02d}  ACTIVE\n{len(self.history):02d}  ARCHIVED  {completed:02d}  DONE")
        if hasattr(self, "uplink_status_label"):
            if downloading:
                status_text, status_color = "● UPLINK ACTIVE", COLOR_ACCENT_HOVER
            elif queued:
                status_text, status_color = "● UPLINK QUEUED", COLOR_WARNING
            else:
                status_text, status_color = "● UPLINK READY", COLOR_SUCCESS
            self.uplink_status_label.configure(text=status_text, text_color=status_color)
        if self._current_page == "Queue" and hasattr(self, "clear_completed_button"):
            state = "normal" if completed else "disabled"
            self.clear_completed_button.configure(state=state)
        if self._current_page == "History" and hasattr(self, "history_count_label"):
            self.history_count_label.configure(text=f"{len(self.history):02d} ARCHIVED ITEMS")
        if self._current_page == "History" and hasattr(self, "history_clear_button"):
            self.history_clear_button.configure(state="normal" if self.history else "disabled")
        if self._current_page == "Queue":
            self._refresh_queue_empty_state()

    def _refresh_queue_empty_state(self):
        if not hasattr(self, "queue_empty_state"):
            return
        with self.tasks_lock:
            is_empty = not self.tasks
        if is_empty:
            if not self.queue_empty_state.winfo_manager():
                self.queue_empty_state.pack(fill="x", padx=4, pady=(42, 12))
        else:
            self.queue_empty_state.pack_forget()

    def _sync_queue_view(self):
        """Apply the latest task model only when the Queue page is visible."""
        with self.tasks_lock:
            tasks = list(self.tasks)
        for task in tasks:
            self._update_task_title(task.id, task.title or task.url)
            self._update_task_progress(task.id, task.progress)
            self._update_task_status(task.id, task.status)

    def _flush_deferred_logs(self):
        if not self._deferred_log_messages:
            return
        pending = self._deferred_log_messages
        self._deferred_log_messages = []
        self._append_log_batch(pending)

    def _page_header(self, parent, title, eyebrow, action=None):
        row = layout_frame(parent)
        row.pack(fill="x", pady=(0, 16))
        text_box = layout_frame(row)
        text_box.pack(side="left")
        ctk.CTkLabel(text_box, text=title, font=ui_font(FONT_PAGE_TITLE, "bold"),
                     text_color=COLOR_TEXT).pack(anchor="w")
        ctk.CTkLabel(text_box, text=eyebrow, font=mono_font(FONT_SMALL),
                     text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(2, 0))
        if action:
            action.pack(in_=row, side="right")
        return row

    def _make_card(self, parent, icon_name, title, *, pack=True, border_color=COLOR_BORDER):
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=CONTROL_RADIUS,
                            border_width=0)
        if pack:
            card.pack(fill="x", padx=4, pady=6)
        ctk.CTkLabel(
            card, text=title, image=get_icon(icon_name, 15, COLOR_ACCENT_HOVER), compound="left",
            anchor="w", font=ui_font(FONT_SECTION_TITLE, "bold"),
            text_color=COLOR_TEXT,
        ).pack(fill="x", padx=CARD_PAD_X, pady=(14, 9))
        return card

    def _icon_chip(self, parent, icon_name, color, size=36, glyph_size=16):
        """A small circular chip with a centered icon glyph. Deliberately no
        border here (unlike cards): this shape can repeat many times in a
        long queue/history list, and each border is one more canvas draw
        pass on every resize - not worth it for a small decorative circle."""
        return ctk.CTkLabel(
            parent, text="", width=size, height=size, corner_radius=size // 2,
            fg_color=COLOR_CARD_ALT, image=get_icon(icon_name, glyph_size, color),
        )

    # -- Download tab ---------------------------------------------------

    def _smooth_wheel_step(self, frame):
        """CTkScrollableFrame ships yscrollincrement=1, so its Windows wheel
        handler moves only ~20 px per notch (measured jank source).  A 6 px
        unit gives the standard ~120 px per notch while keeping CTk's own
        event handling."""
        try:
            frame._parent_canvas.configure(yscrollincrement=6)
        except Exception:
            pass

    def _build_download_tab(self, parent):
        scroll = ctk.CTkScrollableFrame(
            parent, fg_color=COLOR_BG, scrollbar_fg_color=COLOR_BG,
            scrollbar_button_color=COLOR_BORDER_STRONG,
            scrollbar_button_hover_color=COLOR_ACCENT,
        )
        self._smooth_wheel_step(scroll)
        scroll.pack(fill="both", expand=True, padx=PAGE_PAD_X, pady=PAGE_PAD_Y)

        self._page_header(
            scroll, "Download media",
            "Paste a supported link and configure your download.",
        )

        stats_row = ctk.CTkFrame(
            scroll, fg_color=COLOR_CARD, corner_radius=CONTROL_RADIUS, border_width=0,
        )
        stats_row.pack(fill="x", pady=(0, 7))
        stats_row.columnconfigure((0, 1, 2, 3), weight=1, uniform="stats")
        self.stat_card_labels = {}
        stat_defs = [
            ("queued", "Queued", "inbox", COLOR_ACCENT),
            ("downloading", "Active", "download", COLOR_ACCENT_HOVER),
            ("completed", "Completed", "check", COLOR_SUCCESS),
            ("history", "Archived", "clock", COLOR_WARNING),
        ]
        for index, (key, label, icon_name, color) in enumerate(stat_defs):
            value_label = ctk.CTkLabel(
                stats_row, text="0", font=ui_font(18, "bold"),
                text_color=COLOR_TEXT,
            )
            value_label.grid(row=0, column=index, sticky="w", padx=16, pady=(10, 0))
            ctk.CTkLabel(
                stats_row, text=label.upper(), font=mono_font(FONT_MICRO, "bold"),
                text_color=color,
            ).grid(row=1, column=index, sticky="w", padx=16, pady=(0, 10))
            if index:
                layout_frame(stats_row, bg_color=COLOR_BORDER, width=1).grid(
                    row=0, column=index, rowspan=2, sticky="nsw", pady=10,
                )
            self.stat_card_labels[key] = value_label

        card = self._make_card(scroll, "link", "Source & Destination")
        form = layout_frame(card)
        form.pack(fill="x", padx=CARD_PAD_X, pady=(0, 15))
        form.columnconfigure((0, 1), weight=1, uniform="source_fields")

        url_group = layout_frame(form)
        url_group.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        url_group.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            url_group, text="VIDEO OR PLAYLIST URL", font=mono_font(FONT_MICRO, "bold"),
            text_color=COLOR_TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.url_entry = ctk.CTkEntry(
            url_group, placeholder_text="Paste a supported media link", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT, border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            url_group, text="Paste", width=72, height=CONTROL_HEIGHT, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_SURFACE_ELEVATED, hover_color=COLOR_SURFACE_HOVER,
            text_color=COLOR_TEXT, command=self.paste_from_clipboard,
        ).grid(row=1, column=1)

        folder_group = layout_frame(form)
        folder_group.grid(row=0, column=1, sticky="ew", padx=(7, 0))
        folder_group.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            folder_group, text="SAVE TO", font=mono_font(FONT_MICRO, "bold"),
            text_color=COLOR_TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.output_folder_entry = ctk.CTkEntry(
            folder_group, height=CONTROL_HEIGHT, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_CARD_ALT, border_width=1, border_color=COLOR_BORDER_STRONG,
        )
        self.output_folder_entry.insert(0, self.output_folder)
        self.output_folder_entry.configure(state="readonly")
        self.output_folder_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            folder_group, text="Browse", width=72, height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=self.browse_folder,
        ).grid(row=1, column=1)

        option_row = layout_frame(scroll)
        option_row.pack(fill="x")
        option_row.columnconfigure((0, 1), weight=1, uniform="download_options")

        media_card = self._make_card(option_row, "sliders", "Media Profile", pack=False)
        media_card.grid(row=0, column=0, sticky="nsew", padx=(4, 4), pady=6)
        self.mode_var = tk.StringVar(value="video")
        mode_row = ctk.CTkFrame(media_card, fg_color=COLOR_CARD_ALT, corner_radius=CONTROL_RADIUS)
        mode_row.pack(fill="x", padx=CARD_PAD_X, pady=(0, 10))
        ctk.CTkRadioButton(
            mode_row, text="Video", variable=self.mode_var, value="video", command=self.on_mode_change,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(side="left", padx=(12, 24), pady=9)
        ctk.CTkRadioButton(
            mode_row, text="Audio only", variable=self.mode_var, value="audio", command=self.on_mode_change,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(side="left", pady=9)

        self.video_settings_frame = layout_frame(media_card)
        self.video_settings_frame.pack(fill="x", padx=CARD_PAD_X, pady=(0, 5))
        self.video_settings_frame.columnconfigure(1, weight=1)
        ctk.CTkLabel(self.video_settings_frame, text="Quality", text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=3,
        )
        self.quality_menu = ctk.CTkOptionMenu(
            self.video_settings_frame, values=list(QUALITY_MAP.keys()), height=CONTROL_HEIGHT,
            fg_color=COLOR_CARD_ALT, button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
            dropdown_fg_color=COLOR_CARD, dropdown_hover_color=COLOR_ACCENT_SOFT,
            dropdown_text_color=COLOR_TEXT, text_color=COLOR_TEXT, corner_radius=CONTROL_RADIUS,
        )
        self.quality_menu.set("1080p (Full HD)")
        self.quality_menu.grid(row=0, column=1, sticky="ew", pady=3)
        self.fps60_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self.video_settings_frame, text="Prefer 60 FPS when available", variable=self.fps60_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 3))

        self.audio_settings_frame = layout_frame(media_card)
        self.audio_settings_frame.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        self.audio_settings_frame.columnconfigure(1, weight=1)
        ctk.CTkLabel(self.audio_settings_frame, text="Audio format", text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=3,
        )
        self.audio_format_menu = ctk.CTkOptionMenu(
            self.audio_settings_frame, values=list(AUDIO_FORMAT_MAP.keys()),
            command=self._on_audio_format_change, height=CONTROL_HEIGHT,
            fg_color=COLOR_CARD_ALT, button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
            dropdown_fg_color=COLOR_CARD, dropdown_hover_color=COLOR_ACCENT_SOFT,
            dropdown_text_color=COLOR_TEXT, text_color=COLOR_TEXT, corner_radius=CONTROL_RADIUS,
        )
        self.audio_format_menu.set("FLAC (Lossless container)")
        self.audio_format_menu.grid(row=0, column=1, sticky="ew", pady=3)
        ctk.CTkLabel(
            self.audio_settings_frame, text="MP3 bitrate", text_color=COLOR_TEXT_MUTED,
        ).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=3)
        self.mp3_bitrate_menu = ctk.CTkOptionMenu(
            self.audio_settings_frame, values=list(MP3_BITRATE_MAP.keys()),
            height=CONTROL_HEIGHT, fg_color=COLOR_CARD_ALT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, dropdown_fg_color=COLOR_CARD,
            dropdown_hover_color=COLOR_ACCENT_SOFT, dropdown_text_color=COLOR_TEXT,
            text_color=COLOR_TEXT, corner_radius=CONTROL_RADIUS,
        )
        self.mp3_bitrate_menu.set("320 kbps")
        self.mp3_bitrate_menu.grid(row=1, column=1, sticky="ew", pady=3)
        self.audio_format_help_var = tk.StringVar(
            value=AUDIO_FORMAT_DESCRIPTIONS["FLAC (Lossless container)"]
        )
        ctk.CTkLabel(
            self.audio_settings_frame, textvariable=self.audio_format_help_var,
            text_color=COLOR_TEXT_DIM, font=ui_font(FONT_SMALL),
            justify="left", wraplength=430,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        playlist_card = self._make_card(option_row, "list", "Playlist Range", pack=False)
        playlist_card.grid(row=0, column=1, sticky="nsew", padx=(4, 4), pady=6)
        self.playlist_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            playlist_card, text="Treat this link as a playlist", variable=self.playlist_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=CARD_PAD_X, pady=(0, 10))
        ctk.CTkLabel(
            playlist_card, text="Choose an optional inclusive item range. Leave End blank for the remainder.",
            font=ui_font(FONT_SMALL), text_color=COLOR_TEXT_DIM, justify="left", wraplength=420,
        ).pack(anchor="w", padx=CARD_PAD_X, pady=(0, 11))
        playlist_fields = layout_frame(playlist_card)
        playlist_fields.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        playlist_fields.columnconfigure((0, 1), weight=1, uniform="playlist_fields")
        ctk.CTkLabel(playlist_fields, text="START ITEM", font=mono_font(FONT_MICRO, "bold"),
                     text_color=COLOR_TEXT_MUTED).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(playlist_fields, text="END ITEM", font=mono_font(FONT_MICRO, "bold"),
                     text_color=COLOR_TEXT_MUTED).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.playlist_start_entry = ctk.CTkEntry(
            playlist_fields, placeholder_text="1", height=CONTROL_HEIGHT, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_CARD_ALT, border_width=1, border_color=COLOR_BORDER_STRONG,
        )
        self.playlist_start_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.playlist_end_entry = ctk.CTkEntry(
            playlist_fields, placeholder_text="last", height=CONTROL_HEIGHT, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_CARD_ALT, border_width=1, border_color=COLOR_BORDER_STRONG,
        )
        self.playlist_end_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))

        self.on_mode_change()

        card = self._make_card(scroll, "captions", "Subtitles & Precision Trim")
        subtitle_body = layout_frame(card)
        subtitle_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        subtitle_body.columnconfigure(1, weight=1)
        self.subs_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            subtitle_body, text="Download matching subtitles", variable=self.subs_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(0, 22))
        trim_fields = layout_frame(subtitle_body)
        trim_fields.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(trim_fields, text="START", font=mono_font(FONT_MICRO, "bold"),
                     text_color=COLOR_TEXT_MUTED).grid(row=0, column=0, sticky="w")
        self.start_time_entry = ctk.CTkEntry(
            trim_fields, placeholder_text="HH:MM:SS", width=116, height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT, border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.start_time_entry.grid(row=1, column=0, pady=(3, 0))
        ctk.CTkLabel(trim_fields, text="END", font=mono_font(FONT_MICRO, "bold"),
                     text_color=COLOR_TEXT_MUTED).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.end_time_entry = ctk.CTkEntry(
            trim_fields, placeholder_text="HH:MM:SS", width=116, height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT, border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.end_time_entry.grid(row=1, column=1, padx=(8, 0), pady=(3, 0))
        self.add_queue_button = ctk.CTkButton(
            subtitle_body, text="Add to Queue  →", width=178, height=PRIMARY_BUTTON_HEIGHT,
            corner_radius=CONTROL_RADIUS, font=ui_font(FONT_SECTION_TITLE, "bold"),
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT_ON_ACCENT,
            command=self.add_to_queue,
        )
        self.add_queue_button.grid(row=0, column=2, sticky="e", padx=(18, 0))
        ctk.CTkLabel(
            subtitle_body,
            text="Language and embed options are in Settings. Blank trim fields keep the full media.",
            font=ui_font(FONT_SMALL), text_color=COLOR_TEXT_DIM, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self._mac_bind_scroll_recursive(scroll, scroll)

    # -- Queue tab --------------------------------------------------------

    def _build_queue_tab(self, parent):
        wrapper = layout_frame(parent, bg_color=COLOR_BG)
        wrapper.pack(fill="both", expand=True, padx=PAGE_PAD_X, pady=PAGE_PAD_Y)

        queue_header = self._page_header(
            wrapper, "Queue",
            "> TASK CONTROL  /  live progress and bounded event output",
        )
        self.clear_completed_button = ctk.CTkButton(
            queue_header, text="Clear Completed", width=142, height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=self.clear_completed,
        )
        self.clear_completed_button.pack(side="right")

        self.queue_list_frame = ctk.CTkScrollableFrame(
            wrapper, height=300, fg_color=COLOR_BG, scrollbar_fg_color=COLOR_BG,
            scrollbar_button_color=COLOR_BORDER_STRONG,
            scrollbar_button_hover_color=COLOR_ACCENT,
        )
        self._smooth_wheel_step(self.queue_list_frame)
        self.queue_list_frame.pack(fill="both", expand=True, pady=(0, 12))
        self.queue_empty_state = layout_frame(self.queue_list_frame, bg_color=COLOR_BG)
        self.queue_empty_state.pack(fill="x", padx=4, pady=(42, 12))
        self.queue_empty_image = get_brand_ctk_image(32)
        ctk.CTkLabel(self.queue_empty_state, text="", image=self.queue_empty_image).pack(pady=(18, 8))
        ctk.CTkLabel(
            self.queue_empty_state, text="Queue standing by",
            font=ui_font(17, "bold"), text_color=COLOR_TEXT,
        ).pack()
        ctk.CTkLabel(
            self.queue_empty_state, text="Add a link from Download to begin. No background animation runs here.",
            font=ui_font(FONT_SMALL), text_color=COLOR_TEXT_MUTED,
        ).pack(pady=(4, 18))

        log_card = ctk.CTkFrame(
            wrapper, fg_color=COLOR_CARD, corner_radius=CONTROL_RADIUS, border_width=0,
        )
        log_card.pack(fill="x")
        log_top = layout_frame(log_card)
        log_top.pack(fill="x", padx=CARD_PAD_X, pady=(13, 7))
        log_title = layout_frame(log_top)
        log_title.pack(side="left")
        ctk.CTkLabel(log_title, text="", image=get_icon("terminal", 15, COLOR_ACCENT_HOVER)).pack(
            side="left", padx=(0, 8),
        )
        ctk.CTkLabel(
            log_title, text="ACTIVITY STREAM", font=mono_font(FONT_SMALL, "bold"),
            text_color=COLOR_TEXT,
        ).pack(side="left")
        self.log_status_label = ctk.CTkLabel(
            log_title, text="  /  BOUNDED 1000 LINES", font=mono_font(FONT_MICRO),
            text_color=COLOR_TEXT_DIM,
        )
        self.log_status_label.pack(side="left")
        ctk.CTkButton(
            log_top, text="Clear Log", fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            corner_radius=CONTROL_RADIUS, width=92, height=30, command=self.clear_log,
        ).pack(side="right")
        self.log_textbox = EfficientCTkTextbox(
            log_card, height=150, state="disabled", corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_CARD_ALT, text_color=COLOR_TEXT_MUTED, font=mono_font(FONT_SMALL),
            border_width=0,
        )
        self.log_textbox.pack(fill="x", padx=CARD_PAD_X, pady=(0, 15))
        try:
            self.log_textbox._textbox.tag_configure("info", foreground=COLOR_TEXT_MUTED)
            self.log_textbox._textbox.tag_configure("warning", foreground=COLOR_WARNING)
            self.log_textbox._textbox.tag_configure("error", foreground=COLOR_ERROR)
        except Exception:
            pass

    # -- History tab ------------------------------------------------------

    def _build_history_tab(self, parent):
        wrapper = layout_frame(parent, bg_color=COLOR_BG)
        wrapper.pack(fill="both", expand=True, padx=PAGE_PAD_X, pady=PAGE_PAD_Y)

        history_header = self._page_header(
            wrapper, "History", "> ARCHIVE INDEX  /  search, reopen, or repeat completed transfers",
        )
        header_action = layout_frame(history_header)
        self.history_count_label = ctk.CTkLabel(
            header_action, text="00 ARCHIVED ITEMS", font=mono_font(FONT_MICRO, "bold"),
            text_color=COLOR_TEXT_DIM,
        )
        self.history_count_label.pack(side="left", padx=(0, 12))
        self.history_clear_button = ctk.CTkButton(
            header_action, text="Clear History", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=self.clear_all_history,
        )
        self.history_clear_button.pack(side="left")
        header_action.pack(side="right")

        search_row = layout_frame(wrapper, bg_color=COLOR_BG)
        search_row.pack(fill="x", pady=(0, 12))
        self.history_search_entry = ctk.CTkEntry(
            search_row, placeholder_text="Search by title or saved path", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT, border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.history_search_entry.pack(side="left", fill="x", expand=True)
        self.history_search_entry.bind("<KeyRelease>", self._schedule_history_filter)

        self.history_list_frame = ctk.CTkScrollableFrame(
            wrapper, fg_color=COLOR_BG, scrollbar_fg_color=COLOR_BG,
            scrollbar_button_color=COLOR_BORDER_STRONG,
            scrollbar_button_hover_color=COLOR_ACCENT,
        )
        self._smooth_wheel_step(self.history_list_frame)
        self.history_list_frame.pack(fill="both", expand=True)

    # -- Settings tab -----------------------------------------------------

    def _build_settings_tab(self, parent):
        scroll = ctk.CTkScrollableFrame(
            parent, fg_color=COLOR_BG, scrollbar_fg_color=COLOR_BG,
            scrollbar_button_color=COLOR_BORDER_STRONG,
            scrollbar_button_hover_color=COLOR_ACCENT,
        )
        self._smooth_wheel_step(scroll)
        scroll.pack(fill="both", expand=True, padx=PAGE_PAD_X, pady=PAGE_PAD_Y)
        self._page_header(
            scroll, "Settings",
            "> SYSTEM PROFILE  /  preferences are saved automatically on exit",
        )

        card = self._make_card(scroll, "gear", "Maintenance & Runtime")
        maintenance = layout_frame(card)
        maintenance.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        self.update_button = ctk.CTkButton(
            maintenance, text="Check & Install", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT_ON_ACCENT,
            command=self.start_update,
        )
        self.update_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.rollback_button = ctk.CTkButton(
            maintenance, text="Roll Back", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=self.start_runtime_rollback,
        )
        self.rollback_button.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.restore_button = ctk.CTkButton(
            maintenance, text="Use Bundled", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=self.start_restore_bundled,
        )
        self.restore_button.grid(row=0, column=2, sticky="w", padx=(0, 8))
        ctk.CTkButton(
            maintenance, text="Open Output Folder", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=lambda: open_path(self.output_folder),
        ).grid(row=0, column=3, sticky="w")
        ctk.CTkLabel(
            maintenance, text="Update channel", text_color=COLOR_TEXT_MUTED,
        ).grid(row=1, column=0, sticky="w", pady=(10, 3))
        self.ytdlp_channel_menu = ctk.CTkOptionMenu(
            maintenance, values=list(YTDLP_CHANNELS), height=CONTROL_HEIGHT,
            fg_color=COLOR_CARD_ALT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, dropdown_fg_color=COLOR_CARD,
            dropdown_hover_color=COLOR_ACCENT_SOFT, dropdown_text_color=COLOR_TEXT,
            text_color=COLOR_TEXT, corner_radius=CONTROL_RADIUS,
        )
        self.ytdlp_channel_menu.set(DEFAULT_YTDLP_CHANNEL)
        self.ytdlp_channel_menu.grid(row=1, column=1, sticky="w", pady=(10, 3))
        backend = active_ytdlp_summary()
        self.runtime_status_var = tk.StringVar(
            value=f"Active: {backend['version']} ({backend['source']})"
        )
        ctk.CTkLabel(
            maintenance, textvariable=self.runtime_status_var,
            text_color=COLOR_TEXT_MUTED,
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=(10, 0), pady=(10, 3))
        self.ytdlp_startup_check_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            maintenance, text="Check this channel at startup (at most once every 24 hours)",
            variable=self.ytdlp_startup_check_var, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 3))
        self.remote_components_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            maintenance,
            text="Allow yt-dlp to fetch official challenge-solver components only when required",
            variable=self.remote_components_var, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 3))
        ctk.CTkLabel(
            maintenance,
            text="Updates come from official yt-dlp GitHub releases and are checksum-verified, "
                 "execution-tested, and activated only while the queue is idle.",
            font=ui_font(FONT_SMALL), text_color=COLOR_TEXT_DIM,
        ).grid(row=4, column=0, columnspan=4, sticky="w")
        settings_grid = layout_frame(scroll)
        settings_grid.pack(fill="x")
        settings_grid.columnconfigure((0, 1), weight=1, uniform="settings_columns")

        card = self._make_card(settings_grid, "lock", "Cookies & Sign-in", pack=False)
        card.grid(row=0, column=0, sticky="nsew", padx=(4, 4), pady=6)
        cookie_body = layout_frame(card)
        cookie_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        cookie_body.columnconfigure(1, weight=1)
        ctk.CTkLabel(cookie_body, text="Source", text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 9), pady=3,
        )
        self.cookie_mode_menu = ctk.CTkOptionMenu(
            cookie_body, values=list(COOKIE_MODE_MAP.keys()), command=self._on_cookie_mode_change,
            height=CONTROL_HEIGHT, fg_color=COLOR_CARD_ALT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, dropdown_fg_color=COLOR_CARD,
            dropdown_hover_color=COLOR_ACCENT_SOFT, dropdown_text_color=COLOR_TEXT,
            text_color=COLOR_TEXT, corner_radius=CONTROL_RADIUS,
        )
        self.cookie_mode_menu.set("Disabled")
        self.cookie_mode_menu.grid(row=0, column=1, sticky="ew", pady=3)
        ctk.CTkLabel(cookie_body, text="Browser", text_color=COLOR_TEXT_MUTED).grid(
            row=1, column=0, sticky="w", padx=(0, 9), pady=3,
        )
        self.cookie_browser_menu = ctk.CTkOptionMenu(
            cookie_body, values=["Chrome", "Edge", "Firefox", "Brave"],
            height=CONTROL_HEIGHT, fg_color=COLOR_CARD_ALT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, dropdown_fg_color=COLOR_CARD,
            dropdown_hover_color=COLOR_ACCENT_SOFT, dropdown_text_color=COLOR_TEXT,
            text_color=COLOR_TEXT, corner_radius=CONTROL_RADIUS,
        )
        self.cookie_browser_menu.set("Chrome")
        self.cookie_browser_menu.grid(row=1, column=1, sticky="ew", pady=3)
        self.cookie_browser_menu.configure(state="disabled")
        ctk.CTkLabel(cookie_body, text="Profile", text_color=COLOR_TEXT_MUTED).grid(
            row=2, column=0, sticky="w", padx=(0, 9), pady=3,
        )
        self.cookie_profile_entry = ctk.CTkEntry(
            cookie_body, placeholder_text="Default profile or profile path (optional)",
            height=CONTROL_HEIGHT, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_CARD_ALT, border_width=1, border_color=COLOR_BORDER_STRONG,
        )
        self.cookie_profile_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        self.cookie_profile_entry.configure(state="disabled")
        ctk.CTkLabel(cookie_body, text="File", text_color=COLOR_TEXT_MUTED).grid(
            row=3, column=0, sticky="w", padx=(0, 9), pady=3,
        )
        self.cookie_file_entry = ctk.CTkEntry(
            cookie_body, placeholder_text="Netscape cookies.txt", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT, border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.cookie_file_entry.grid(row=3, column=1, sticky="ew", pady=3)
        self.cookie_file_entry.configure(state="disabled")
        ctk.CTkButton(
            cookie_body, text="Browse", width=70, height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=self.browse_cookie_file,
        ).grid(row=3, column=2, padx=(7, 0), pady=3)
        self.browser_verify_button = ctk.CTkButton(
            cookie_body, text="Open Verification Window", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=self.start_browser_verification,
        )
        self.browser_verify_button.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.browser_clear_button = ctk.CTkButton(
            cookie_body, text="Clear Session", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_ERROR, text_color=COLOR_TEXT,
            command=self.clear_browser_session,
        )
        self.browser_clear_button.grid(row=4, column=2, sticky="e", pady=(8, 2))
        self.browser_retry_button = ctk.CTkButton(
            cookie_body, text="Retry after verification", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, text_color="#FFFFFF",
            state="disabled", command=self.retry_after_verification,
        )
        self.browser_retry_button.grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(5, 2)
        )
        self.browser_session_status_var = tk.StringVar(value="No verified session loaded.")
        ctk.CTkLabel(
            cookie_body, textvariable=self.browser_session_status_var,
            text_color=COLOR_TEXT_DIM, font=ui_font(FONT_SMALL),
            justify="left", wraplength=430,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self._browser_candidate_map = {"Automatic": None}
        self.browser_candidate_menu = ctk.CTkOptionMenu(
            cookie_body, values=["Automatic"], height=CONTROL_HEIGHT,
            fg_color=COLOR_CARD_ALT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, dropdown_fg_color=COLOR_CARD,
            dropdown_hover_color=COLOR_ACCENT_SOFT, dropdown_text_color=COLOR_TEXT,
            text_color=COLOR_TEXT, corner_radius=CONTROL_RADIUS,
        )
        self.browser_candidate_menu.set("Automatic")
        self.browser_candidate_menu.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        card = self._make_card(settings_grid, "captions", "Subtitle Preferences", pack=False)
        card.grid(row=0, column=1, sticky="nsew", padx=(4, 4), pady=6)
        subtitle_body = layout_frame(card)
        subtitle_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        ctk.CTkLabel(
            subtitle_body, text="LANGUAGE PATTERN", font=mono_font(FONT_MICRO, "bold"),
            text_color=COLOR_TEXT_MUTED,
        ).pack(anchor="w")
        self.sub_langs_entry = ctk.CTkEntry(
            subtitle_body, height=CONTROL_HEIGHT, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_CARD_ALT, border_width=1, border_color=COLOR_BORDER_STRONG,
        )
        self.sub_langs_entry.insert(0, DEFAULT_SUBTITLE_LANGUAGE_PATTERN)
        self.sub_langs_entry.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(
            subtitle_body,
            text="Regular-expression matching is supported. en.* matches en, en-US, en-GB, and similar English variants.",
            font=ui_font(FONT_SMALL), text_color=COLOR_TEXT_DIM,
            justify="left", wraplength=430,
        ).pack(anchor="w", pady=(0, 8))
        self.embed_subs_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            subtitle_body, text="Embed subtitles into the video (remuxes to MKV)",
            variable=self.embed_subs_var, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 6))
        self.auto_captions_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            subtitle_body, text="Include automatically generated captions",
            variable=self.auto_captions_var, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w")

        card = self._make_card(settings_grid, "music", "Audio Extraction", pack=False)
        card.grid(row=1, column=0, sticky="nsew", padx=(4, 4), pady=6)
        audio_body = layout_frame(card)
        audio_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        self.embed_thumbnail_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            audio_body, text="Embed the thumbnail as cover art", variable=self.embed_thumbnail_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 7))
        self.embed_metadata_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            audio_body, text="Embed title, artist, and available metadata", variable=self.embed_metadata_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w")

        card = self._make_card(settings_grid, "block", "SponsorBlock", pack=False)
        card.grid(row=1, column=1, sticky="nsew", padx=(4, 4), pady=6)
        sponsor_body = layout_frame(card)
        sponsor_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        self.sponsorblock_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            sponsor_body, text="Remove selected sponsor/ad segments", variable=self.sponsorblock_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(sponsor_body, text="CATEGORIES", font=mono_font(FONT_MICRO, "bold"),
                     text_color=COLOR_TEXT_MUTED).pack(anchor="w")
        self.sponsorblock_entry = ctk.CTkEntry(
            sponsor_body, height=CONTROL_HEIGHT, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_CARD_ALT, border_width=1, border_color=COLOR_BORDER_STRONG,
        )
        self.sponsorblock_entry.insert(0, "sponsor,selfpromo,interaction")
        self.sponsorblock_entry.pack(fill="x", pady=(4, 0))

        card = self._make_card(settings_grid, "globe", "Network", pack=False)
        card.grid(row=2, column=0, sticky="nsew", padx=(4, 4), pady=6)
        net_body = layout_frame(card)
        net_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        net_body.columnconfigure(1, weight=1)
        ctk.CTkLabel(net_body, text="Proxy URL", text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 9), pady=3,
        )
        self.proxy_entry = ctk.CTkEntry(
            net_body, placeholder_text="http://127.0.0.1:8080", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT, border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.proxy_entry.grid(row=0, column=1, sticky="ew", pady=3)
        ctk.CTkLabel(net_body, text="Rate limit", text_color=COLOR_TEXT_MUTED).grid(
            row=1, column=0, sticky="w", padx=(0, 9), pady=3,
        )
        self.rate_limit_entry = ctk.CTkEntry(
            net_body, placeholder_text="2M or 500K", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT, border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.rate_limit_entry.grid(row=1, column=1, sticky="ew", pady=3)
        ctk.CTkLabel(
            net_body, text="Browser request profile", text_color=COLOR_TEXT_MUTED,
        ).grid(row=2, column=0, sticky="w", padx=(0, 9), pady=3)
        self.impersonation_menu = ctk.CTkOptionMenu(
            net_body,
            values=["Automatic", "Chrome", "Firefox", "Safari", "Disabled"],
            height=CONTROL_HEIGHT, fg_color=COLOR_CARD_ALT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, dropdown_fg_color=COLOR_CARD,
            dropdown_hover_color=COLOR_ACCENT_SOFT, dropdown_text_color=COLOR_TEXT,
            text_color=COLOR_TEXT, corner_radius=CONTROL_RADIUS,
        )
        self.impersonation_menu.set("Automatic")
        self.impersonation_menu.grid(row=2, column=1, sticky="ew", pady=3)
        self.force_ipv4_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            net_body, text="Force IPv4 connections", variable=self.force_ipv4_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        card = self._make_card(settings_grid, "folder", "Files & Naming", pack=False)
        card.grid(row=2, column=1, sticky="nsew", padx=(4, 4), pady=6)
        files_body = layout_frame(card)
        files_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        self.restrict_filenames_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            files_body, text="Restrict filenames to plain ASCII", variable=self.restrict_filenames_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 6))
        self.use_archive_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            files_body, text="Skip items already recorded in the archive", variable=self.use_archive_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(files_body, text="FILENAME TEMPLATE", font=mono_font(FONT_MICRO, "bold"),
                     text_color=COLOR_TEXT_MUTED).pack(anchor="w")
        self.output_template_entry = ctk.CTkEntry(
            files_body, height=CONTROL_HEIGHT, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_CARD_ALT, border_width=1, border_color=COLOR_BORDER_STRONG,
        )
        self.output_template_entry.insert(0, "%(title)s.%(ext)s")
        self.output_template_entry.pack(fill="x", pady=(4, 7))
        ctk.CTkLabel(files_body, text="FORMAT SORTING (-S)", font=mono_font(FONT_MICRO, "bold"),
                     text_color=COLOR_TEXT_MUTED).pack(anchor="w")
        self.format_sort_entry = ctk.CTkEntry(
            files_body, placeholder_text="res,ext:mp4:m4a", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT, border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.format_sort_entry.pack(fill="x", pady=(4, 0))

        # Media Observer settings card: passive sensor status/health/update
        # (read-only adapter; ranking/validation stay core-owned).
        card = self._make_card(settings_grid, "gear", "Media Observer", pack=False)
        card.grid(row=3, column=0, sticky="nsew", padx=(4, 4), pady=6)
        observer_body = layout_frame(card)
        observer_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        self.observer_status_label = ctk.CTkLabel(
            observer_body,
            text=self._media_observer_status_text(),
            text_color=COLOR_TEXT, font=ui_font(FONT_SMALL),
            anchor="w", justify="left", wraplength=430,
        )
        self.observer_status_label.pack(anchor="w")
        ctk.CTkLabel(
            observer_body,
            text="puemos/hls-downloader · MIT · github.com/puemos/hls-downloader",
            text_color=COLOR_TEXT_MUTED, font=ui_font(FONT_SMALL), anchor="w",
        ).pack(anchor="w", pady=(2, 6))
        observer_buttons = ctk.CTkFrame(observer_body, fg_color="transparent")
        observer_buttons.pack(fill="x")
        ctk.CTkButton(
            observer_buttons, text="Check for updates", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            width=150, command=self.start_observer_check,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            observer_buttons, text="Update", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            width=110, command=self.start_observer_update,
        ).pack(side="left")

        # The settings card for the homemade adblock engine was removed: the
        # protected browser's content filtering is the bundled uBOL extension
        # (installed before the requested page loads), which manages its own
        # filter lists.
        card = self._make_card(settings_grid, "inbox", "About", pack=False)
        card.grid(row=3, column=1, sticky="nsew", padx=(4, 4), pady=6)
        about_body = layout_frame(card)
        about_body.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        ctk.CTkLabel(
            about_body, text=f"{APP_NAME} {APP_VERSION}  /  BUILD {APP_BUILD}",
            text_color=COLOR_TEXT, font=mono_font(FONT_SECTION_TITLE, "bold"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            about_body, text=APP_COPYRIGHT, text_color=COLOR_TEXT_MUTED,
            font=ui_font(FONT_SMALL), anchor="w",
        ).pack(anchor="w", pady=(2, 2))
        ctk.CTkLabel(
            about_body,
            text="Dense desktop media downloader with same-job protected browser "
                 "fallback, queue/history persistence, and local-only capture "
                 "protection. See the sidebar footer for the version/build marker.",
            font=ui_font(FONT_SMALL), text_color=COLOR_TEXT_DIM,
            justify="left", wraplength=430,
        ).pack(anchor="w", pady=(2, 8))
        ctk.CTkButton(
            about_body, text="Licenses & notices", height=CONTROL_HEIGHT,
            corner_radius=CONTROL_RADIUS, fg_color=COLOR_SURFACE_ELEVATED,
            hover_color=COLOR_SURFACE_HOVER, text_color=COLOR_TEXT,
            command=self._open_notices,
        ).pack(anchor="w")

        card = self._make_card(
            scroll, "terminal", "Advanced: Explicit Custom yt-dlp Command",
            border_color=COLOR_WARNING,
        )
        advanced = layout_frame(card)
        advanced.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        self.use_custom_command_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            advanced, text="I understand: use this custom command for the next queued download",
            variable=self.use_custom_command_var, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            advanced,
            text="SAFETY GATE  /  Text below is inert until the checkbox is explicitly enabled. Custom mode overrides most normal options.",
            font=mono_font(FONT_MICRO, "bold"), text_color=COLOR_WARNING,
            justify="left", wraplength=900,
        ).pack(anchor="w", pady=(6, 8))
        self.custom_command_box = EfficientCTkTextbox(
            advanced, height=78, corner_radius=CONTROL_RADIUS, fg_color=COLOR_CARD_ALT,
            text_color=COLOR_TEXT, font=mono_font(FONT_SMALL), border_width=1,
            border_color=COLOR_BORDER_STRONG,
        )
        self.custom_command_box.pack(fill="x")

        self._mac_bind_scroll_recursive(scroll, scroll)

    # ------------------------------------------------------------------
    # UI event handlers
    # ------------------------------------------------------------------

    def on_mode_change(self):
        if self.mode_var.get() == "video":
            self._set_frame_state(self.video_settings_frame, "normal")
            self._set_frame_state(self.audio_settings_frame, "disabled")
        else:
            self._set_frame_state(self.video_settings_frame, "disabled")
            self._set_frame_state(self.audio_settings_frame, "normal")
            self._on_audio_format_change(self.audio_format_menu.get())

    def _on_audio_format_change(self, selection):
        description = AUDIO_FORMAT_DESCRIPTIONS.get(selection, "")
        if hasattr(self, "audio_format_help_var"):
            self.audio_format_help_var.set(description)
        if hasattr(self, "mp3_bitrate_menu"):
            enabled = self.mode_var.get() == "audio" and selection == "MP3 (Compressed)"
            self.mp3_bitrate_menu.configure(state="normal" if enabled else "disabled")

    @staticmethod
    def _set_frame_state(frame, state):
        for child in frame.winfo_children():
            try:
                child.configure(state=state)
            except Exception:
                pass

    def _mac_bind_scroll_recursive(self, widget, scroll_frame):
        """Forward macOS trackpad events from nested controls to their local
        scroll frame. CustomTkinter's global Darwin handler can lose nested
        events; returning ``break`` prevents that handler from scrolling a
        second time. Text boxes, sliders and scrollbars keep native behavior."""
        if platform.system() != "Darwin":
            return
        excluded_types = (ctk.CTkTextbox, ctk.CTkSlider, ctk.CTkScrollbar)
        if isinstance(widget, excluded_types):
            return
        try:
            widget.bind(
                "<MouseWheel>",
                lambda event, frame=scroll_frame: self._mac_scroll_forward(event, frame),
                add="+",
            )
        except Exception:
            pass
        try:
            children = widget.winfo_children()
        except Exception:
            children = []
        for child in children:
            self._mac_bind_scroll_recursive(child, scroll_frame)

    def _mac_scroll_forward(self, event, scroll_frame):
        if platform.system() != "Darwin":
            return None
        try:
            canvas = scroll_frame._parent_canvas
            if canvas.yview() == (0.0, 1.0):
                return "break"
            delta = getattr(event, "delta", 0)
            amount = -int(delta)
            if amount == 0 and delta:
                amount = -1 if delta > 0 else 1
            if amount:
                canvas.yview_scroll(amount, "units")
            return "break"
        except Exception:
            return None

    def _on_cookie_mode_change(self, choice):
        if choice == "Selected Browser":
            self.cookie_browser_menu.configure(state="normal")
            self.cookie_profile_entry.configure(state="normal")
            self.cookie_file_entry.configure(state="disabled")
        elif choice == "cookies.txt File":
            self.cookie_browser_menu.configure(state="disabled")
            self.cookie_profile_entry.configure(state="disabled")
            self.cookie_file_entry.configure(state="normal")
        else:
            self.cookie_browser_menu.configure(state="disabled")
            self.cookie_profile_entry.configure(state="disabled")
            self.cookie_file_entry.configure(state="disabled")

    def _open_notices(self):
        notices = resource_path(Path("THIRD_PARTY_NOTICES.md"))
        if not Path(notices).exists():
            messagebox.showinfo(
                "Licenses & notices",
                "The notices file is not available in this build.",
            )
            return
        open_path(str(notices))

    def browse_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_folder)
        if folder:
            self.output_folder = folder
            self.output_folder_entry.configure(state="normal")
            self.output_folder_entry.delete(0, "end")
            self.output_folder_entry.insert(0, folder)
            self.output_folder_entry.configure(state="readonly")

    def browse_cookie_file(self):
        path = filedialog.askopenfilename(title="Select cookies.txt",
                                           filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.cookie_file_entry.configure(state="normal")
            self.cookie_file_entry.delete(0, "end")
            self.cookie_file_entry.insert(0, path)

    def start_browser_verification(self, requested_url=None):
        url = (requested_url or self.url_entry.get()).strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showerror(
                "Media Link Required",
                "Paste the page address on the Download page before opening verification.",
            )
            return
        BROWSER_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        result_path = BROWSER_SESSION_DIR / f"verification-{uuid.uuid4().hex}.json"
        self.browser_verify_button.configure(state="disabled", text="Verification open...")
        threading.Thread(
            target=self._run_browser_verification,
            args=(url, result_path),
            daemon=True,
        ).start()

    def _run_browser_verification(self, url, result_path):
        command = build_self_invocation() + ["__vrka_browser__", url, str(result_path)]
        try:
            process = subprocess.Popen(command)
            self._browser_verification_process = process
            try:
                returncode = process.wait(timeout=60 * 30)
            except subprocess.TimeoutExpired:
                self._terminate_process_tree(process)
                raise RuntimeError("The verification window timed out after 30 minutes.")
            if not result_path.is_file():
                raise RuntimeError(f"Verification process exited with code {returncode}.")
            with open(result_path, "r", encoding="utf-8") as file_handle:
                payload = json.load(file_handle)
            if not payload.get("ok"):
                raise RuntimeError(payload.get("error") or "No browser session was captured.")
            self.ui_queue.put(("browser_session_ready", payload))

        except Exception as exc:
            self.ui_queue.put(("browser_session_error", str(exc)))
        finally:
            self._browser_verification_process = None
            try:
                result_path.unlink()
            except OSError:
                pass
            shutil.rmtree(result_path.with_suffix(".profile"), ignore_errors=True)

    def _accept_browser_session(self, payload):
        self._verified_session = dict(payload)
        candidates = list(payload.get("media_candidates") or [])[:10]
        candidate_map = {"Automatic": None}
        for index, candidate in enumerate(candidates, 1):
            candidate_map[media_candidate_label(index, candidate)] = candidate
        self._browser_candidate_map = candidate_map
        self.browser_candidate_menu.configure(values=list(candidate_map))
        self.browser_candidate_menu.set("Automatic")
        count = len(candidates)
        observed = int(payload.get("observed_request_count") or 0)
        rejected = int(payload.get("rejected_junk_count") or 0)
        blocked_popups = int(payload.get("blocked_popup_count") or 0)
        drm_text = (
            " DRM-protected media was detected and will be refused."
            if payload.get("drm_detected") else ""
        )
        self.browser_session_status_var.set(
            f"Session captured; {observed} request(s) observed, {count} media candidate(s) "
            f"kept, {rejected} obvious junk request(s) rejected, {blocked_popups} popup(s) "
            f"blocked. Retry to verify.{drm_text}"
        )
        self.cookie_mode_menu.set("Verified Session")
        self._on_cookie_mode_change("Verified Session")
        self.browser_verify_button.configure(state="normal", text="Open Verification Window")
        self.browser_retry_button.configure(
            state="normal" if self._pending_browser_retry_url else "disabled"
        )
        self.ui_queue.put((
            "log",
            f"Browser session captured: {observed} request(s) observed, {count} media "
            f"candidate(s) kept, {rejected} obvious junk request(s) rejected; "
            "success is confirmed only by retrying extraction."
            + drm_text,
        ))

    def retry_after_verification(self):
        """Queue the exact failed page again; the retry itself proves success."""
        url = str(getattr(self, "_pending_browser_retry_url", "") or "").strip()
        if not url or not getattr(self, "_verified_session", {}).get("ok"):
            messagebox.showerror(
                "Verification Session Required",
                "Complete browser verification for a failed download before retrying.",
            )
            return
        self.show_page("Download")
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)
        self.cookie_mode_menu.set("Verified Session")
        self._on_cookie_mode_change("Verified Session")
        self.add_to_queue()
    def clear_browser_session(self):
        self._verified_session = {}
        self._browser_candidate_map = {"Automatic": None}
        self.browser_candidate_menu.configure(values=["Automatic"])
        self.browser_candidate_menu.set("Automatic")
        self.browser_session_status_var.set("No verified session loaded.")
        self._pending_browser_retry_url = ""
        self.browser_retry_button.configure(state="disabled")
        if self.cookie_mode_menu.get() == "Verified Session":
            self.cookie_mode_menu.set("Disabled")
            self._on_cookie_mode_change("Disabled")
        try:
            (BROWSER_SESSION_DIR / "verified-session-cookies.txt").unlink()
        except OSError:
            pass
        self.ui_queue.put(("log", "Cleared the temporary VRKA Browser session."))

    def _prompt_browser_verification(self, url, category):
        reason = {
            "cloudflare": "The website is blocking automated access.",
            "cookies": "The website appears to require a signed-in browser session.",
            "unsupported": "The page opened, but yt-dlp could not identify playable media.",
            "expired": "The previously verified media address or session is no longer valid.",
        }.get(category, "Browser verification may be required.")
        open_now = messagebox.askyesno(
            "Browser verification required",
            reason + "\n\nOpen the exact page in the on-demand VRKA Browser now? "
            "After verification or sign-in, close that browser and add the download again.",
        )
        if open_now:
            self._pending_browser_retry_url = url
            self.start_browser_verification(url)
    def paste_from_clipboard(self):
        try:
            text = self.clipboard_get()
        except Exception:
            return
        if text:
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, text.strip())

    # ------------------------------------------------------------------
    # UI queue processing (runs on the main/UI thread)
    # ------------------------------------------------------------------

    def process_ui_queue(self):
        if self._closing:
            return
        log_messages = []
        latest_progress = {}
        latest_status = {}
        latest_title = {}
        latest_metrics = {}
        history_refresh = False
        processed = 0
        try:
            while processed < UI_QUEUE_BATCH_LIMIT:
                msg = self.ui_queue.get_nowait()
                processed += 1
                kind = msg[0]
                if kind == "log":
                    log_messages.append(msg[1])
                elif kind == "task_progress":
                    latest_progress[msg[1]] = msg[2]
                elif kind == "task_status":
                    latest_status[msg[1]] = msg[2]
                elif kind == "task_title":
                    latest_title[msg[1]] = msg[2]
                elif kind == "task_metrics":
                    latest_metrics[msg[1]] = msg[2:]
                elif kind == "history_refresh":
                    history_refresh = True
                elif kind == "runtime_done":
                    self._set_runtime_controls(True)
                    self.update_button.configure(text="Check & Install")
                    self._refresh_runtime_status()
                elif kind == "browser_session_ready":
                    self._accept_browser_session(msg[1])
                elif kind == "browser_needed":
                    self._prompt_browser_verification(msg[1], msg[2])
                elif kind == "browser_session_error":
                    self.browser_verify_button.configure(
                        state="normal", text="Open Verification Window"
                    )
                    self.browser_session_status_var.set("Verification did not complete.")
                    log_messages.append(f"Browser verification failed safely: {msg[1]}")
        except queue.Empty:
            pass
        if self._current_page == "Queue":
            for task_id, title in latest_title.items():
                self._update_task_title(task_id, title)
            for task_id, progress in latest_progress.items():
                self._update_task_progress(task_id, progress)
            for task_id, metrics in latest_metrics.items():
                self._update_task_metrics(task_id, *metrics)
            for task_id, status in latest_status.items():
                self._update_task_status(task_id, status)
        elif latest_status:
            self._refresh_stats()
        if history_refresh:
            if self._current_page == "History":
                self._history_visible_limit = HISTORY_PAGE_SIZE
                self._rebuild_history_list(
                    self.history_search_entry.get(), force=True
                )
            else:
                self._history_view_dirty = True
            self._refresh_stats()
        if log_messages:
            self._append_log_batch(log_messages)
        delay = UI_QUEUE_BUSY_INTERVAL_MS if not self.ui_queue.empty() else UI_QUEUE_INTERVAL_MS
        self._ui_queue_after_id = self.after(delay, self.process_ui_queue)

    def _append_log(self, text):
        self._append_log_batch([text])

    def _append_log_batch(self, messages):
        if getattr(self, "_current_page", "Queue") != "Queue" and hasattr(self, "pages"):
            for message in messages:
                self._deferred_log_messages.extend(
                    str(message).rstrip("\r\n").splitlines() or [""]
                )
            if len(self._deferred_log_messages) > MAX_LOG_LINES:
                self._deferred_log_messages = self._deferred_log_messages[-MAX_LOG_LINES:]
            return
        try:
            view = self.log_textbox.yview()
            should_follow = not view or view[1] >= 0.98
        except Exception:
            should_follow = True
        timestamp = time.strftime("%H:%M:%S")
        tagged_lines = []
        for message in messages:
            for line in str(message).rstrip("\r\n").splitlines() or [""]:
                upper = line.upper()
                tag = "error" if "ERROR" in upper or "FAILED" in upper else (
                    "warning" if "WARNING" in upper or "NOTICE" in upper else "info"
                )
                tagged_lines.append((f"> {timestamp}  {line}\n", tag))
        self.log_textbox.configure(state="normal")
        for payload, tag in tagged_lines:
            try:
                self.log_textbox.insert("end", payload, tag)
            except TypeError:
                self.log_textbox.insert("end", payload)
        self._log_line_count = getattr(self, "_log_line_count", 0) + len(tagged_lines)
        overflow = self._log_line_count - MAX_LOG_LINES
        if overflow > 0:
            self.log_textbox.delete("1.0", f"{overflow + 1}.0")
            self._log_line_count -= overflow
        if should_follow:
            self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")
        if hasattr(self, "log_status_label"):
            self.log_status_label.configure(text=f"  /  {self._log_line_count:04d} OF {MAX_LOG_LINES} LINES")

    def clear_log(self):
        self._deferred_log_messages = []
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")
        self._log_line_count = 0
        if hasattr(self, "log_status_label"):
            self.log_status_label.configure(text=f"  /  0000 OF {MAX_LOG_LINES} LINES")

    def check_ffmpeg(self):
        bundled = get_bundled_ffmpeg_dir()
        if bundled:
            self.ui_queue.put(("log", f"Using bundled FFmpeg: {bundled}"))
        elif shutil.which("ffmpeg") is None:
            self.ui_queue.put(("log",
                "WARNING: FFmpeg was not found (not bundled, not on PATH). Audio conversion, "
                "embedding, and trimming will fail until it is available. See the setup instructions."))
        else:
            self.ui_queue.put(("log", "FFmpeg detected on system PATH. You're all set!"))
        deno_dir = get_bundled_deno_dir()
        if deno_dir:
            self.ui_queue.put(("log", f"Using bundled Deno runtime: {deno_dir}"))
        elif shutil.which("deno") is None:
            self.ui_queue.put(("log", "NOTICE: Deno was not found. Most downloads still work, but "
                                      "some YouTube formats may require a packaged or system Deno runtime."))
        else:
            self.ui_queue.put(("log", "Deno runtime detected on system PATH."))

    # ------------------------------------------------------------------
    # Queue tab: task rows
    # ------------------------------------------------------------------

    def add_task_row(self, task):
        self._refresh_queue_empty_state()
        row = ctk.CTkFrame(
            self.queue_list_frame, fg_color=COLOR_CARD, corner_radius=CONTROL_RADIUS,
            border_width=0,
        )
        row.pack(fill="x", padx=4, pady=5)
        row.columnconfigure(1, weight=1)

        icon_name, icon_color = _kind_icon_and_color(task.mode)
        chip = self._icon_chip(row, icon_name, icon_color, size=40, glyph_size=18)
        chip.grid(row=0, column=0, rowspan=3, padx=(15, 13), pady=15, sticky="n")

        title_label = ctk.CTkLabel(
            row, text=task.title or task.url, anchor="w",
            font=ui_font(FONT_SECTION_TITLE, "bold"), text_color=COLOR_TEXT,
        )
        title_label.grid(row=0, column=1, sticky="ew", pady=(13, 1))
        mode_name = "AUDIO EXTRACT" if task.mode == "audio" else (
            "CUSTOM COMMAND" if task.mode == "custom" else "VIDEO DOWNLOAD"
        )
        metadata_label = ctk.CTkLabel(
            row, text=f"{mode_name}  /  {task.url}", anchor="w",
            font=mono_font(FONT_MICRO), text_color=COLOR_TEXT_DIM,
        )
        metadata_label.grid(row=1, column=1, sticky="ew", pady=(0, 7))

        progress_row = layout_frame(row)
        progress_row.grid(row=2, column=1, sticky="ew", pady=(0, 13), padx=(0, 8))
        progress_row.columnconfigure(0, weight=1)
        progress_bar = ctk.CTkProgressBar(
            progress_row, height=7, corner_radius=4,
            progress_color=COLOR_ACCENT, fg_color=COLOR_CARD_ALT,
        )
        progress_bar.set(0)
        progress_bar.grid(row=0, column=0, sticky="ew")
        percent_label = ctk.CTkLabel(
            progress_row, text="000%", width=42, anchor="e",
            font=mono_font(FONT_MICRO, "bold"), text_color=COLOR_TEXT_MUTED,
        )
        percent_label.grid(row=0, column=1, padx=(8, 0))

        controls = layout_frame(row)
        controls.grid(row=0, column=2, rowspan=3, padx=(7, 14), pady=13, sticky="e")
        status_label = ctk.CTkLabel(
            controls, text="QUEUED", font=mono_font(FONT_MICRO, "bold"),
            fg_color=COLOR_BORDER_STRONG, text_color=COLOR_TEXT_ON_ACCENT,
            corner_radius=6, width=90, height=22,
        )
        status_label.pack(anchor="e", pady=(0, 8))
        button_row = layout_frame(controls)
        button_row.pack(anchor="e")
        cancel_btn = ctk.CTkButton(
            button_row, text="Cancel", width=68, height=30, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_SURFACE_ELEVATED, hover_color=COLOR_SURFACE_HOVER,
            text_color=COLOR_TEXT, command=lambda tid=task.id: self.cancel_task(tid),
        )
        cancel_btn.pack(side="left", padx=(0, 5))
        retry_btn = ctk.CTkButton(
            button_row, text="Retry", width=64, height=30, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_SURFACE_ELEVATED, hover_color=COLOR_ACCENT_SOFT_HOVER,
            text_color=COLOR_TEXT, command=lambda tid=task.id: self.retry_task(tid),
        )
        retry_btn.configure(state="disabled")
        retry_btn.pack(side="left", padx=(0, 5))
        remove_btn = ctk.CTkButton(
            button_row, text="Remove", width=68, height=30, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_SURFACE_ELEVATED, hover_color=COLOR_ERROR,
            text_color=COLOR_TEXT, command=lambda tid=task.id: self.remove_task(tid),
        )
        remove_btn.configure(state="disabled")
        remove_btn.pack(side="left")

        self.task_widgets[task.id] = {
            "frame": row, "title": title_label, "metadata": metadata_label,
            "status": status_label, "progress": progress_bar,
            "percent": percent_label, "cancel_btn": cancel_btn,
            "retry_btn": retry_btn, "remove_btn": remove_btn,
        }
        self._mac_bind_scroll_recursive(row, self.queue_list_frame)
        self._refresh_stats()

    def _update_task_progress(self, task_id, percent):
        widget_set = self.task_widgets.get(task_id)
        if widget_set:
            value = max(0.0, min(1.0, float(percent)))
            widget_set["progress"].set(value)
            widget_set["percent"].configure(text=f"{round(value * 100):03d}%")

    def _update_task_status(self, task_id, status):
        widget_set = self.task_widgets.get(task_id)
        if not widget_set:
            return
        colors = {
            "queued": COLOR_BORDER_STRONG, "downloading": COLOR_ACCENT,
            "completed": COLOR_SUCCESS, "error": COLOR_ERROR, "canceled": COLOR_WARNING,
        }
        widget_set["status"].configure(text=status.upper(), fg_color=colors.get(status, COLOR_BORDER_STRONG))
        if status in ("completed", "error", "canceled"):
            widget_set["cancel_btn"].configure(state="disabled")
            widget_set["remove_btn"].configure(state="normal")
            widget_set["retry_btn"].configure(
                state="normal" if status in ("error", "canceled") else "disabled"
            )
        else:
            widget_set["cancel_btn"].configure(state="normal")
            widget_set["retry_btn"].configure(state="disabled")
            widget_set["remove_btn"].configure(state="disabled")
        self._refresh_stats()

    def _update_task_metrics(self, task_id, stage, speed="", eta=""):
        widget_set = self.task_widgets.get(task_id)
        task = self._find_task(task_id)
        if not widget_set or task is None:
            return
        mode_name = "AUDIO EXTRACT" if task.mode == "audio" else (
            "CUSTOM COMMAND" if task.mode == "custom" else "VIDEO DOWNLOAD"
        )
        details = [mode_name, stage]
        if speed:
            details.append(speed)
        if eta:
            details.append(f"ETA {eta}")
        widget_set["metadata"].configure(text="  /  ".join(details))

    def _update_task_title(self, task_id, title):
        widget_set = self.task_widgets.get(task_id)
        if widget_set:
            widget_set["title"].configure(text=title)

    def _find_task(self, task_id):
        with self.tasks_lock:
            for t in self.tasks:
                if t.id == task_id:
                    return t
        return None

    def cancel_task(self, task_id):
        adapter = getattr(self, "_core_adapter", None)
        if adapter is not None and adapter.scheduler.get(task_id) is not None:
            adapter.cancel(task_id)
            return
        event = self.cancel_events.get(task_id)
        if event:
            event.set()
        task = self._find_task(task_id)
        if task is None:
            return
        if task.status == "queued":
            task.status = "canceled"
            self.ui_queue.put(("task_status", task_id, "canceled"))
            self.ui_queue.put(("log", f"Canceled before starting: {task.url}"))
        elif task.status == "downloading" and task.process:
            self._terminate_process_tree(task.process)
    def retry_task(self, task_id):
        adapter = getattr(self, "_core_adapter", None)
        if adapter is not None and adapter.scheduler.get(task_id) is not None:
            adapter.retry(task_id)
            return
        task = self._find_task(task_id)
        if task is None or task.status not in ("error", "canceled"):
            return
        task.status = "queued"
        task.progress = 0.0
        task.error = ""
        task.process = None
        task.stage = "Waiting"
        task.speed = ""
        task.eta = ""
        self.cancel_events[task_id] = threading.Event()
        self.ui_queue.put(("task_progress", task_id, 0.0))
        self.ui_queue.put(("task_status", task_id, "queued"))
        self.ui_queue.put(("task_metrics", task_id, "Waiting", "", ""))
        self.ui_queue.put(("log", f"Retry queued: {task.url}"))
        self._queue_wakeup.set()
    def remove_task(self, task_id):
        adapter = getattr(self, "_core_adapter", None)
        if adapter is not None:
            adapter.remove(task_id)
        with self.tasks_lock:
            self.tasks = [t for t in self.tasks if t.id != task_id]
        widgets = self.task_widgets.pop(task_id, None)
        if widgets:
            widgets["frame"].destroy()
        self.cancel_events.pop(task_id, None)
        self._refresh_stats()

    def clear_completed(self):
        adapter = getattr(self, "_core_adapter", None)
        if adapter is not None:
            adapter.clear_finished()
        with self.tasks_lock:
            to_remove = [t.id for t in self.tasks if t.status in ("completed", "error", "canceled")]
        for tid in to_remove:
            self.remove_task(tid)

    # ------------------------------------------------------------------
    # Adding downloads to the queue
    # ------------------------------------------------------------------

    def add_to_queue(self):
        try:
            url = validate_media_url(self.url_entry.get())
            output_template = validate_output_template(self.output_template_entry.get())
        except ValueError as exc:
            messagebox.showerror("Check Download Settings", str(exc))
            return

        custom_command = self.custom_command_box.get("1.0", "end").strip()
        use_custom_command = bool(self.use_custom_command_var.get())
        if use_custom_command and not custom_command:
            messagebox.showerror(
                "Custom Command Is Empty",
                "Either enter yt-dlp arguments in the custom command box or turn off "
                "the custom-command checkbox.",
            )
            return
        if use_custom_command:
            try:
                validate_custom_ytdlp_arguments(shlex.split(custom_command))
            except ValueError as exc:
                messagebox.showerror("Custom Command Is Not Allowed", str(exc))
                return
        mode = "custom" if use_custom_command else self.mode_var.get()

        cookie_mode = COOKIE_MODE_MAP.get(self.cookie_mode_menu.get(), "none")
        if cookie_mode == "session" and not getattr(self, "_verified_session", {}).get("ok"):
            messagebox.showerror(
                "Verified Session Required",
                "Open the verification window and finish the website check first.",
            )
            return

        session_candidates = list(
            getattr(self, "_verified_session", {}).get("media_candidates") or []
        )
        selected_candidate = getattr(self, "_browser_candidate_map", {}).get(
            control_value(self, "browser_candidate_menu", "Automatic")
        )
        if selected_candidate:
            session_candidates = [selected_candidate]

        options = {
            "output_folder": self.output_folder,
            "quality": self.quality_menu.get(),
            "fps60": self.fps60_var.get(),
            "audio_format": self.audio_format_menu.get(),
            "mp3_bitrate": control_value(self, "mp3_bitrate_menu", "320 kbps"),
            "impersonation": control_value(self, "impersonation_menu", "Automatic"),
            "download_subs": self.subs_var.get(),
            "sub_langs": self.sub_langs_entry.get().strip(),
            "embed_subs": self.embed_subs_var.get(),
            "auto_captions": self.auto_captions_var.get(),
            "is_playlist": self.playlist_var.get(),
            "playlist_start": self.playlist_start_entry.get().strip(),
            "playlist_end": self.playlist_end_entry.get().strip(),
            "start_time": self.start_time_entry.get().strip(),
            "end_time": self.end_time_entry.get().strip(),
            "cookie_mode": cookie_mode,
            "cookie_browser": self.cookie_browser_menu.get(),
            "cookie_profile": control_value(self, "cookie_profile_entry", "").strip(),
            "cookie_file": self.cookie_file_entry.get().strip(),
            "session_cookie_file": "",
            "session_media_candidates": session_candidates,
            "session_drm_detected": bool(getattr(self, "_verified_session", {}).get("drm_detected")),
            "session_user_agent": getattr(self, "_verified_session", {}).get("user_agent", ""),
            "session_referer": getattr(self, "_verified_session", {}).get("referer", ""),
            "session_origin": getattr(self, "_verified_session", {}).get("origin", ""),
            "session_page_title": getattr(self, "_verified_session", {}).get("page_title", ""),
            "embed_thumbnail": self.embed_thumbnail_var.get(),
            "embed_metadata": self.embed_metadata_var.get(),
            "sponsorblock": self.sponsorblock_var.get(),
            "sponsorblock_categories": self.sponsorblock_entry.get().strip(),
            "proxy": self.proxy_entry.get().strip(),
            "rate_limit": self.rate_limit_entry.get().strip(),
            "force_ipv4": self.force_ipv4_var.get(),
            "restrict_filenames": self.restrict_filenames_var.get(),
            "output_template": output_template,
            "use_archive": self.use_archive_var.get(),
            "format_sort": self.format_sort_entry.get().strip(),
            "allow_remote_components": self.remote_components_var.get(),
            "use_custom_command": use_custom_command,
            "browser_fallback_enabled": not use_custom_command,
            "custom_command": custom_command if use_custom_command else "",
        }

        task = DownloadTask(id=str(uuid.uuid4()), url=url, mode=mode, options=options)
        if cookie_mode == "session" and getattr(self, "_verified_session", {}).get("cookies"):
            BROWSER_SESSION_DIR.mkdir(parents=True, exist_ok=True)
            task_cookie_path = BROWSER_SESSION_DIR / f"task-{task.id}.cookies.txt"
            write_netscape_cookie_file(self._verified_session["cookies"], task_cookie_path)
            task.options["session_cookie_file"] = str(task_cookie_path)
        try:
            self._core_adapter.submit(task)
        except (RuntimeError, ValueError) as exc:
            messagebox.showerror("Queue Unavailable", str(exc))
            return

        self.url_entry.delete(0, "end")
        self.use_custom_command_var.set(False)
        self.show_page("Queue")
        self.ui_queue.put(("log", f"Added to queue: {url}"))
        self.save_settings()

    def _resolve_core_task(self, record):
        """Rehydrate one durable core record into the existing build008 row model."""
        state = record.state.value
        status = {
            "queued": "queued",
            "completed": "completed",
            "failed": "error",
            "cancelled": "canceled",
        }.get(state, "downloading")
        options = record.spec.to_dict()["options"]
        return DownloadTask(
            id=record.task_id,
            url=record.spec.url,
            mode=record.spec.mode,
            options=options,
            status=status,
            progress=record.progress,
            title=record.title,
            output_path=record.output_path,
            error=record.error,
            stage="Waiting" if status == "queued" else "Starting",
            speed=record.speed,
            eta=record.eta,
        )

    def _show_core_task(self, task):
        """Expose a persisted task only after the core has made it durable."""
        if self._find_task(task.id) is not None:
            return
        with self.tasks_lock:
            self.tasks.append(task)
        self.add_task_row(task)
        self._refresh_stats()

    def _protected_browser_command(self, record, result_path):
        return build_self_invocation() + [
            "__vrka_protected_browser__", record.spec.url, str(result_path),
        ]

    def _run_core_direct_attempt(self, task, output_folder, context):
        if task.mode == "custom":
            return self._run_custom_command_task(task, output_folder, context.cancel_event)
        try:
            return self._run_standard_task(task, output_folder, context.cancel_event)
        except YTDLPCommandError as exc:
            # A fast direct-path failure in a browser-recoverable category
            # (Cloudflare challenge, cookie wall, HTTP rejection, expired
            # address, or an extractor-level failure that followed such a
            # first error) continues on the SAME task through the automatic
            # protected-browser fallback.  Genuinely invalid/unrecoverable
            # input (bare "Unsupported URL", DRM, impersonation mechanism
            # errors) stays terminal.
            if not direct_failure_is_browser_recoverable(exc):
                raise
            raise DirectPathEligibleForFallback(
                f"Direct extraction failed ({exc.category}); Browser Fallback eligible",
                category=exc.category,
            ) from exc

    @staticmethod
    def _clear_resolved_handoff_options(task):
        for key in ("resolved_media_url", "resolved_media_headers", "resolved_media_title"):
            task.options.pop(key, None)

    def _resume_protected_browser_transfer(self, task, output_folder, bundle, context):
        """Validate and start one candidate without changing the logical task."""
        context.check_cancelled()
        # Assemble the COMPLETE transfer context before validation so the
        # probe exercises the same conditions as the eventual transfer: the
        # candidate's own handoff headers plus the browser-derived
        # User-Agent/Referer/Origin and the session cookie file.  Validating
        # a weaker synthetic request rejected candidates whose real transfer
        # would have succeeded (the probe saw no cookies/UA/Referer because
        # they were only attached after validation).
        headers = dict(bundle.headers)
        if bundle.user_agent:
            headers.setdefault("User-Agent", bundle.user_agent)
        if bundle.referer:
            headers.setdefault("Referer", bundle.referer)
        if bundle.origin:
            headers.setdefault("Origin", bundle.origin)
        media_kind_value = str(
            getattr(getattr(bundle, "media_kind", None), "value", "") or "")
        candidate = {
            "url": bundle.media_url,
            "headers": headers,
            "content_type": (bundle.expected_content_types or ("",))[0],
            "kind": media_kind_value,
        }
        task.options["resolved_media_url"] = bundle.media_url
        task.options["resolved_media_headers"] = dict(headers)
        if bundle.user_agent:
            task.options["session_user_agent"] = bundle.user_agent
        if bundle.referer:
            task.options["session_referer"] = bundle.referer
        if bundle.origin:
            task.options["session_origin"] = bundle.origin
        if bundle.cookies:
            BROWSER_SESSION_DIR.mkdir(parents=True, exist_ok=True)
            cookie_path = BROWSER_SESSION_DIR / f"task-{task.id}.cookies.txt"
            write_netscape_cookie_file(bundle.cookies, cookie_path)
            task.options["cookie_mode"] = "session"
            task.options["session_cookie_file"] = str(cookie_path)
        override_credit = False
        try:
            validated = self._validate_media_candidate(
                task, candidate, context.cancel_event, context)
            if not validated:
                category = str(getattr(task, "_last_probe_category", "") or "")
                if probe_failure_overridden_by_browser_observation(
                        bundle, category):
                    context.log(
                        "Probe replay was blocked "
                        f"({getattr(task, '_last_probe_kind', '') or 'unknown'} | "
                        f"{getattr(task, '_last_probe_host', '') or 'unknown host'} | "
                        f"{category}), but the protected browser fetched this "
                        "media successfully moments ago; proceeding to the "
                        "gated transfer."
                    )
                    validated = True
                    override_credit = True
            if not validated:
                context.log(
                    "Protected-browser candidate did not validate for transfer "
                    f"({getattr(task, '_last_probe_kind', '') or 'unknown'} | "
                    f"{getattr(task, '_last_probe_host', '') or 'unknown host'} | "
                    f"{getattr(task, '_last_probe_category', '') or 'no-error' })."
                )
                return False
        except (DownloadCanceled, TaskCancelled):
            raise
        except Exception as exc:
            context.log(f"Protected-browser candidate validation failed: {exc}")
            return False

        started = threading.Event()
        flow = threading.Event()
        finished = threading.Event()
        outcome = {"error": None}
        task._handoff_transfer_started = started
        task._handoff_transfer_flow = flow

        def run_transfer():
            try:
                self._run_standard_task(task, output_folder, context.cancel_event)
            except BaseException as exc:
                outcome["error"] = exc
            finally:
                finished.set()

        worker = threading.Thread(
            target=run_transfer,
            name=f"vrka-handoff-{task.id}",
            daemon=True,
        )
        task._handoff_transfer = (worker, started, finished, outcome)
        worker.start()
        deadline = time.monotonic() + 30.0
        while not started.is_set() and not finished.is_set():
            context.check_cancelled()
            if time.monotonic() >= deadline:
                if task.process:
                    self._terminate_process_tree(task.process)
                worker.join(timeout=2.0)
                self._clear_resolved_handoff_options(task)
                return False
            finished.wait(0.1)
        if not started.is_set():
            worker.join(timeout=0.2)
            failure = outcome.get("error")
            if failure:
                context.log(f"Protected-browser candidate stopped before transfer start: {failure}")
            self._clear_resolved_handoff_options(task)
            if external_replay_rejected_by_server(override_credit, failure):
                raise ExternalReplayRejected(
                    "The protected browser fetched this media, but the media "
                    "server rejected the independent transfer replay.")
            return False

        context.log("Protected-browser transfer start was validated for this task.")
        # Transfer start (a before_dl marker) is NOT enough to close the
        # protected browser.  Wait (bounded) for sustained transfer activity:
        # real percentage/ffmpeg progress via the flow event, staging-byte
        # growth, or a successful finish.  A candidate that cannot demonstrate
        # sustained activity is terminated and the fallback tries the next
        # stabilized candidate on the SAME task with the browser still open.
        staging_dir = str(task.options.get("_staging_dir") or "")
        last_bytes = _staging_bytes(staging_dir)
        flow_deadline = time.monotonic() + TRANSFER_FLOW_GRACE_SECONDS
        while not flow.is_set() and not finished.is_set():
            context.check_cancelled()
            if time.monotonic() >= flow_deadline:
                context.log(
                    "Transfer start was validated but no sustained transfer activity was "
                    "observed; trying another media candidate within this task."
                )
                if task.process:
                    self._terminate_process_tree(task.process)
                worker.join(timeout=2.0)
                self._clear_resolved_handoff_options(task)
                if external_replay_rejected_by_server(override_credit,
                                                      outcome.get("error")):
                    raise ExternalReplayRejected(
                        "The protected browser fetched this media, but the media "
                        "server rejected the independent transfer replay.")
                return False
            current_bytes = _staging_bytes(staging_dir)
            if current_bytes > last_bytes:
                flow.set()
                break
            time.sleep(0.2)
        if flow.is_set():
            context.log("Sustained transfer activity was observed for this task.")
            return True
        if finished.is_set() and not outcome.get("error"):
            context.log("Protected-browser transfer completed for this task.")
            return True
        worker.join(timeout=0.2)
        failure = outcome.get("error")
        if failure:
            context.log(f"Protected-browser candidate failed after transfer start: {failure}")
        self._clear_resolved_handoff_options(task)
        if external_replay_rejected_by_server(override_credit, failure):
            raise ExternalReplayRejected(
                "The protected browser fetched this media, but the media "
                "server rejected the independent transfer replay.")
        return False

    def _await_protected_browser_transfer(self, task, context):
        transfer = getattr(task, "_handoff_transfer", None)
        if not transfer:
            return
        worker, _started, finished, outcome = transfer
        while not finished.wait(0.1):
            context.check_cancelled()
        worker.join(timeout=1.0)
        task._handoff_transfer = None
        if outcome.get("error"):
            raise outcome["error"]

    def _run_browser_context_transfer(self, episode, task, output_folder,
                                      bundle, context):
        """Generic browser-context transfer: the protected browser fetched
        the media (HTTP 200) and independent replay was refused, so the
        session-wide capture collects the bodies the player itself fetches
        while the USER watches.  VRKA never automates the player: the user
        plays/mutes/seeks freely; capture, coverage accounting, assembly
        and validation are VRKA's job.  Activated only after
        ExternalReplayRejected; bounded; cancellable."""
        context.check_cancelled()
        context.log(
            "Protected browser is required for this download. Keep it open "
            "until the download finishes. You may mute the video and "
            "continue using your computer normally.")
        episode.request_media_capture()
        try:
            capture = self._collect_browser_capture(episode, context)
        except BrowserFallbackError as exc:
            # The user closed the protected browser (or it exited) before
            # the capture finished: a clean cancellation of the
            # browser-context transfer - never a media/candidate failure.
            raise BrowserContextCancelled(
                "Protected browser closed. Browser-context download was "
                "cancelled.") from exc
        objects = [o for o in (capture or {}).get("objects", [])
                   if int(o.get("bytes") or 0) > 0]
        if not objects:
            raise BrowserContextCancelled(
                "Protected browser closed. Browser-context download was "
                "cancelled: no media bytes were captured during playback.")
        objects_dir = Path(capture["objects_dir"])
        try:
            episode.cleanup_paths = tuple(episode.cleanup_paths) + (objects_dir,)
        except (AttributeError, TypeError):
            pass
        staging_dir = task.options.get("_staging_dir")
        assembled_path = Path(staging_dir) / "browser-context-media" / "assembled.bin"
        assembled_path.parent.mkdir(parents=True, exist_ok=True)
        report = assemble_browser_capture(
            capture.get("objects", []), objects_dir, assembled_path,
            capture.get("redirects") or {})
        if not report.get("assembled"):
            raise RuntimeError(
                f"capture assembly failed: {report.get('reason', 'unknown')}")
        summary = self._probe_media_summary(str(assembled_path))
        duration = float(
            ((summary or {}).get("format") or {}).get("duration") or 0.0)
        expected = float(getattr(bundle, "expected_duration_seconds", 0.0) or 0.0)
        # Coverage honesty: ffprobe duration on fMP4 reports the DECLARED
        # stream duration, not the captured byte coverage - the only
        # trustworthy completeness evidence is the playlist's segment list.
        # Playlist mode with missing segments is an honest partial capture.
        if (report.get("mode") == "playlist"
                and report.get("missing")
                and report.get("playlist_segments")):
            raise RuntimeError(
                f"browser capture covered only "
                f"{report.get('segments', 0)} of "
                f"{report.get('playlist_segments')} playlist segments; "
                "let the protected browser play the full media for a "
                "complete capture")
        if duration <= 0 and not (summary or {}).get("streams"):
            from vrka_core.media_assembly import classify as _classify_capture
            composition = {}
            for entry in capture.get("objects", []):
                kind = _classify_capture(
                    entry.get("url", ""), entry.get("content_type", ""))
                composition[kind] = composition.get(kind, 0) + 1
            raise RuntimeError(
                "assembled media failed validation "
                f"(mode={report.get('mode')}, segments={report.get('segments')}, "
                f"bytes={report.get('bytes')}, composition={composition})")
        final_path = self._place_browser_context_output(
            task, output_folder, assembled_path, summary or {})
        if not task.title:
            task.title = bundle.referer or task.url
        task.output_path = str(final_path)
        context.progress(
            1.0, title=task.title, output_path=str(final_path))
        context.log("Browser-context transfer finished; media validated.")
        return True

    BROWSER_CAPTURE_WAIT_SECONDS = 120.0
    BROWSER_CAPTURE_SETTLE_SECONDS = 8.0
    BROWSER_CAPTURE_PLAYTHROUGH_SECONDS = 5400.0
    BROWSER_CAPTURE_SEEK_SECONDS = 7.0

    BROWSER_CAPTURE_WAIT_SECONDS = 120.0
    BROWSER_CAPTURE_SETTLE_SECONDS = 8.0
    BROWSER_CAPTURE_PLAYTHROUGH_SECONDS = 5400.0
    BROWSER_CAPTURE_SEEK_SECONDS = 7.0
    BROWSER_CAPTURE_SEEK_SPACING_SECONDS = 45.0

    BROWSER_CAPTURE_WAIT_SECONDS = 120.0
    BROWSER_CAPTURE_SETTLE_SECONDS = 8.0
    BROWSER_CAPTURE_PLAYTHROUGH_SECONDS = 5400.0
    BROWSER_CAPTURE_SEEK_SECONDS = 7.0
    BROWSER_CAPTURE_SEEK_SPACING_SECONDS = 45.0

    BROWSER_CAPTURE_WAIT_SECONDS = 120.0
    BROWSER_CAPTURE_SETTLE_SECONDS = 8.0
    BROWSER_CAPTURE_PLAYTHROUGH_SECONDS = 5400.0

    def _collect_browser_capture(self, episode, context):
        """Passive coverage monitor for the browser-context transfer.

        The USER drives playback (and any seeking) in the protected browser;
        VRKA never moves the cursor, clicks, or otherwise automates the
        player.  This loop only polls the helper's capture state, tracks
        playlist coverage with the pure CoverageModel, reports progress,
        and returns when coverage is complete, growth has settled, the
        playthrough bound is reached, or the user closes the browser
        (surfaced as a clean cancellation by the caller)."""
        from vrka_core.coverage import model_from_urls, parse_playlist
        deadline = time.monotonic() + self.BROWSER_CAPTURE_WAIT_SECONDS
        playthrough_deadline = (time.monotonic()
                                + self.BROWSER_CAPTURE_PLAYTHROUGH_SECONDS)
        last_bytes = -1
        best = {}
        last_growth = time.monotonic()
        last_progress_log = 0.0
        objects_dir = None
        model = None
        model_urls = None
        while True:
            now = time.monotonic()
            coverage_known = model is not None
            bound = (playthrough_deadline
                     if coverage_known and now < playthrough_deadline
                     else deadline)
            if now >= bound:
                return best
            context.check_cancelled()
            episode.request_capture()
            payload = episode.capture(context.cancel_event,
                                      since_seq=int(getattr(episode, "_capture_seq", 0)))
            try:
                episode._capture_seq = int(payload.get("capture_seq") or 0)
            except Exception:
                pass
            capture = payload.get("media_capture") or {}
            current = int(capture.get("total_bytes") or 0)
            if current > int(best.get("total_bytes") or 0):
                best = capture
            if capture.get("stopped"):
                return best or capture
            if current > last_bytes:
                last_bytes = current
                last_growth = time.monotonic()
            if best.get("objects") and best.get("objects_dir"):
                if objects_dir is None:
                    objects_dir = Path(best["objects_dir"])
                if model is None:
                    for entry in reversed(best.get("objects", [])):
                        if ".m3u8" not in entry.get("url", "").lower():
                            continue
                        playlist_file = objects_dir / entry.get("object", "")
                        if not playlist_file.is_file():
                            continue
                        text = playlist_file.read_text(errors="replace")
                        if "#EXTINF" not in text:
                            continue
                        times, urls, _total = parse_playlist(
                            text, entry.get("url", ""))
                        if times:
                            model = model_from_urls(times, urls, set())
                            model_urls = urls
                            break
                if model is not None:
                    captured_urls = {
                        o.get("url", "").split("?")[0]
                        for o in best.get("objects", [])
                        if int(o.get("bytes") or 0) > 0}
                    for url in captured_urls:
                        index = model_urls.index(url) if url in model_urls else None
                        if index is not None:
                            model.mark_captured(index)
                    if model.is_complete():
                        context.log(
                            "Browser-context capture covers the complete "
                            f"playlist ({len(model.segment_times)} segments).")
                        return best
            if current > last_bytes:
                last_growth = time.monotonic()
            if (time.monotonic() - last_growth
                    >= self.BROWSER_CAPTURE_SETTLE_SECONDS
                    and now >= deadline):
                return best
            if now - last_progress_log >= 15.0 and last_bytes > 0:
                last_progress_log = now
                coverage_note = ""
                if model is not None:
                    coverage_note = (
                        f" Coverage: {len(model.captured)} of "
                        f"{len(model.segment_times)} playlist segments.")
                context.log(
                    "Browser-context capture in progress "
                    f"({last_bytes // (1024 * 1024)} MB captured). Keep the "
                    "protected browser open and playing until the download "
                    f"finishes.{coverage_note}")
            time.sleep(1.0)

    def _probe_media_summary(self, path):
        """ffprobe JSON summary (format + streams) for a captured file."""
        ffprobe_exe = "ffprobe"
        ffmpeg_dir = get_bundled_ffmpeg_dir()
        if ffmpeg_dir:
            candidate = os.path.join(ffmpeg_dir, "ffprobe.exe" if os.name == "nt" else "ffprobe")
            if os.path.isfile(candidate):
                ffprobe_exe = candidate
        try:
            creation_kwargs = {}
            if os.name == "nt":
                creation_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                [ffprobe_exe, "-v", "error", "-show_entries",
                 "format=format_name,duration:stream=codec_type,codec_name",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=60, **creation_kwargs)
            return json.loads(result.stdout or "{}")
        except Exception:
            return None

    def _place_browser_context_output(self, task, output_folder, assembled_path,
                                      summary):
        formats = str(summary.get("format", {}).get("format_name") or "")
        extension = ".ts" if "mpegts" in formats else ".mp4"
        base_title = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]', "_", (task.title or "media"))[:120].strip(" .") or "media"
        output_dir = Path(output_folder)
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / f"{base_title} (browser-captured){extension}"
        index = 2
        while final_path.exists():
            final_path = Path(output_folder) / (
                f"{base_title} (browser-captured {index}){extension}")
            index += 1
        assembled_path.replace(final_path)
        return final_path

    def _execute_core_task(self, task, context):
        """Run one unchanged build008 task through the durable core lifecycle."""
        record = getattr(task, "_core_record", None)
        if record is None:
            raise RuntimeError("Build010 core record is unavailable for this task")
        staging_path = STAGING_DIR / str(uuid.uuid4())
        task._core_context = context
        context.log(f"Starting: {task.url}")
        try:
            output_path = Path(task.options["output_folder"]).expanduser()
            if output_path.exists() and not output_path.is_dir():
                raise NotADirectoryError("The selected output path is not a folder.")
            output_path.mkdir(parents=True, exist_ok=True)
            output_folder = str(output_path.resolve())
            STAGING_DIR.mkdir(parents=True, exist_ok=True)
            staging_path.mkdir()
            task.options["_staging_dir"] = str(staging_path)

            direct = lambda _record, active_context: self._run_core_direct_attempt(
                task, output_folder, active_context,
            )
            # Interactive sites (server selection -> player load -> Play ->
            # media appearance) need longer than the 45 s default: the widget
            # wait must not expire before the user-driven interaction can bring
            # the requested media into the store.  120 s is the bounded design
            # maximum; the wait still ends early the moment a user-started
            # candidate appears.
            browser = ProtectedBrowserFallback(
                self._protected_browser_launcher,
                lambda bundle, active_context: self._resume_protected_browser_transfer(
                    task, output_folder, bundle, active_context,
                ),
                browser_context_transfer=(
                    lambda episode, bundle, active_context:
                    self._run_browser_context_transfer(
                        episode, task, output_folder, bundle, active_context)),
                interaction_wait_seconds=120.0,
            )
            AutomaticFallbackExecutor(
                direct,
                browser,
                enabled=lambda current: (
                    current.spec.mode != "custom"
                    and bool(current.spec.options.get("browser_fallback_enabled", True))
                ),
            )(record, context)
            self._await_protected_browser_transfer(task, context)
            context.progress(
                1.0,
                title=task.title or None,
                output_path=task.output_path or None,
                speed=task.speed or None,
                eta=task.eta or None,
            )
            context.log(f"Completed: {task.title or task.url}")
        except DownloadCanceled as exc:
            raise TaskCancelled(task.id) from exc
        except TaskCancelled:
            raise
        except Exception as exc:
            context.log(f"ERROR [{task.url}]: {exc}")
            raise
        finally:
            self._clear_resolved_handoff_options(task)
            cleanup_task_session_cookie(task)
            _safe_remove_staging_dir(staging_path)
            task.options.pop("_staging_dir", None)
    # ------------------------------------------------------------------
    # Queue worker (background thread)
    # ------------------------------------------------------------------

    def queue_worker(self):
        while not self._shutdown_event.is_set():
            self._queue_wakeup.clear()
            task = None
            with self.tasks_lock:
                for t in self.tasks:
                    if t.status == "queued":
                        task = t
                        break
            if task is None:
                self._queue_wakeup.wait()
                continue
            self.process_task(task)

    def process_task(self, task):
        cancel_event = self.cancel_events.get(task.id, threading.Event())
        task.status = "downloading"
        task.stage = "Starting"
        self.ui_queue.put(("task_status", task.id, "downloading"))
        self.ui_queue.put(("task_metrics", task.id, "Starting", "", ""))
        self.ui_queue.put(("log", f"Starting: {task.url}"))

        staging_path = STAGING_DIR / str(uuid.uuid4())
        try:
            output_path = Path(task.options["output_folder"]).expanduser()
            if output_path.exists() and not output_path.is_dir():
                raise NotADirectoryError("The selected output path is not a folder.")
            output_path.mkdir(parents=True, exist_ok=True)
            output_folder = str(output_path.resolve())
            STAGING_DIR.mkdir(parents=True, exist_ok=True)
            staging_path.mkdir()
            task.options["_staging_dir"] = str(staging_path)

            if task.mode == "custom":
                self._run_custom_command_task(task, output_folder, cancel_event)
            else:
                self._run_standard_task(task, output_folder, cancel_event)

            task.status = "completed"
            task.progress = 100.0
            task.stage = "Completed"
            task.speed = ""
            task.eta = ""
            self.ui_queue.put(("task_progress", task.id, 100.0))
            self.ui_queue.put(("task_metrics", task.id, "Completed", "", ""))
            self.ui_queue.put(("task_status", task.id, "completed"))
            self.ui_queue.put(("log", f"Completed: {task.title or task.url}"))
            self.add_history_entry(task)

        except DownloadCanceled:
            task.status = "canceled"
            self.ui_queue.put(("task_status", task.id, "canceled"))
            self.ui_queue.put(("log", f"Canceled: {task.url}"))
        except YTDLPCommandError as exc:
            task.status = "error"
            task.error = str(exc)
            self.ui_queue.put(("task_status", task.id, "error"))
            self.ui_queue.put(("log", f"ERROR [{task.url}]: {exc}"))
            if should_offer_browser_verification(task.options, exc.category):
                category = (
                    "expired"
                    if task.options.get("cookie_mode") == "session"
                    else exc.category
                )
                self.ui_queue.put(("browser_needed", task.url, category))
        except Exception as exc:
            task.status = "error"
            task.error = str(exc)
            self.ui_queue.put(("task_status", task.id, "error"))
            self.ui_queue.put(("log", f"ERROR [{task.url}]: {exc}"))
        finally:
            cleanup_task_session_cookie(task)
            _safe_remove_staging_dir(staging_path)
            task.options.pop("_staging_dir", None)
    def _validate_media_candidate(self, task, candidate, cancel_event, context=None):
        """Confirm a browser candidate through a task-owned bounded probe."""
        if cancel_event.is_set():
            raise DownloadCanceled()
        _backend, command = build_candidate_probe_command(task, candidate)
        creation_kwargs = {}
        if os.name == "nt":
            creation_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **creation_kwargs,
        )
        task.process = proc
        try:
            result = MonitoredProcessRunner().run(
                proc,
                cancel_event=cancel_event,
                register_process=(context.own_process if context is not None else None),
            )
        except ProcessCancelled as exc:
            raise DownloadCanceled() from exc
        except TimeoutError:
            return False
        finally:
            task.process = None
        if cancel_event.is_set():
            raise DownloadCanceled()
        output = "\n".join(result.output_tail)
        if isinstance(candidate, dict):
            for line in result.output_tail:
                if line.startswith("__VRKA_CANDIDATE_TITLE__"):
                    candidate["probe_title"] = line[len("__VRKA_CANDIDATE_TITLE__"):].strip()
                elif line.startswith("__VRKA_CANDIDATE_DURATION__"):
                    candidate["probe_duration"] = line[len("__VRKA_CANDIDATE_DURATION__"):].strip()
                elif line.startswith("__VRKA_CANDIDATE__"):
                    parts = line[len("__VRKA_CANDIDATE__"):].split("|")
                    if len(parts) >= 7:
                        candidate["probe_format_id"] = parts[0].strip()
                        candidate["probe_resolution"] = parts[1].strip()
                        candidate["probe_ext"] = parts[2].strip()
                        candidate["probe_tbr"] = parts[3].strip()
                        candidate["probe_acodec"] = parts[4].strip()
                        candidate["probe_vcodec"] = parts[5].strip()
                        candidate["probe_height"] = parts[6].strip()
        category = classify_download_error(output)
        # Redacted probe diagnostics: (kind, host, category) only. Never the
        # URL, query, tokens, or cookies.
        try:
            task._last_probe_category = category if result.returncode != 0 else ""
            task._last_probe_host = (
                urllib.parse.urlparse(media_candidate_url(candidate)).hostname or ""
            )
            kind = candidate.get("kind") if isinstance(candidate, dict) else ""
            task._last_probe_kind = str(kind or "")
        except Exception:
            pass
        if category == "drm":
            raise YTDLPCommandError(
                "This media appears to be DRM-protected and cannot be downloaded by VRKA.",
                category="drm",
                output=output,
            )
        transfer_ready = result.returncode == 0 and "__VRKA_CANDIDATE__" in output
        if transfer_ready and isinstance(candidate, dict):
            # Representation completeness: a video task must hand off a
            # candidate that resolves to audio+video, otherwise the merged
            # file is silent (or pictureless) regardless of user quality.
            ok, reason = candidate_satisfies_task_mode(candidate, getattr(task, "mode", ""))
            if not ok:
                try:
                    task._last_probe_category = reason
                except Exception:
                    pass
                return False
        return transfer_ready
    def _run_standard_task(self, task, output_folder, cancel_event):
        return self._run_standard_subprocess_task(task, output_folder, cancel_event)

    def _execute_ytdlp_command(self, task, command, cancel_event, backend):
        """Run yt-dlp through the core-owned monitored process boundary."""
        self.ui_queue.put((
            "log",
            f"[runtime] yt-dlp {backend.version} ({backend.source})",
        ))
        self.ui_queue.put(("log", f"[yt-dlp] {sanitize_command_for_log(command)}"))
        creation_kwargs = {}
        if os.name == "nt":
            creation_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **creation_kwargs,
        )
        task.process = proc
        core_context = getattr(task, "_core_context", None)
        percent_pattern = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
        metrics_pattern = re.compile(r"\bat\s+(\S+/s)\s+ETA\s+([0-9:]+)")
        output_tail = []
        last_progress_emit = 0.0
        last_progress_log = 0.0

        def on_line(raw_line):
            nonlocal last_progress_emit, last_progress_log
            line = raw_line.rstrip()
            if not line:
                return
            if line.startswith("__VRKA_TITLE__"):
                # ``--print before_dl:`` is emitted by yt-dlp immediately before
                # the real transfer begins. HLS streams downloaded through the
                # format-merge/ffmpeg path emit no ``[download]`` progress lines,
                # so this marker is the only validated transfer-start evidence
                # for them; without it the handoff deadline would kill a healthy
                # download.
                handoff_signal = getattr(task, "_handoff_transfer_started", None)
                if handoff_signal is not None:
                    handoff_signal.set()
                task.title = line[len("__VRKA_TITLE__"):].strip() or task.title
                if task.title:
                    self.ui_queue.put(("task_title", task.id, task.title))
                    if core_context is not None:
                        core_context.progress(task.progress, title=task.title)
                return
            handoff_signal = getattr(task, "_handoff_transfer_started", None)
            if line.startswith("[download] Destination:") and handoff_signal is not None:
                handoff_signal.set()
            if line.startswith("__VRKA_OUTPUT__"):
                if handoff_signal is not None:
                    handoff_signal.set()
                task.output_path = line[len("__VRKA_OUTPUT__"):].strip()
                if core_context is not None:
                    core_context.progress(task.progress, output_path=task.output_path)
                return
            line = normalize_subtitle_message(line)
            output_tail.append(line)
            if len(output_tail) > 200:
                del output_tail[:50]
            flow_signal = getattr(task, "_handoff_transfer_flow", None)
            if flow_signal is not None and (
                percent_pattern.search(line)
                or (" time=" in line and _FFMPEG_TIME_RE.search(line))
            ):
                # Sustained transfer activity evidence: real percentage
                # progress or ffmpeg frame/time progression (HLS/ffmpeg paths
                # print no ordinary ``[download]`` percentage lines).  A
                # before_dl/``[download] Destination:`` marker alone does NOT
                # set this - bytes must demonstrably be flowing.
                flow_signal.set()
            match = percent_pattern.search(line)
            if match:
                try:
                    progress = min(max(float(match.group(1)) / 100.0, 0.0), 1.0)
                    if handoff_signal is not None:
                        handoff_signal.set()
                    task.progress = progress
                    task.stage = "Downloading"
                    metrics_match = metrics_pattern.search(line)
                    if metrics_match:
                        task.speed, task.eta = metrics_match.groups()
                    now = time.monotonic()
                    if progress >= 1.0 or now - last_progress_emit >= PROGRESS_EMIT_INTERVAL_SECONDS:
                        self.ui_queue.put(("task_progress", task.id, progress))
                        self.ui_queue.put((
                            "task_metrics", task.id, task.stage, task.speed, task.eta,
                        ))
                        if core_context is not None:
                            core_context.progress(
                                progress,
                                title=task.title or None,
                                output_path=task.output_path or None,
                                speed=task.speed or None,
                                eta=task.eta or None,
                            )
                        last_progress_emit = now
                    if progress >= 1.0 or now - last_progress_log >= PROGRESS_LOG_INTERVAL_SECONDS:
                        self.ui_queue.put(("log", f"[{task.title or task.url}] {line}"))
                        last_progress_log = now
                except ValueError:
                    pass
                return
            stage = task.stage
            if line.startswith("[Merger]"):
                stage = "Merging"
            elif line.startswith(("[ExtractAudio]", "[VideoConvertor]")):
                stage = "Converting"
            elif line.startswith(("[Metadata]", "[EmbedThumbnail]", "[EmbedSubtitle]")):
                stage = "Finalizing"
            if stage != task.stage:
                task.stage = stage
                self.ui_queue.put((
                    "task_metrics", task.id, stage, task.speed, task.eta,
                ))
            self.ui_queue.put(("log", f"[{task.title or task.url}] {line}"))

        def _staging_byte_probe():
            """Total bytes currently written into this task's staging area.
            Lets the watchdog treat REAL byte growth as transfer activity even
            when yt-dlp's percentage output stalls on a rate-limited HLS
            fragment."""
            try:
                staging = str(task.options.get("_staging_dir") or "")
                if not staging:
                    return None
                total = 0
                for path in Path(staging).rglob("*"):
                    if path.is_file():
                        total += path.stat().st_size
                return float(total)
            except OSError:
                return None

        try:
            result = MonitoredProcessRunner(activity_probe=_staging_byte_probe).run(
                proc,
                cancel_event=cancel_event,
                register_process=(core_context.own_process if core_context is not None else None),
                on_line=on_line,
            )
        except ProcessCancelled as exc:
            raise DownloadCanceled() from exc
        finally:
            task.process = None
        if cancel_event.is_set():
            raise DownloadCanceled()
        if result.returncode != 0:
            output = "\n".join(output_tail or result.output_tail)
            category, friendly = format_download_error(output or f"Exit code {result.returncode}")
            raise YTDLPCommandError(friendly, category=category, output=output)
    def _run_standard_subprocess_task(self, task, output_folder, cancel_event):
        opts = task.options
        if opts.get("session_drm_detected"):
            raise YTDLPCommandError(
                "The verification window detected encrypted media. VRKA will not bypass DRM.",
                category="drm",
            )
        trim_enabled = opts.get("trim_enabled")
        if trim_enabled is False:
            wants_trim = False
            start_sec = None
            end_sec = None
        elif trim_enabled is True:
            start_sec = parse_time_to_seconds(opts.get("start_time", ""))
            end_sec = parse_time_to_seconds(opts.get("end_time", ""))
            wants_trim = (start_sec is not None and start_sec > 0) or (end_sec is not None and end_sec > 0) or (start_sec is not None and end_sec is not None and start_sec < end_sec)
        else:
            # Legacy/test callers without explicit trim_enabled boolean
            start_sec = parse_time_to_seconds(opts.get("start_time", ""))
            end_sec = parse_time_to_seconds(opts.get("end_time", ""))
            wants_trim = (start_sec is not None and start_sec > 0) or (end_sec is not None and end_sec > 0)
        ffmpeg_dir = get_bundled_ffmpeg_dir()

        if opts.get("use_archive"):
            _archive_path, migrated = migrate_download_archive(output_folder)
            if migrated:
                self.ui_queue.put((
                    "log",
                    "Imported legacy download archive records from: " + ", ".join(migrated),
                ))

        def execute(format_override=None, section=None, extra=None):
            backend, command = build_standard_ytdlp_command(
                task,
                output_folder,
                format_override=format_override,
                download_section=section,
                extra_arguments=extra,
            )
            self._execute_ytdlp_command(task, command, cancel_event, backend)

        def execute_with_recovery(format_override=None, section=None):
            try:
                execute(format_override=format_override, section=section)
                return
            except YTDLPCommandError as first_error:
                if cancel_event.is_set():
                    raise DownloadCanceled()
                recovery_error = first_error
                automatic = str(opts.get("impersonation") or "Automatic").lower() == "automatic"
                if first_error.category in ("cloudflare", "http") and automatic:
                    self.ui_queue.put((
                        "log",
                        f"[{task.title or task.url}] Website verification was detected; "
                        "retrying once with Chrome request impersonation.",
                    ))
                    # The direct path discovered this site requires Chrome
                    # request impersonation.  Record it so the protected-browser
                    # handoff probe/transfer can reproduce the same legitimate
                    # browser context instead of failing with a bare 403.
                    opts["_needs_impersonation"] = "chrome"
                    try:
                        execute(
                            format_override=format_override,
                            section=section,
                            extra=[
                                "--impersonate", "chrome",
                                "--extractor-args", "generic:impersonate",
                            ],
                        )
                        return
                    except YTDLPCommandError as retry_error:
                        recovery_error = retry_error
                        recovery_error.prior_categories = (
                            first_error.category,
                        ) + tuple(getattr(first_error, "prior_categories", ()) or ())

                candidates = opts.get("session_media_candidates") or []
                if (
                    candidates
                    and recovery_error.category
                    in ("cloudflare", "http", "unsupported", "expired", "unknown")
                ):
                    for candidate in candidates[:3]:
                        candidate_url = media_candidate_url(candidate)
                        host = urllib.parse.urlparse(candidate_url).hostname or "detected media host"
                        self.ui_queue.put((
                            "log",
                            f"[{task.title or task.url}] Validating a media resource detected "
                            f"by the verified session ({host}).",
                        ))
                        if not self._validate_media_candidate(task, candidate, cancel_event):
                            self.ui_queue.put((
                                "log",
                                f"[{task.title or task.url}] Rejected an unusable media "
                                f"candidate from {host}.",
                            ))
                            continue
                        opts["resolved_media_url"] = candidate_url
                        opts["resolved_media_headers"] = media_candidate_headers(candidate)
                        if candidate_needs_fallback_title(candidate):
                            opts["resolved_media_title"] = browser_fallback_title(
                                opts.get("session_page_title"), task.url
                            )
                        try:
                            execute(format_override=format_override, section=section)
                            return
                        except YTDLPCommandError as candidate_error:
                            candidate_error.prior_categories = tuple(
                                getattr(recovery_error, "prior_categories", ()) or ()
                            )
                            recovery_error = candidate_error
                        finally:
                            opts.pop("resolved_media_url", None)
                            opts.pop("resolved_media_headers", None)
                            opts.pop("resolved_media_title", None)
                raise recovery_error
        requested_height = (
            QUALITY_MAP.get(opts.get("quality")) if task.mode == "video" else None
        )
        if wants_trim:
            final_start = start_sec if start_sec is not None else 0
            final_end = end_sec
            skip_efficient = (
                task.mode == "video"
                and (requested_height is None or requested_height > 1080)
            )
            if not skip_efficient:
                section = (
                    f"*{final_start}-{final_end}"
                    if final_end is not None
                    else f"*{final_start}-inf"
                )
                try:
                    self.ui_queue.put((
                        "log",
                        f"[{task.title or task.url}] Trimming {final_start}s-"
                        f"{final_end if final_end is not None else 'end'} during download.",
                    ))
                    execute_with_recovery(section=section)
                    actual_height = self._probe_video_height(task.output_path, ffmpeg_dir)
                    if (
                        requested_height
                        and actual_height
                        and actual_height < requested_height - 5
                    ):
                        self.ui_queue.put((
                            "log",
                            f"[{task.title or task.url}] The efficient clip was only "
                            f"{actual_height}p; retrying as a full-quality download.",
                        ))
                        try:
                            os.remove(task.output_path)
                        except OSError:
                            pass
                        task.output_path = ""
                    else:
                        return
                except DownloadCanceled:
                    raise
                except Exception as exc:
                    if cancel_event.is_set():
                        raise DownloadCanceled()
                    self.ui_queue.put((
                        "log",
                        f"[{task.title or task.url}] Efficient trim was unavailable ({exc}); "
                        "downloading the full item before a local trim.",
                    ))
                    task.output_path = ""
            else:
                self.ui_queue.put((
                    "log",
                    f"[{task.title or task.url}] Full-quality local trimming is used "
                    "for this quality selection.",
                ))

        try:
            execute_with_recovery()
        except YTDLPCommandError:
            if task.mode != "video" or not opts.get("fps60", False):
                raise
            self.ui_queue.put((
                "log",
                f"[{task.title or task.url}] The preferred 60 FPS format failed; "
                "retrying once at the same quality without requiring 60 FPS.",
            ))
            height = QUALITY_MAP.get(opts.get("quality"))
            execute_with_recovery(format_override=build_video_format(height, False))

        if task.mode == "video":
            delivered_height = self._probe_video_height(task.output_path, ffmpeg_dir)
            if delivered_height:
                self.ui_queue.put((
                    "log",
                    f"[{task.title or task.url}] Delivered at {delivered_height}p.",
                ))

        if wants_trim:
            final_start = start_sec if start_sec is not None else 0
            if task.output_path and os.path.isfile(task.output_path):
                task.output_path = self._trim_local_file(task, final_start, end_sec)
            else:
                self.ui_queue.put((
                    "log",
                    f"[{task.title or task.url}] WARNING: the final file could not be "
                    "located for local trimming; the full download was kept.",
                ))


    def _probe_video_height(self, path, ffmpeg_dir):
        """Uses ffprobe to read the height of a video file's first video stream.
        Returns None if it can't be determined (e.g. audio-only file, missing
        ffprobe, or any other error) - callers must treat None as "unknown",
        not as a failure."""
        if not path or not os.path.isfile(path):
            return None
        ffprobe_exe = "ffprobe"
        if ffmpeg_dir:
            candidate = os.path.join(ffmpeg_dir, "ffprobe.exe" if os.name == "nt" else "ffprobe")
            if os.path.isfile(candidate):
                ffprobe_exe = candidate
        try:
            creation_kwargs = {}
            if os.name == "nt":
                creation_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                [ffprobe_exe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=height", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=15, **creation_kwargs)
            height_str = (result.stdout or "").strip()
            return int(height_str) if height_str.isdigit() else None
        except Exception:
            return None

    def _trim_local_file(self, task, start_sec, end_sec):
        """Cuts [start_sec, end_sec] out of the already-downloaded local file.
        This runs entirely offline (no network involved), using a fast
        stream-copy trim: sample-accurate for audio, snapped to the nearest
        keyframe for video. If it fails for any reason, the full-length file
        is kept instead of losing the download."""
        input_path = task.output_path
        base, ext = os.path.splitext(input_path)
        trimmed_path = f"{base} [trimmed]{ext}"

        ffmpeg_dir = get_bundled_ffmpeg_dir()
        ffmpeg_exe = "ffmpeg"
        if ffmpeg_dir:
            candidate = os.path.join(ffmpeg_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if os.path.isfile(candidate):
                ffmpeg_exe = candidate

        cmd = [ffmpeg_exe, "-y", "-ss", str(start_sec), "-i", input_path]
        if end_sec is not None:
            duration = max(end_sec - start_sec, 0.1)
            cmd += ["-t", str(duration)]
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", trimmed_path]

        end_display = end_sec if end_sec is not None else "end"
        self.ui_queue.put(("log", f"[{task.title or task.url}] Trimming locally: {start_sec}s to {end_display}"))

        creation_kwargs = {}
        if os.name == "nt":
            creation_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, **creation_kwargs)
        except Exception as e:
            self.ui_queue.put(("log", f"[{task.title or task.url}] Trim failed to start ({e}); "
                                       "keeping the full-length file."))
            return input_path

        if result.returncode != 0 or not os.path.isfile(trimmed_path):
            last_line = (result.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
            self.ui_queue.put(("log", f"[{task.title or task.url}] Trim failed, keeping the full-length "
                                       f"file. ({last_line[0]})"))
            return input_path

        try:
            os.remove(input_path)
        except OSError:
            pass

        self.ui_queue.put(("log", f"[{task.title or task.url}] Trim complete."))
        return trimmed_path

    def _run_custom_command_task(self, task, output_folder, cancel_event):
        backend, command = build_custom_ytdlp_command(task, output_folder)
        self.ui_queue.put((
            "log",
            f"[custom] Using yt-dlp {backend.version} ({backend.source}).",
        ))
        self._execute_ytdlp_command(task, command, cancel_event, backend)
        return


    # ------------------------------------------------------------------
    # History (persisted to ~/.vrka/history.json)
    # ------------------------------------------------------------------

    def load_history(self):
        try:
            if HISTORY_FILE.exists():
                if HISTORY_FILE.stat().st_size > MAX_HISTORY_FILE_BYTES:
                    return []
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    value = json.load(f)
                if isinstance(value, list):
                    return [
                        entry for entry in value if isinstance(entry, dict)
                    ][:MAX_HISTORY_ENTRIES]
        except Exception:
            pass
        return []

    def save_history(self):
        try:
            self.history = self.history[:MAX_HISTORY_ENTRIES]
            _atomic_write_json(HISTORY_FILE, self.history)
        except Exception as e:
            self.ui_queue.put(("log", f"Could not save history: {e}"))

    def add_history_entry(self, task):
        entry = {
            "id": str(uuid.uuid4()),
            "title": task.title or task.url,
            "url": task.url,
            "path": task.output_path,
            "mode": task.mode,
            "timestamp": time.strftime("%Y-%m-%d %H:%M"),
        }
        self.history.insert(0, entry)
        self.history = self.history[:MAX_HISTORY_ENTRIES]
        self.save_history()
        self.ui_queue.put(("history_refresh", None))

    def _schedule_history_filter(self, _event=None):
        if self._history_filter_after_id:
            try:
                self.after_cancel(self._history_filter_after_id)
            except Exception:
                pass
        self._history_filter_after_id = self.after(
            HISTORY_SEARCH_DEBOUNCE_MS,
            self._apply_history_filter,
        )

    def _apply_history_filter(self):
        self._history_visible_limit = HISTORY_PAGE_SIZE
        self._rebuild_history_list(self.history_search_entry.get(), force=True)

    def _rebuild_history_list(self, filter_text="", force=False):
        normalized_filter = (filter_text or "").strip().lower()
        if not force and normalized_filter == self._last_history_filter:
            return
        self._last_history_filter = normalized_filter
        self._history_filter_after_id = None
        for child in self.history_list_frame.winfo_children():
            child.destroy()

        matches = []
        for entry in self.history:
            searchable = f"{entry.get('title', '')} {entry.get('path', '')}".lower()
            if normalized_filter and normalized_filter not in searchable:
                continue
            matches.append(entry)

        if not matches:
            empty = layout_frame(self.history_list_frame, bg_color=COLOR_BG)
            empty.pack(fill="x", padx=4, pady=(38, 12))
            empty_image = get_brand_ctk_image(32)
            empty._brand_image = empty_image
            ctk.CTkLabel(empty, text="", image=empty_image).pack(pady=(18, 8))
            title = "No matching archive entries" if normalized_filter else "Archive is empty"
            detail = (
                "Try a different title or path." if normalized_filter else
                "Completed downloads appear here without scanning your entire drive."
            )
            ctk.CTkLabel(
                empty, text=title, font=ui_font(17, "bold"), text_color=COLOR_TEXT,
            ).pack()
            ctk.CTkLabel(
                empty, text=detail, font=ui_font(FONT_SMALL), text_color=COLOR_TEXT_MUTED,
            ).pack(pady=(4, 18))
        else:
            visible_matches = matches[:self._history_visible_limit]
            for entry in visible_matches:
                self._add_history_row(entry)
            remaining = len(matches) - len(visible_matches)
            if remaining > 0:
                load_more = ctk.CTkButton(
                    self.history_list_frame,
                    text=f"Show {min(HISTORY_PAGE_SIZE, remaining)} more",
                    height=CONTROL_HEIGHT,
                    corner_radius=CONTROL_RADIUS,
                    fg_color=COLOR_SURFACE_ELEVATED,
                    hover_color=COLOR_SURFACE_HOVER,
                    text_color=COLOR_TEXT,
                    command=self._show_more_history,
                )
                load_more.pack(pady=12)

        if hasattr(self, "history_count_label"):
            if normalized_filter:
                self.history_count_label.configure(
                    text=f"{len(matches):02d} MATCHES  /  {len(self.history):02d} TOTAL",
                )
            else:
                self.history_count_label.configure(text=f"{len(self.history):02d} ARCHIVED ITEMS")

    def _show_more_history(self):
        self._history_visible_limit += HISTORY_PAGE_SIZE
        self._rebuild_history_list(self.history_search_entry.get(), force=True)

    def _add_history_row(self, entry):
        row = ctk.CTkFrame(
            self.history_list_frame, fg_color=COLOR_CARD, corner_radius=CONTROL_RADIUS,
            border_width=0,
        )
        row.pack(fill="x", padx=4, pady=5)
        row.columnconfigure(1, weight=1)

        icon_name, icon_color = _kind_icon_and_color(entry.get("mode", "video"))
        self._icon_chip(row, icon_name, icon_color, size=40, glyph_size=18).grid(
            row=0, column=0, rowspan=3, padx=(15, 13), pady=15, sticky="n",
        )

        kind = "Audio" if entry.get("mode") == "audio" else (
            "Custom" if entry.get("mode") == "custom" else "Video"
        )
        ctk.CTkLabel(
            row, text=entry.get("title") or entry.get("url", "Untitled"), anchor="w",
            font=ui_font(FONT_SECTION_TITLE, "bold"), text_color=COLOR_TEXT,
        ).grid(row=0, column=1, sticky="ew", pady=(12, 0))
        ctk.CTkLabel(
            row, text=f"{kind.upper()}  /  {entry.get('timestamp', '')}", anchor="w",
            text_color=COLOR_TEXT_MUTED, font=mono_font(FONT_MICRO, "bold"),
        ).grid(row=1, column=1, sticky="ew", pady=(1, 0))
        saved_path = entry.get("path") or "Saved path unavailable"
        ctk.CTkLabel(
            row, text=saved_path, anchor="w", text_color=COLOR_TEXT_DIM,
            font=mono_font(FONT_MICRO),
        ).grid(row=2, column=1, sticky="ew", pady=(1, 12))

        buttons = layout_frame(row)
        buttons.grid(row=0, column=2, rowspan=3, padx=14, sticky="e")
        ctk.CTkButton(
            buttons, text="Open", width=64, height=30, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_ACCENT_SOFT, hover_color=COLOR_ACCENT_SOFT_HOVER,
            text_color=COLOR_TEXT, command=lambda item=entry: self.open_history_item(item),
        ).pack(side="left", padx=(0, 5))
        ctk.CTkButton(
            buttons, text="Again", width=64, height=30, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_SURFACE_ELEVATED, hover_color=COLOR_SURFACE_HOVER,
            text_color=COLOR_TEXT, command=lambda item=entry: self.redownload_from_history(item),
        ).pack(side="left", padx=(0, 5))
        ctk.CTkButton(
            buttons, text="Remove", width=68, height=30, corner_radius=CONTROL_RADIUS,
            fg_color=COLOR_SURFACE_ELEVATED, hover_color=COLOR_ERROR,
            text_color=COLOR_TEXT, command=lambda item=entry: self.remove_history_entry(item),
        ).pack(side="left")
        self._mac_bind_scroll_recursive(row, self.history_list_frame)

    def open_history_item(self, entry):
        path = entry.get("path")
        if path and os.path.exists(path):
            open_path(path)
        elif path and os.path.exists(os.path.dirname(path)):
            open_path(os.path.dirname(path))
        else:
            messagebox.showinfo("Not found", "This file could no longer be found on disk.")

    def redownload_from_history(self, entry):
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, entry["url"])
        self.show_page("Download")

    def remove_history_entry(self, entry):
        self.history = [h for h in self.history if h["id"] != entry["id"]]
        self.save_history()
        self._rebuild_history_list(self.history_search_entry.get(), force=True)
        self._refresh_stats()

    def clear_all_history(self):
        if messagebox.askyesno("Clear History", "Remove all download history? This won't delete the actual files."):
            self.history = []
            self.save_history()
            self._rebuild_history_list(force=True)
            self._refresh_stats()

    # ------------------------------------------------------------------
    # Settings persistence (saved to ~/.vrka/settings.json)
    # ------------------------------------------------------------------

    def collect_settings(self):
        return {
            "appearance_mode": "Light" if self.theme_var.get() else "Dark",
            "output_folder": self.output_folder,
            "mode": self.mode_var.get(),
            "quality": self.quality_menu.get(),
            "fps60": self.fps60_var.get(),
            "audio_format": self.audio_format_menu.get(),
            "mp3_bitrate": control_value(self, "mp3_bitrate_menu", "320 kbps"),
            "download_subs": self.subs_var.get(),
            "sub_langs": self.sub_langs_entry.get(),
            "embed_subs": self.embed_subs_var.get(),
            "auto_captions": self.auto_captions_var.get(),
            "embed_thumbnail": self.embed_thumbnail_var.get(),
            "embed_metadata": self.embed_metadata_var.get(),
            "sponsorblock": self.sponsorblock_var.get(),
            "sponsorblock_categories": self.sponsorblock_entry.get(),
            "proxy": self.proxy_entry.get(),
            "rate_limit": self.rate_limit_entry.get(),
            "force_ipv4": self.force_ipv4_var.get(),
            "restrict_filenames": self.restrict_filenames_var.get(),
            "output_template": self.output_template_entry.get(),
            "use_archive": self.use_archive_var.get(),
            "format_sort": self.format_sort_entry.get(),
            "allow_remote_components": self.remote_components_var.get(),
            "impersonation": control_value(self, "impersonation_menu", "Automatic"),
            "ytdlp_channel": control_value(self, "ytdlp_channel_menu", DEFAULT_YTDLP_CHANNEL),
            "ytdlp_check_on_startup": bool(control_value(self, "ytdlp_startup_check_var", False)),
            "cookie_mode": self.cookie_mode_menu.get(),
            "cookie_browser": self.cookie_browser_menu.get(),
            "cookie_profile": control_value(self, "cookie_profile_entry", "").strip(),
            "cookie_file": self.cookie_file_entry.get(),
        }

    def save_settings(self):
        try:
            _atomic_write_json(SETTINGS_FILE, self.collect_settings())
        except Exception:
            pass  # never block the app over a settings-save failure

    def load_settings(self):
        try:
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                migrated, subtitle_changed = migrate_subtitle_language_setting(loaded)
                migrated, audio_changed = migrate_audio_settings(migrated)
                migrated, cookie_changed = migrate_cookie_settings(migrated)
                if subtitle_changed or audio_changed or cookie_changed:
                    _atomic_write_json(SETTINGS_FILE, migrated)
                return migrated
        except Exception:
            pass
        return {}

    def _set_entry(self, entry, value):
        prev_state = entry.cget("state")
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, value)
        entry.configure(state=prev_state)

    def apply_settings(self, s):
        if not s:
            return
        try:
            self._apply_theme(s.get("appearance_mode", "Dark"))
            if s.get("output_folder"):
                self.output_folder = s["output_folder"]
                self._set_entry(self.output_folder_entry, self.output_folder)
            if "mode" in s:
                self.mode_var.set(s["mode"])
                self.on_mode_change()
            if "quality" in s:
                self.quality_menu.set(s["quality"])
            if "fps60" in s:
                self.fps60_var.set(s["fps60"])
            if "audio_format" in s:
                self.audio_format_menu.set(s["audio_format"])
            if "mp3_bitrate" in s:
                self.mp3_bitrate_menu.set(s["mp3_bitrate"])
            self._on_audio_format_change(self.audio_format_menu.get())
            if "download_subs" in s:
                self.subs_var.set(s["download_subs"])
            if "sub_langs" in s:
                self._set_entry(self.sub_langs_entry, s["sub_langs"])
            if "embed_subs" in s:
                self.embed_subs_var.set(s["embed_subs"])
            if "auto_captions" in s:
                self.auto_captions_var.set(s["auto_captions"])
            if "embed_thumbnail" in s:
                self.embed_thumbnail_var.set(s["embed_thumbnail"])
            if "embed_metadata" in s:
                self.embed_metadata_var.set(s["embed_metadata"])
            if "sponsorblock" in s:
                self.sponsorblock_var.set(s["sponsorblock"])
            if "sponsorblock_categories" in s:
                self._set_entry(self.sponsorblock_entry, s["sponsorblock_categories"])
            if "proxy" in s:
                self._set_entry(self.proxy_entry, s["proxy"])
            if "rate_limit" in s:
                self._set_entry(self.rate_limit_entry, s["rate_limit"])
            if "force_ipv4" in s:
                self.force_ipv4_var.set(s["force_ipv4"])
            if "restrict_filenames" in s:
                self.restrict_filenames_var.set(s["restrict_filenames"])
            if "output_template" in s:
                self._set_entry(self.output_template_entry, s["output_template"])
            if "use_archive" in s:
                self.use_archive_var.set(s["use_archive"])
            if "format_sort" in s:
                self._set_entry(self.format_sort_entry, s["format_sort"])
            if "allow_remote_components" in s:
                self.remote_components_var.set(s["allow_remote_components"])
            if "impersonation" in s:
                self.impersonation_menu.set(s["impersonation"])
            if "ytdlp_channel" in s:
                self.ytdlp_channel_menu.set(s["ytdlp_channel"])
            if "ytdlp_check_on_startup" in s:
                self.ytdlp_startup_check_var.set(s["ytdlp_check_on_startup"])
            if "cookie_mode" in s:
                restored_cookie_mode = s["cookie_mode"]
                if restored_cookie_mode == "Verified Session":
                    restored_cookie_mode = "Disabled"
                self.cookie_mode_menu.set(restored_cookie_mode)
            if "cookie_browser" in s:
                self.cookie_browser_menu.set(s["cookie_browser"])
            if "cookie_profile" in s:
                self._set_entry(self.cookie_profile_entry, s["cookie_profile"])
            if "cookie_file" in s:
                self._set_entry(self.cookie_file_entry, s["cookie_file"])
            # Re-apply enabled/disabled state for cookie fields based on the
            # restored mode (must happen after cookie_mode is set above).
            self._on_cookie_mode_change(self.cookie_mode_menu.get())
        except Exception as e:
            self.ui_queue.put(("log", f"Could not fully restore saved settings: {e}"))

    # ------------------------------------------------------------------
    # Self-updater
    # ------------------------------------------------------------------

    def _downloads_are_busy(self):
        with self.tasks_lock:
            return any(task.status in ("queued", "downloading") for task in self.tasks)

    def _refresh_runtime_status(self):
        backend = active_ytdlp_summary()
        if hasattr(self, "runtime_status_var"):
            self.runtime_status_var.set(
                f"Active: {backend['version']} ({backend['source']})"
            )

    def _set_runtime_controls(self, enabled):
        state = "normal" if enabled else "disabled"
        for name in ("update_button", "rollback_button", "restore_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(state=state)

    def start_update(self):
        if self._downloads_are_busy():
            messagebox.showwarning(
                "Downloads Are Active",
                "Finish or cancel queued downloads before changing the yt-dlp runtime.",
            )
            return
        self._set_runtime_controls(False)
        self.update_button.configure(text="Checking...")
        channel = control_value(self, "ytdlp_channel_menu", DEFAULT_YTDLP_CHANNEL)
        threading.Thread(
            target=self.run_update, args=(channel,), daemon=True,
        ).start()

    def run_update(self, channel):
        self.ui_queue.put(("log", f"Checking official yt-dlp {channel} releases..."))
        try:
            result = check_ytdlp_update(channel)
            if result["available"]:
                installed = install_ytdlp_update(channel)
                self.ui_queue.put((
                    "log",
                    f"Activated yt-dlp {installed['version']} ({installed['channel']}); "
                    "the previous managed build remains available for rollback.",
                ))
            else:
                self.ui_queue.put((
                    "log",
                    f"yt-dlp {result['active']['version']} is already current on {channel}.",
                ))
        except Exception as exc:
            self.ui_queue.put(("log", f"yt-dlp update failed safely: {exc}"))
        finally:
            self.ui_queue.put(("runtime_done", None))

    def start_runtime_rollback(self):
        if self._downloads_are_busy():
            messagebox.showwarning(
                "Downloads Are Active",
                "Finish or cancel queued downloads before rolling back yt-dlp.",
            )
            return
        self._set_runtime_controls(False)
        threading.Thread(target=self._run_runtime_rollback, daemon=True).start()

    def _run_runtime_rollback(self):
        try:
            result = rollback_ytdlp_update()
            self.ui_queue.put(("log", f"Rolled back to yt-dlp {result['version']}."))
        except Exception as exc:
            self.ui_queue.put(("log", f"Rollback was not performed: {exc}"))
        finally:
            self.ui_queue.put(("runtime_done", None))

    # -- Media Observer settings actions -----------------------------------

    def _media_observer_adapter(self):
        from vrka_core.media_observer import MediaObserverAdapter
        return MediaObserverAdapter(
            artifacts_root=str(Path(_bundled_observer_zip()).parent)
            if _bundled_observer_zip() else None,
            runtime_dir=str(BROWSER_EXT_DIR))

    def _media_observer_status_text(self):
        try:
            adapter = self._media_observer_adapter()
            status = adapter.status()
            health = adapter.health()
            state = "healthy" if health.get("ok") else "check diagnostics"
            installed = "installed" if status.get("dir_present") else "not prepared"
            verified = "verified" if status.get("artifact_verified") else "UNVERIFIED"
            return ("Installed version: %s  /  %s, artifact %s, %s"
                    % (status.get("version", "?"), installed, verified, state))
        except Exception as exc:
            return "Media observer status unavailable: %s" % exc

    def _observer_refresh(self, extra=""):
        def apply():
            try:
                self.observer_status_label.configure(
                    text=self._media_observer_status_text()
                    + (("  |  " + extra) if extra else ""))
            except Exception:
                pass
        self.after(0, apply)

    def start_observer_check(self):
        self.observer_status_label.configure(text="Checking official upstream release...")
        def run():
            try:
                from vrka_core.media_observer import check_for_update
                info = check_for_update()
                if info.get("error"):
                    self._observer_refresh("update check failed")
                elif info.get("update_available"):
                    self._observer_refresh(
                        "latest %s available" % info.get("available_version"))
                else:
                    self._observer_refresh("up to date (latest %s)" % info.get("available_version"))
            except Exception as exc:
                self._observer_refresh("update check error: %s" % exc)
        threading.Thread(target=run, daemon=True).start()

    def start_observer_update(self):
        self.observer_status_label.configure(text="Updating media observer...")
        def run():
            try:
                from vrka_core.media_observer import apply_update
                result = apply_update(artifacts_root=str(
                    Path(_bundled_observer_zip()).parent)) \
                    if _bundled_observer_zip() else {"error": "artifact unavailable"}
                if result.get("updated"):
                    self._observer_refresh(
                        "updated to %s" % result.get("installed_version"))
                elif result.get("message"):
                    self._observer_refresh(result["message"])
                else:
                    self._observer_refresh(
                        "update failed safely: %s" % result.get("error"))
            except Exception as exc:
                self._observer_refresh("update error: %s" % exc)
        threading.Thread(target=run, daemon=True).start()

    def start_restore_bundled(self):
        if self._downloads_are_busy():
            messagebox.showwarning(
                "Downloads Are Active",
                "Finish or cancel queued downloads before restoring bundled yt-dlp.",
            )
            return
        self._set_runtime_controls(False)
        threading.Thread(target=self._run_restore_bundled, daemon=True).start()

    def _run_restore_bundled(self):
        try:
            result = restore_bundled_ytdlp()
            self.ui_queue.put(("log", f"Restored bundled yt-dlp {result['version']}."))
        except Exception as exc:
            self.ui_queue.put(("log", f"Bundled runtime restore failed safely: {exc}"))
        finally:
            self.ui_queue.put(("runtime_done", None))

    def _startup_runtime_update(self, channel):
        try:
            result = check_ytdlp_update(channel)
            if result["available"] and not self._downloads_are_busy():
                installed = install_ytdlp_update(channel)
                self.ui_queue.put((
                    "log",
                    f"Startup runtime check activated yt-dlp {installed['version']} "
                    f"from the {installed['channel']} channel.",
                ))
            else:
                self.ui_queue.put((
                    "log", f"Startup runtime check: yt-dlp {result['active']['version']} is current."
                ))
        except Exception as exc:
            self.ui_queue.put(("log", f"Startup runtime check was skipped safely: {exc}"))
        finally:
            self.ui_queue.put(("runtime_done", None))

_DISCARDED_STD_STREAMS = []


def _frozen_std_stream_broken(stream):
    """True when an existing std stream cannot accept writes right now.

    A GUI-subsystem EXE can be launched with std handles that reference
    pipes the parent has already closed.  The wrapper looks healthy, but
    the first real write fails with ``OSError: [Errno 22] Invalid argument``
    and the pending buffer then poisons interpreter shutdown (exit 120).
    A zero-byte WriteFile probe detects this without emitting anything.
    """
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    try:
        handle = msvcrt.get_osfhandle(stream.fileno())
        written = ctypes.c_ulong(0)
        ok = kernel32.WriteFile(
            ctypes.c_void_p(handle), None, 0,
            ctypes.byref(written), None)
        return not ok
    except Exception:
        return True


def restore_frozen_cli_streams():
    """Reconnect redirected pipes/console for internal CLI modes in a windowed EXE.

    A windowed PyInstaller process may be launched with std handles that are
    absent (NULL), detached from any console, or non-null but BROKEN (a
    closed parent pipe).  Wrapping such a handle with open_osfhandle
    succeeds and the FIRST write then fails with ``OSError: [Errno 22]
    Invalid argument`` deep inside yt-dlp's output helpers.  Therefore every
    handle is probed for actual usability (GetFileType), the parent console
    is attached when one exists, CONOUT$/CONIN$ are used as console fallback,
    and devnull is the final fallback so callers always get writable streams.
    """
    if not is_frozen() or os.name != "nt":
        return
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.GetFileType.restype = ctypes.c_ulong
    kernel32.CreateFileW.restype = ctypes.c_void_p
    invalid_handle = ctypes.c_void_p(-1).value
    generic_write = 0x40000000
    generic_read = 0x80000000
    file_share_read = 0x1
    file_share_write = 0x2
    open_existing = 3

    # A GUI-subsystem process never owns a console; attaching to the parent
    # process's console makes CLI output visible for bare-console launches.
    console_attached = False
    try:
        if not kernel32.GetConsoleWindow():
            console_attached = bool(
                kernel32.AttachConsole(ctypes.c_ulong(0xFFFFFFFF)))
    except Exception:
        pass

    def _usable(handle):
        if handle in (None, 0, invalid_handle):
            return False
        # FILE_TYPE_UNKNOWN (0) is returned for closed/garbage handles.
        return kernel32.GetFileType(ctypes.c_void_p(handle)) != 0

    def _console_handle(for_write):
        if not console_attached:
            return None
        try:
            if for_write:
                return kernel32.CreateFileW(
                    "CONOUT$", generic_write,
                    file_share_read | file_share_write, None,
                    open_existing, 0, None)
            return kernel32.CreateFileW(
                "CONIN$", generic_read, file_share_read, None,
                open_existing, 0, None)
        except Exception:
            return None

    text_flag = getattr(os, "O_TEXT", 0)
    stream_specs = (
        ("stdin", -10, "r", os.O_RDONLY | text_flag, False),
        ("stdout", -11, "w", os.O_WRONLY | text_flag, True),
        ("stderr", -12, "w", os.O_WRONLY | text_flag, True),
    )
    for attribute, std_id, mode, flags, for_write in stream_specs:
        existing = getattr(sys, attribute, None)
        if existing is not None and mode == "w" \
                and not _frozen_std_stream_broken(existing):
            continue
        if existing is not None:
            # A broken wrapper keeps unflushable data that would poison
            # interpreter shutdown (unraisable OSError at exit).  Keep a
            # module-level reference so it is never finalized, and replace
            # it with a guaranteed-writable stream below.
            try:
                _DISCARDED_STD_STREAMS.append(existing)
            except Exception:
                pass
        stream = None
        handle = kernel32.GetStdHandle(ctypes.c_ulong(std_id & 0xFFFFFFFF))
        if not _usable(handle):
            handle = _console_handle(for_write)
        if _usable(handle):
            try:
                descriptor = msvcrt.open_osfhandle(int(handle), flags)
                stream = os.fdopen(
                    descriptor,
                    mode,
                    buffering=1,
                    encoding="utf-8",
                    errors="replace",
                )
                os.fstat(stream.fileno())
            except Exception:
                stream = None
        if stream is None:
            try:
                stream = open(
                    os.devnull,
                    mode,
                    buffering=1,
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception:
                continue
        setattr(sys, attribute, stream)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    configure_bundled_runtime_path()
    configure_windows_app_identity()

    if len(sys.argv) > 1 and sys.argv[1] in (
        "__vrka_protected_browser__", "__vrka_browser__",
        "__vrka_diagnostics__", "__ytdlp_cli__",
    ):
        restore_frozen_cli_streams()

    if len(sys.argv) > 3 and sys.argv[1] == "__vrka_protected_browser__":
        sys.exit(run_protected_browser_helper(sys.argv[2], sys.argv[3]))

    if len(sys.argv) > 3 and sys.argv[1] == "__vrka_browser__":
        sys.exit(run_browser_verification_helper(sys.argv[2], sys.argv[3]))

    if len(sys.argv) > 1 and sys.argv[1] == "__vrka_diagnostics__":
        print(json.dumps({
            "name": APP_NAME,
            "version": APP_VERSION,
            "build": APP_BUILD,
            "display_version": APP_DISPLAY_VERSION,
            "author": APP_AUTHOR,
            "copyright": APP_COPYRIGHT,
            "ytdlp": active_ytdlp_summary(),
            "frozen": is_frozen(),
            "font": get_font_registration_report(),
        }))
        sys.exit(0)

    # Internal mode: when re-invoked with this sentinel as the first argument
    # (used by the custom-command feature), act as a plain yt-dlp CLI process
    # instead of opening the GUI. This lets the SAME executable serve as its
    # own "yt-dlp binary" whether running as a script or a frozen .exe, where
    # there is no separate python/yt-dlp executable to shell out to.
    if len(sys.argv) > 1 and sys.argv[1] == "__ytdlp_cli__":
        try:
            sys.exit(yt_dlp.main(sys.argv[2:]))
        except OSError:
            # A parent may hand this GUI-subsystem executable broken stdio
            # handles; yt-dlp's writers then raise OSError mid-output. Swap in
            # devnull-backed streams and retry once so the CLI result still
            # completes with a meaningful exit code instead of crashing.
            try:
                for name, mode in (("stdout", "w"), ("stderr", "w")):
                    broken = getattr(sys, name, None)
                    if broken is not None:
                        # Keep the broken wrapper alive: finalizing it would
                        # flush unflushable data and fail interpreter shutdown.
                        _DISCARDED_STD_STREAMS.append(broken)
                    setattr(sys, name, open(
                        os.devnull, mode, buffering=1,
                        encoding="utf-8", errors="replace"))
                sys.__stdout__ = sys.stdout
                sys.__stderr__ = sys.stderr
            except Exception:
                pass
            sys.exit(yt_dlp.main(sys.argv[2:]))

    try:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        app = VRKADownloader()
        app.mainloop()
    except Exception:
        # A --windowed build has no console, so an uncaught startup error
        # would otherwise just make the app silently vanish with zero
        # feedback. Log it, and try to show a real dialog too.
        crash_text = traceback.format_exc()
        _write_crash_log(crash_text)
        try:
            import tkinter.messagebox as _mb
            _mb.showerror(
                f"{APP_NAME} - Startup Error",
                f"{APP_NAME} hit an error on startup and couldn't open.\n\n"
                f"Details were saved to:\n{APP_DATA_DIR / 'crash_log.txt'}\n\n"
                "Please share that file's contents for help fixing this."
            )
        except Exception:
            pass
        raise
