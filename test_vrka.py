"""Headless regression checks for VRKA.

The production application remains one file. This test harness supplies small
in-memory stand-ins for Tkinter, CustomTkinter, and yt-dlp so important control
flow can be exercised without opening a GUI or making network requests.
"""

from __future__ import annotations

import importlib.util
import contextlib
import ast
import hashlib
import io
import json
import os
import queue
import re
import shutil
import sys
import threading
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

from PIL import Image, ImageChops


PROJECT_DIR = Path(__file__).resolve().parent
TEST_TMP_ROOT = PROJECT_DIR / ".test_tmp"
TEST_TMP_ROOT.mkdir(exist_ok=True)
APP_FILE = (
    PROJECT_DIR / "vrka_downloader.py"
    if (PROJECT_DIR / "vrka_downloader.py").exists()
    else PROJECT_DIR / "seal_downloader.py"
)
BRAND_DIR = PROJECT_DIR / "assets" / "branding"
REQUIRED_PNG_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256, 512, 1024)
REQUIRED_ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _icon_two_colour_counts(rgba):
    """Count fully-opaque black and purple pixels in an RGBA production icon.

    Internal black geometry must be real opaque black (not transparent holes);
    the purple body must be opaque; only the antialiased silhouette edge may
    carry partial alpha.
    """
    opaque = dark = purple = 0
    for pixel in rgba.get_flattened_data():
        if not pixel[3]:
            continue
        opaque += 1
        if pixel[3] != 255:
            continue
        value = max(pixel[0], pixel[1], pixel[2])
        if value < 60:
            dark += 1
        elif pixel[2] > pixel[0] + 20 and pixel[2] >= 60:
            purple += 1
    return opaque, dark, purple


@contextlib.contextmanager
def workspace_temporary_directory():
    """Create inheritable test scratch space inside the project directory."""
    path = TEST_TMP_ROOT / f"case_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeVariable:
    def __init__(self, value=None, **_kwargs):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeWidget:
    def __init__(self, master=None, **kwargs):
        self.master = master
        self._children = []
        if master is not None and hasattr(master, "_children"):
            master._children.append(self)
        self._config = dict(kwargs)
        self._value = kwargs.get("text", "")
        self._bindings = {}
        self._destroyed = False
        self._manager = ""
        self._after_calls = []
        self._after_canceled = []
        self._yview = (0.0, 1.0)
        self.see_calls = 0

    def pack(self, **_kwargs):
        self._manager = "pack"
        return self

    def grid(self, **_kwargs):
        self._manager = "grid"
        return self

    def place(self, **_kwargs):
        self._manager = "place"
        return self

    def pack_forget(self):
        self._manager = ""

    def place_forget(self):
        self._manager = ""

    def winfo_manager(self):
        return self._manager

    def winfo_ismapped(self):
        return bool(self._manager)

    def pack_propagate(self, *_args):
        return None

    def grid_propagate(self, *_args):
        return None

    def rowconfigure(self, *_args, **_kwargs):
        return None

    def columnconfigure(self, *_args, **_kwargs):
        return None

    def configure(self, **kwargs):
        self._config.update(kwargs)
        if "text" in kwargs:
            self._value = kwargs["text"]

    config = configure

    def cget(self, name):
        return self._config.get(name, "normal" if name == "state" else None)

    def insert(self, _index, value):
        self._value = str(value)

    def delete(self, *_args):
        self._value = ""

    def get(self, *_args):
        return self._value

    def set(self, value):
        self._value = value

    def bind(self, sequence, callback, add=None):
        if add in ("+", True):
            self._bindings.setdefault(sequence, []).append(callback)
        else:
            self._bindings[sequence] = [callback]
        return f"bind-{sequence}"

    def winfo_children(self):
        return list(self._children)

    def winfo_exists(self):
        return not self._destroyed

    def destroy(self):
        self._destroyed = True
        self._manager = ""

    def tkraise(self):
        return None

    def after(self, *args, **kwargs):
        after_id = f"after-{len(self._after_calls) + 1}"
        self._after_calls.append((after_id, args, kwargs))
        return after_id

    def after_cancel(self, after_id):
        self._after_canceled.append(after_id)

    def protocol(self, *_args):
        return None

    def title(self, value):
        self._title = value

    def geometry(self, value):
        self._geometry = value

    def minsize(self, *_args):
        return None

    def iconphoto(self, *_args):
        return None

    def iconbitmap(self, *_args, **_kwargs):
        return None

    def clipboard_get(self):
        return ""

    def see(self, *_args):
        self.see_calls += 1
        return None

    def yview(self):
        return self._yview

    def update_idletasks(self):
        """Real tkinter processes pending idle work; the fake needs none."""
        return None


class FakeTextbox(FakeWidget):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.content = ""
        self._textbox = self
        self.tags = {}

    def insert(self, _index, value, *_tags):
        self.content += str(value)

    def tag_configure(self, name, **kwargs):
        self.tags[name] = kwargs

    def delete(self, start, end=None):
        if start in ("1.0", 0) and end == "end":
            self.content = ""
            return
        try:
            first_line = int(str(start).split(".", 1)[0])
            last_line = int(str(end).split(".", 1)[0]) if end else first_line
        except (TypeError, ValueError):
            self.content = ""
            return
        lines = self.content.splitlines(keepends=True)
        del lines[max(first_line - 1, 0):max(last_line - 1, 0)]
        self.content = "".join(lines)

    def get(self, *_args):
        return self.content


class FakeSlider(FakeWidget):
    pass


class FakeScrollbar(FakeWidget):
    pass


class FakeCanvas(FakeWidget):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.scroll_calls = []
        self._yview = (0.0, 0.5)

    def yview(self, *args):
        if args:
            self.scroll_calls.append(args)
        return self._yview

    def yview_scroll(self, amount, units):
        self.scroll_calls.append((amount, units))


class FakeScrollableFrame(FakeWidget):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self._parent_canvas = FakeCanvas()


class FakeRoot(FakeWidget):
    pass


class FakePhotoImage:
    def __init__(self, **_kwargs):
        self.tk = self

    def call(self, *_args):
        return None


class NoStartThread:
    def __init__(self, target=None, daemon=None, **_kwargs):
        self.target = target
        self.daemon = daemon

    def start(self):
        return None

    def join(self, *_args, **_kwargs):
        return None

    def is_alive(self):
        return False


def install_fake_gui_modules():
    tkinter = types.ModuleType("tkinter")
    tkinter.StringVar = FakeVariable
    tkinter.BooleanVar = FakeVariable
    tkinter.Variable = FakeVariable
    tkinter.PhotoImage = FakePhotoImage
    tkinter.Frame = FakeWidget
    tkinter.TkVersion = 8.6

    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.askdirectory = lambda **_kwargs: ""
    filedialog.askopenfilename = lambda **_kwargs: ""

    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.errors = []
    messagebox.infos = []
    messagebox.showerror = lambda *args, **kwargs: messagebox.errors.append((args, kwargs))
    messagebox.showinfo = lambda *args, **kwargs: messagebox.infos.append((args, kwargs))
    messagebox.askyesno = lambda *_args, **_kwargs: True

    tkinter.filedialog = filedialog
    tkinter.messagebox = messagebox

    ctk = types.ModuleType("customtkinter")
    ctk.CTk = FakeRoot
    ctk.CTkFrame = FakeWidget
    ctk.CTkLabel = FakeWidget
    ctk.CTkButton = FakeWidget
    ctk.CTkEntry = FakeWidget
    ctk.CTkOptionMenu = FakeWidget
    ctk.CTkRadioButton = FakeWidget
    ctk.CTkCheckBox = FakeWidget
    ctk.CTkProgressBar = FakeWidget
    ctk.CTkScrollableFrame = FakeScrollableFrame
    ctk.CTkTextbox = FakeTextbox
    ctk.CTkSlider = FakeSlider
    ctk.CTkScrollbar = FakeScrollbar
    ctk.CTkSwitch = FakeWidget
    ctk.CTkImage = lambda **kwargs: kwargs
    ctk.CTkFont = lambda **kwargs: kwargs
    ctk.set_appearance_mode = lambda *_args: None
    ctk.get_appearance_mode = lambda: "Dark"
    ctk.set_default_color_theme = lambda *_args: None

    sys.modules["tkinter"] = tkinter
    sys.modules["tkinter.filedialog"] = filedialog
    sys.modules["tkinter.messagebox"] = messagebox
    sys.modules["customtkinter"] = ctk
    return ctk, messagebox


YTDLP_BEHAVIOR = {
    "calls": [],
    "outcomes": [],
    "output_path": None,
}


def install_fake_ytdlp_module():
    yt_dlp = types.ModuleType("yt_dlp")

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            YTDLP_BEHAVIOR["calls"].append((dict(self.options), list(urls)))
            if YTDLP_BEHAVIOR["outcomes"]:
                outcome = YTDLP_BEHAVIOR["outcomes"].pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome

            output_path = YTDLP_BEHAVIOR["output_path"]
            info = {"title": "Test title", "filepath": output_path}
            for hook in self.options.get("progress_hooks", []):
                hook({
                    "status": "downloading",
                    "total_bytes": 100,
                    "downloaded_bytes": 100,
                    "speed": 1024 * 1024,
                    "info_dict": info,
                })
                hook({"status": "finished", "filename": output_path, "info_dict": info})
            for hook in self.options.get("postprocessor_hooks", []):
                hook({"status": "finished", "filename": output_path, "info_dict": info})
            return 0

    yt_dlp.YoutubeDL = FakeYoutubeDL
    yt_dlp.main_calls = []
    yt_dlp.main = lambda argv: yt_dlp.main_calls.append(list(argv)) or 0
    sys.modules["yt_dlp"] = yt_dlp
    return yt_dlp


CTK, MESSAGEBOX = install_fake_gui_modules()
FAKE_YTDLP = install_fake_ytdlp_module()

APP_SPEC = importlib.util.spec_from_file_location("vrka_app_under_test", APP_FILE)
APP = importlib.util.module_from_spec(APP_SPEC)
sys.modules[APP_SPEC.name] = APP
APP_SPEC.loader.exec_module(APP)

APP_CLASS = getattr(APP, "VRKADownloader", getattr(APP, "SealDownloader", None))


def fake_value(value=""):
    widget = FakeWidget()
    widget._value = value
    return widget


def standard_options(**overrides):
    options = {
        "output_folder": "",
        "quality": "1080p (Full HD)",
        "fps60": False,
        "audio_format": "FLAC (Lossless container)",
        "mp3_bitrate": "320 kbps",
        "impersonation": "Automatic",
        "download_subs": False,
        "sub_langs": "en.*",
        "embed_subs": False,
        "auto_captions": True,
        "is_playlist": False,
        "playlist_start": "",
        "playlist_end": "",
        "start_time": "",
        "end_time": "",
        "cookie_mode": "none",
        "cookie_browser": "chrome",
        "cookie_file": "",
        "embed_thumbnail": True,
        "embed_metadata": True,
        "sponsorblock": False,
        "sponsorblock_categories": "sponsor,selfpromo,interaction",
        "proxy": "",
        "rate_limit": "",
        "force_ipv4": False,
        "restrict_filenames": False,
        "output_template": "%(title)s.%(ext)s",
        "use_archive": False,
        "format_sort": "",
        "allow_remote_components": True,
        "custom_command": "",
    }
    options.update(overrides)
    return options


def make_form_app(tmpdir, custom_text, custom_opt_in):
    app = object.__new__(APP_CLASS)
    app.output_folder = tmpdir
    app.url_entry = fake_value("https://example.test/video")
    app.custom_command_box = FakeTextbox()
    app.custom_command_box.content = custom_text
    app.use_custom_command_var = FakeVariable(custom_opt_in)
    app.mode_var = FakeVariable("video")
    app.theme_var = FakeVariable(False)
    app.cookie_mode_menu = fake_value("None")
    app.quality_menu = fake_value("1080p (Full HD)")
    app.fps60_var = FakeVariable(False)
    app.audio_format_menu = fake_value("FLAC (Lossless container)")
    app.mp3_bitrate_menu = fake_value("320 kbps")
    app.impersonation_menu = fake_value("Automatic")
    app.ytdlp_channel_menu = fake_value("Stable")
    app.ytdlp_startup_check_var = FakeVariable(False)
    app.subs_var = FakeVariable(False)
    app.sub_langs_entry = fake_value("en.*")
    app.embed_subs_var = FakeVariable(False)
    app.auto_captions_var = FakeVariable(True)
    app.playlist_var = FakeVariable(False)
    app.playlist_start_entry = fake_value("")
    app.playlist_end_entry = fake_value("")
    app.start_time_entry = fake_value("")
    app.end_time_entry = fake_value("")
    app.cookie_browser_menu = fake_value("chrome")
    app.cookie_file_entry = fake_value("")
    app.embed_thumbnail_var = FakeVariable(True)
    app.embed_metadata_var = FakeVariable(True)
    app.sponsorblock_var = FakeVariable(False)
    app.sponsorblock_entry = fake_value("sponsor,selfpromo,interaction")
    app.proxy_entry = fake_value("")
    app.rate_limit_entry = fake_value("")
    app.force_ipv4_var = FakeVariable(False)
    app.restrict_filenames_var = FakeVariable(False)
    app.output_template_entry = fake_value("%(title)s.%(ext)s")
    app.use_archive_var = FakeVariable(False)
    app.format_sort_entry = fake_value("")
    app.remote_components_var = FakeVariable(True)
    app.tasks = []
    app.tasks_lock = threading.Lock()
    app.cancel_events = {}
    app.task_widgets = {}
    app._verified_session = {}
    app.ui_queue = queue.Queue()
    app.add_task_row = lambda _task: None
    app.show_page = lambda _page: None
    app.save_settings = lambda: None
    app._core_adapter = types.SimpleNamespace(
        submit=lambda task: (app.tasks.append(task), task)[1],
    )
    return app


class VRKARegressionTests(unittest.TestCase):
    def setUp(self):
        YTDLP_BEHAVIOR["calls"] = []
        YTDLP_BEHAVIOR["outcomes"] = []
        YTDLP_BEHAVIOR["output_path"] = None
        MESSAGEBOX.errors.clear()
        MESSAGEBOX.infos.clear()

    def test_production_icon_assets_are_two_colour_transparent_and_complete(self):
        for size in REQUIRED_PNG_SIZES:
            path = BRAND_DIR / f"vrka-wolf-{size}.png"
            self.assertTrue(path.is_file(), path)
            with Image.open(path) as image:
                rgba = image.convert("RGBA")
                self.assertEqual(rgba.size, (size, size))
                alpha = rgba.getchannel("A")
                self.assertEqual(alpha.getextrema(), (0, 255))
                self.assertTrue(all(rgba.getpixel(point)[3] == 0 for point in (
                    (0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1),
                )))
                opaque, dark, purple = _icon_two_colour_counts(rgba)
                self.assertGreater(dark, 0, f"{size}px: internal black geometry lost")
                self.assertGreater(purple, 0, f"{size}px: purple geometry lost")
                self.assertLessEqual(0.02, dark / opaque)
                self.assertLessEqual(dark / opaque, 0.60)
                self.assertGreaterEqual(purple / opaque, 0.20)
                difference = ImageChops.difference(
                    alpha, alpha.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                )
                differing = sum(1 for value in difference.get_flattened_data() if value > 4)
                self.assertLessEqual(differing / (size * size), 0.03)
                left, top, right, bottom = alpha.getbbox()
                self.assertGreaterEqual(min(left, top, size - right, size - bottom), 1)

        with Image.open(BRAND_DIR / "vrka.ico") as icon:
            self.assertTrue(set(REQUIRED_ICO_SIZES).issubset({size[0] for size in icon.ico.sizes()}))
            for size in REQUIRED_ICO_SIZES:
                icon.size = (size, size)
                opaque, dark, purple = _icon_two_colour_counts(icon.convert("RGBA"))
                self.assertGreater(dark, 0, f"ico {size}px: internal black geometry lost")
                self.assertGreater(purple, 0, f"ico {size}px: purple geometry lost")
        with Image.open(BRAND_DIR / "vrka.icns") as icon:
            pixel_sizes = {width * scale for width, _height, scale in icon.info.get("sizes", [])}
            self.assertTrue({32, 64, 128, 256, 512, 1024}.issubset(pixel_sizes))

        canonical = BRAND_DIR / "vrka-build010-canonical-source.png"
        self.assertTrue(canonical.is_file(), canonical)
        self.assertEqual(
            hashlib.sha256(canonical.read_bytes()).hexdigest().upper(),
            "862AD0437F2A080C23D3F91A29865E76A3D85A3FACF2C3324835FB7159553C20",
        )
        self.assertFalse((BRAND_DIR / "vrka-wolf-master.svg").exists())
        self.assertFalse((BRAND_DIR / "vrka-wolf-compact.svg").exists())
        manifest = json.loads((BRAND_DIR / "icon-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["canonical_master"], "vrka-build010-canonical-source.png")
        self.assertEqual(
            manifest["canonical_source_sha256"],
            "862AD0437F2A080C23D3F91A29865E76A3D85A3FACF2C3324835FB7159553C20",
        )
        self.assertFalse(manifest["flat_color"])
        self.assertTrue(manifest["two_colour"])
        self.assertIsNone(manifest["tiny_variant"])
        self.assertTrue((BRAND_DIR / manifest["contact_sheet"]).is_file())
        self.assertEqual(
            hashlib.sha256((BRAND_DIR / "vrka-wolf-1024.png").read_bytes()).hexdigest().upper(),
            "CC4CE7A87E5563CE830B9981C140F333C07B9E303D1DCC191178EA1DB85A3887",
        )
        generator = (PROJECT_DIR / "tools" / "generate_brand_assets.py").read_text(encoding="utf-8")
        self.assertNotIn("COMPACT_", generator)
        self.assertNotIn("CENTRAL =", generator)
        self.assertIn("floodfill", generator)

    def test_subtitle_default_exact_migration_and_message_wording(self):
        self.assertEqual(APP.DEFAULT_SUBTITLE_LANGUAGE_PATTERN, "en.*")
        migrated, changed = APP.migrate_subtitle_language_setting({"sub_langs": "en"})
        self.assertTrue(changed)
        self.assertEqual(migrated["sub_langs"], "en.*")
        for custom_value in ("en-US", "hi,en", "en.+", "fr", ""):
            preserved, changed = APP.migrate_subtitle_language_setting({"sub_langs": custom_value})
            self.assertFalse(changed)
            self.assertEqual(preserved["sub_langs"], custom_value)
        wording = APP.normalize_subtitle_message("video does not have subtitles")
        self.assertIn("No matching or downloadable subtitle track", wording)

    def test_legacy_subtitle_default_is_persisted_once(self):
        with workspace_temporary_directory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text('{"sub_langs": "en", "quality": "720p (HD)"}', encoding="utf-8")
            app = object.__new__(APP_CLASS)
            with mock.patch.object(APP, "SETTINGS_FILE", settings_path):
                loaded = APP_CLASS.load_settings(app)
            self.assertEqual(loaded["sub_langs"], "en.*")
            persisted = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["sub_langs"], "en.*")
            self.assertEqual(persisted["quality"], "720p (HD)")

    def test_subtitle_pattern_reaches_ytdlp_unchanged(self):
        with workspace_temporary_directory() as tmpdir:
            task = APP.DownloadTask(
                id="subtitle-pattern", url="https://example.test/video", mode="video",
                options=standard_options(output_folder=tmpdir, download_subs=True, sub_langs="en.*"),
            )
            args = APP._standard_ytdlp_arguments(task, tmpdir)
            self.assertEqual(args[args.index("--sub-langs") + 1], "en.*")
            self.assertIn("--write-auto-subs", args)

    def test_settings_round_trip_preserves_subtitle_and_safety_defaults(self):
        with workspace_temporary_directory() as tmpdir:
            app = make_form_app(tmpdir, "--embed-chapters", False)
            app.sub_langs_entry = fake_value("en.*")
            app.theme_var = FakeVariable(True)
            collected = APP_CLASS.collect_settings(app)
            destination = Path(tmpdir) / "settings.json"
            APP._atomic_write_json(destination, collected)
            restored = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(restored["sub_langs"], "en.*")
            self.assertEqual(restored["appearance_mode"], "Light")
            self.assertNotIn("custom_command", restored)
            self.assertNotIn("use_custom_command", restored)

    def test_theme_is_single_persistent_and_amoled(self):
        source = APP_FILE.read_text(encoding="utf-8")
        self.assertEqual(source.count("ctk.CTkSwitch("), 0)
        self.assertEqual(source.count("self.theme_button = ctk.CTkButton("), 1)
        self.assertEqual(APP.COLOR_BG, ("#FFFFFF", "#000000"))
        self.assertEqual(APP.COLOR_ACCENT, "#8140DC")
        self.assertIn('"appearance_mode": "Light" if self.theme_var.get() else "Dark"', source)
        self.assertIn('"sun" if mode == "Light" else "moon"', source)
        tree = ast.parse(source)
        alpha_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "attributes"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "-alpha"
        ]
        self.assertEqual(alpha_calls, [])

        app = make_form_app(str(TEST_TMP_ROOT), "", False)
        app._theme_animation_after_ids = []
        app.theme_button = FakeWidget()
        scheduled = []
        app.after = lambda delay, callback: scheduled.append((delay, callback)) or f"theme-{len(scheduled)}"
        app._apply_theme = lambda mode: app.theme_var.set(mode == "Light")
        app.save_settings = lambda: None
        APP_CLASS._toggle_theme(app)
        self.assertEqual([item[0] for item in scheduled], [45, 110])
        scheduled[0][1]()
        scheduled[1][1]()
        self.assertTrue(app.theme_var.get())
        self.assertEqual(app._theme_animation_after_ids, [])
        scheduled.clear()
        APP_CLASS._toggle_theme(app)
        scheduled[0][1]()
        scheduled[1][1]()
        self.assertFalse(app.theme_var.get())

    def test_download_header_has_no_fake_personalisation(self):
        source = APP_FILE.read_text(encoding="utf-8")
        self.assertIn('scroll, "Download media"', source)
        self.assertIn('"Paste a supported link and configure your download."', source)
        self.assertIsNone(re.search(r"Good (morning|afternoon|evening),?\s*VRKA", source, re.I))

    def test_readability_font_assets_contrast_and_brand_block(self):
        source = APP_FILE.read_text(encoding="utf-8")
        font_dir = PROJECT_DIR / "assets" / "fonts"
        production_fonts = {path.name for path in font_dir.glob("*.ttf")}

        self.assertNotIn("MEDIA ACQUISITION", source.upper())
        self.assertEqual(
            production_fonts,
            {"SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"},
        )
        self.assertTrue((font_dir / "OFL.txt").is_file())
        self.assertIn(
            "Permission is hereby granted, free of charge",
            (font_dir / "OFL.txt").read_text(encoding="utf-8"),
        )
        self.assertEqual(APP.UI_FONT_FAMILY, "Space Mono")
        self.assertEqual(set(APP.PRODUCTION_FONT_FILES), production_fonts)
        self.assertGreaterEqual(APP.FONT_PAGE_TITLE, 24)
        self.assertGreaterEqual(APP.FONT_SECTION_TITLE, 15)
        self.assertGreaterEqual(APP.FONT_BODY, 13)
        self.assertGreaterEqual(APP.FONT_SMALL, 11)
        self.assertGreaterEqual(APP.FONT_MICRO, 11)

        def luminance(hex_color):
            channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                for value in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def contrast(foreground, background):
            high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
            return (high + 0.05) / (low + 0.05)

        for text_tier in (APP.COLOR_TEXT, APP.COLOR_TEXT_MUTED, APP.COLOR_TEXT_DIM):
            self.assertGreaterEqual(contrast(text_tier[0], "#FFFFFF"), 4.5)
            self.assertGreaterEqual(contrast(text_tier[1], "#000000"), 4.5)
        self.assertGreaterEqual(contrast(APP.COLOR_TEXT_DISABLED[0], "#FFFFFF"), 3.0)
        self.assertGreaterEqual(contrast(APP.COLOR_TEXT_DISABLED[1], "#000000"), 3.0)

        for spec_name in ("VRKA-Windows.spec", "VRKA-macOS.spec"):
            spec = (PROJECT_DIR / spec_name).read_text(encoding="utf-8")
            self.assertIn('"assets" / "fonts"', spec)

    def test_runtime_brand_icon_has_nonfatal_fallback(self):
        APP._BRAND_IMAGE_CACHE.clear()
        missing = PROJECT_DIR / "missing-brand-file.png"
        with (
            mock.patch.object(APP, "resource_path", return_value=missing),
            mock.patch.object(APP, "_write_crash_log"),
        ):
            fallback = APP.load_brand_image(24)
        self.assertEqual(fallback.mode, "RGBA")
        self.assertEqual(fallback.size, (24, 24))
        self.assertEqual(fallback.getchannel("A").getextrema(), (0, 0))
        APP._BRAND_IMAGE_CACHE.clear()
        source = APP_FILE.read_text(encoding="utf-8")
        self.assertIn("def _windows_set_titlebar_icon(self):", source)
        self.assertIn("self._apply_window_icon()", source)
        self.assertIn('self.iconbitmap(default=str(ico_path))', source)
        self.assertIn('self.iconphoto(True, self._app_icon_photo)', source)
        self.assertIn('return load_brand_image(size)', source)
        self.assertIn('self.sidebar_brand_image = get_brand_ctk_image(48)', source)
        self.assertGreaterEqual(source.count('get_brand_ctk_image(32)'), 2)
        for stale_name in ("seal_icon.ico", "seal_icon.icns", "download_arrow.png"):
            self.assertNotIn(stale_name, source.lower())

    def test_build_metadata_uses_new_version_and_wolf_assets(self):
        source = APP_FILE.read_text(encoding="utf-8")
        windows_spec = (PROJECT_DIR / "VRKA-Windows.spec").read_text(encoding="utf-8")
        mac_spec = (PROJECT_DIR / "VRKA-macOS.spec").read_text(encoding="utf-8")
        installer = (PROJECT_DIR / "VRKA.iss").read_text(encoding="utf-8")
        version_info = (PROJECT_DIR / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION = "3.0.0"', source)
        self.assertIn('APP_BUILD = "011"', source)
        self.assertIn('assets" / "branding" / "vrka.ico', windows_spec)
        self.assertIn('assets" / "branding" / "vrka.icns', mac_spec)
        self.assertIn('CFBundleVersion": "011"', mac_spec)
        self.assertIn('#define MyAppVersion "3.5"', installer)
        self.assertIn('SetupIconFile=assets\\branding\\vrka.ico', installer)
        self.assertIn("StringStruct('ProductVersion', '3.5.0')", version_info)

    def test_timer_audit_contains_only_queue_poll_and_one_shot_ui_work(self):
        source = APP_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        after_calls = [
            ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "after"
        ]
        self.assertEqual(len(after_calls), 8, after_calls)
        joined = "\n".join(after_calls)
        self.assertIn("UI_QUEUE_INTERVAL_MS", joined)
        self.assertIn("self.process_ui_queue", joined)
        self.assertIn("self.check_ffmpeg", joined)
        self.assertIn("HISTORY_SEARCH_DEBOUNCE_MS", joined)
        self.assertIn("_scrollbar_update_time", joined)
        self.assertIn("THEME_TOGGLE_MIDPOINT_MS", joined)
        self.assertIn("THEME_TOGGLE_DURATION_MS", joined)
        # Count 8: the Media Observer card's one-shot marshaled label refresh.
        self.assertIn("self.after(0, apply)", joined)
        self.assertFalse(any(word in joined.lower() for word in ("pulse", "blink", "reveal")))

    def test_complete_headless_ui_constructs_and_closes(self):
        with workspace_temporary_directory() as tmpdir:
            app_data = Path(tmpdir) / ".vrka"
            history_file = app_data / "history.json"
            settings_file = app_data / "settings.json"
            app_data.mkdir(parents=True)
            settings_file.write_text('{"appearance_mode": "Light"}', encoding="utf-8")
            with (
                mock.patch.object(APP, "APP_DATA_DIR", app_data),
                mock.patch.object(APP, "HISTORY_FILE", history_file),
                mock.patch.object(APP, "SETTINGS_FILE", settings_file),
                mock.patch.object(APP, "migrate_legacy_app_data", return_value=[]),
                mock.patch.object(APP, "_HAS_IMAGETK", False),
                mock.patch.object(APP.threading, "Thread", NoStartThread),
            ):
                app = APP_CLASS()
                self.assertEqual(app._geometry, "1240x820")
                self.assertEqual(app.sub_langs_entry.get(), "en.*")
                self.assertTrue(app.theme_var.get())
                self.assertEqual(app.collect_settings()["appearance_mode"], "Light")
                self.assertEqual(app._current_page, "Download")
                self.assertEqual(
                    [name for name, page in app.pages.items() if page.winfo_manager()],
                    ["Download"],
                )
                self.assertTrue(hasattr(app, "queue_empty_state"))
                app._on_close()
                self.assertTrue(app._destroyed)
                self.assertTrue(settings_file.is_file())

    def test_helper_functions(self):
        self.assertEqual(APP.parse_time_to_seconds("01:02:03"), 3723)
        self.assertEqual(APP.parse_time_to_seconds("02:30"), 150)
        self.assertIsNone(APP.parse_time_to_seconds("bad"))
        self.assertEqual(APP.parse_rate_limit("2M"), 2 * 1024 * 1024)
        selector = APP.build_video_format(2160, True)
        self.assertIn("[fps>=60]", selector)
        self.assertIn("bestvideo[height<=2160]+bestaudio", selector)
        # Final unfiltered tier: streams whose formats carry no resolution
        # metadata (protected HLS lists resolution "unknown") match none of the
        # capped tiers and would otherwise be undownloadable.
        self.assertTrue(selector.endswith("/best"))
        self.assertEqual(APP.build_video_format(0, False), "bestvideo+bestaudio/best/best")

    def test_auto_captions_and_custom_opt_in_defaults(self):
        app = object.__new__(APP_CLASS)
        APP_CLASS._build_settings_tab(app, FakeWidget())
        self.assertTrue(app.auto_captions_var.get())
        self.assertFalse(app.use_custom_command_var.get())

    def test_leftover_custom_text_is_inert_without_opt_in(self):
        with workspace_temporary_directory() as tmpdir:
            app = make_form_app(tmpdir, "--embed-chapters", False)
            APP_CLASS.add_to_queue(app)
            self.assertEqual(len(app.tasks), 1)
            self.assertEqual(app.tasks[0].mode, "video")

    def test_custom_text_runs_when_explicitly_enabled(self):
        with workspace_temporary_directory() as tmpdir:
            app = make_form_app(tmpdir, "--embed-chapters", True)
            APP_CLASS.add_to_queue(app)
            self.assertEqual(len(app.tasks), 1)
            self.assertEqual(app.tasks[0].mode, "custom")

    def test_60fps_failure_retries_without_fps_preference(self):
        with workspace_temporary_directory() as tmpdir:
            task = APP.DownloadTask(
                id="fps-test", url="https://example.test/video", mode="video",
                options=standard_options(output_folder=tmpdir, fps60=True),
            )
            first = APP._standard_ytdlp_arguments(task, tmpdir)
            fallback = APP._standard_ytdlp_arguments(
                task, tmpdir, format_override=APP.build_video_format(1080, False)
            )
            self.assertIn("[fps>=60]", first[first.index("-f") + 1])
            self.assertNotIn("[fps>=60]", fallback[fallback.index("-f") + 1])

    def test_high_quality_trim_skips_efficient_path(self):
        with workspace_temporary_directory() as tmpdir:
            output_path = os.path.join(tmpdir, "video.mp4")
            Path(output_path).touch()
            task = APP.DownloadTask(
                id="trim-high", url="https://example.test/video", mode="video",
                options=standard_options(output_folder=tmpdir, quality="2160p (4K)", start_time="10", end_time="20"),
            )
            worker = object.__new__(APP_CLASS)
            worker.ui_queue = queue.Queue()
            commands = []
            def execute(current_task, command, _cancel, _backend):
                commands.append(command)
                current_task.output_path = output_path
            worker._execute_ytdlp_command = execute
            worker._probe_video_height = lambda *_args: 2160
            worker._trim_local_file = lambda current_task, *_args: current_task.output_path
            APP_CLASS._run_standard_task(worker, task, tmpdir, threading.Event())
            self.assertEqual(len(commands), 1)
            self.assertNotIn("--download-sections", commands[0])

    def test_1080p_trim_uses_efficient_path(self):
        with workspace_temporary_directory() as tmpdir:
            output_path = os.path.join(tmpdir, "video.mp4")
            Path(output_path).touch()
            task = APP.DownloadTask(
                id="trim-efficient", url="https://example.test/video", mode="video",
                options=standard_options(output_folder=tmpdir, quality="1080p (Full HD)", start_time="10", end_time="20"),
            )
            worker = object.__new__(APP_CLASS)
            worker.ui_queue = queue.Queue()
            commands = []
            def execute(current_task, command, _cancel, _backend):
                commands.append(command)
                current_task.output_path = output_path
            worker._execute_ytdlp_command = execute
            worker._probe_video_height = lambda *_args: 1080
            APP_CLASS._run_standard_task(worker, task, tmpdir, threading.Event())
            self.assertEqual(len(commands), 1)
            self.assertIn("--download-sections", commands[0])

    def test_mac_scroll_binding_is_local_and_stops_global_handler(self):
        app = object.__new__(APP_CLASS)
        scroll = FakeScrollableFrame()
        child = FakeWidget(scroll)
        grandchild = FakeWidget(child)
        with mock.patch.object(APP.platform, "system", return_value="Darwin"):
            app._mac_bind_scroll_recursive(child, scroll)
            callbacks = grandchild._bindings.get("<MouseWheel>", [])
            self.assertEqual(len(callbacks), 1)
            event = types.SimpleNamespace(delta=2, widget=grandchild)
            self.assertEqual(callbacks[0](event), "break")
        self.assertEqual(scroll._parent_canvas.scroll_calls[-1], (-2, "units"))

    def test_legacy_data_migration_is_non_destructive(self):
        with workspace_temporary_directory() as tmpdir:
            legacy = Path(tmpdir) / ".seal_desktop"
            current = Path(tmpdir) / ".vrka"
            legacy.mkdir()
            (legacy / "settings.json").write_text('{"quality": "720p (HD)"}', encoding="utf-8")
            (legacy / "history.json").write_text('[{"id": "old"}]', encoding="utf-8")
            with (
                mock.patch.object(APP, "LEGACY_APP_DATA_DIR", legacy),
                mock.patch.object(APP, "APP_DATA_DIR", current),
                mock.patch.object(APP, "HISTORY_FILE", current / "history.json"),
                mock.patch.object(APP, "SETTINGS_FILE", current / "settings.json"),
            ):
                migrated = APP.migrate_legacy_app_data()
            self.assertEqual(set(migrated), {"settings.json", "history.json"})
            self.assertTrue((legacy / "settings.json").exists())
            self.assertEqual(
                (current / "settings.json").read_text(encoding="utf-8"),
                '{"quality": "720p (HD)"}',
            )
            (legacy / "settings.json").write_text('{"quality": "360p"}', encoding="utf-8")
            with (
                mock.patch.object(APP, "LEGACY_APP_DATA_DIR", legacy),
                mock.patch.object(APP, "APP_DATA_DIR", current),
                mock.patch.object(APP, "HISTORY_FILE", current / "history.json"),
                mock.patch.object(APP, "SETTINGS_FILE", current / "settings.json"),
            ):
                migrated_again = APP.migrate_legacy_app_data()
            self.assertEqual(migrated_again, [])
            self.assertEqual(
                (current / "settings.json").read_text(encoding="utf-8"),
                '{"quality": "720p (HD)"}',
            )

    def test_activity_log_is_prefixed_and_bounded(self):
        app = object.__new__(APP_CLASS)
        app.log_textbox = FakeTextbox()
        app._log_line_count = 0
        original_limit = getattr(APP, "MAX_LOG_LINES", 1000)
        with mock.patch.object(APP, "MAX_LOG_LINES", 5, create=True):
            for index in range(8):
                APP_CLASS._append_log(app, f"line {index}")
        lines = app.log_textbox.content.splitlines()
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(line.startswith("> ") for line in lines))
        self.assertEqual(original_limit, getattr(APP, "MAX_LOG_LINES", original_limit))

    def test_atomic_json_save_leaves_no_partial_file(self):
        with workspace_temporary_directory() as tmpdir:
            destination = Path(tmpdir) / "settings.json"
            APP._atomic_write_json(destination, {"quality": "1080p (Full HD)"})
            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                '{\n  "quality": "1080p (Full HD)"\n}',
            )
            self.assertEqual(list(Path(tmpdir).glob("*.tmp")), [])

    def test_custom_command_tracking_and_secret_redaction(self):
        class FakeProcess:
            def __init__(self, command, **_kwargs):
                self.command = command
                self.stdout = io.StringIO(
                    "__VRKA_TITLE__Tracked title\n"
                    "[download] 50.0%\n"
                    "__VRKA_OUTPUT__C:/Downloads/tracked.mp4\n"
                )
                self.returncode = 0
                self.pid = 4242

            def poll(self):
                return self.returncode

            def wait(self):
                return self.returncode

            def terminate(self):
                self.returncode = 1

        with workspace_temporary_directory() as tmpdir:
            task = APP.DownloadTask(
                id="custom-track",
                url="https://example.test/video",
                mode="custom",
                options=standard_options(
                    output_folder=tmpdir,
                    custom_command="--password supersecret --embed-chapters",
                ),
            )
            worker = object.__new__(APP_CLASS)
            worker.ui_queue = queue.Queue()
            with mock.patch.object(APP.subprocess, "Popen", FakeProcess):
                APP_CLASS._run_custom_command_task(worker, task, tmpdir, threading.Event())
            self.assertEqual(task.title, "Tracked title")
            self.assertEqual(task.output_path, "C:/Downloads/tracked.mp4")
            queued_messages = list(worker.ui_queue.queue)
            log_text = "\n".join(message[1] for message in queued_messages if message[0] == "log")
            self.assertNotIn("supersecret", log_text)
            self.assertIn("<redacted>", log_text)

    def test_custom_mode_is_never_persisted_implicitly(self):
        with workspace_temporary_directory() as tmpdir:
            app = make_form_app(tmpdir, "--password secret", True)
            settings = APP_CLASS.collect_settings(app)
            self.assertNotIn("custom_command", settings)
            self.assertNotIn("use_custom_command", settings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
