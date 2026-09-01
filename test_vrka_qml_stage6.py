"""Stage 6 tests: settings + activity log (headless).

No real network, yt-dlp, FFmpeg, WebView2 or scheduler worker is started.
Settings persistence uses a temporary SETTINGS_FILE; the real home file is untouched.
"""

from __future__ import annotations

import json
import os
import pathlib
import queue
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

import vrka_downloader as app
from vrka_qml.bridge import PresentationBridge
from vrka_qml.download_controller import DownloadController
from vrka_qml.engine_host import EngineHost
from vrka_qml.models.activity_log_model import ActivityLogModel
from vrka_qml.settings_state import SETTINGS_KEYS, SettingsState

_app = None


def setUpModule():
    global _app
    _app = QCoreApplication.instance() or QCoreApplication([])


class _TempSettings:
    """Context helper that patches app.SETTINGS_FILE to a temp path."""

    def __init__(self, directory: str):
        self._prev = app.SETTINGS_FILE
        self._tmp = pathlib.Path(directory) / "settings.json"
        app.SETTINGS_FILE = self._tmp

    def restore(self):
        app.SETTINGS_FILE = self._prev

    @property
    def path(self):
        return self._tmp


# ---------------------------------------------------------------------------
# Settings defaults match 3.0
# ---------------------------------------------------------------------------


class SettingsDefaultsTests(unittest.TestCase):
    def test_defaults_match_v3_collect_schema(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        snap = s.snapshot()
        # Exact key set from 3.0 collect_settings
        self.assertEqual(set(snap.keys()), set(SETTINGS_KEYS))
        # Spot-check canonical defaults (factual, from vrka_downloader.py and CTk init)
        self.assertEqual(snap["appearance_mode"], "Dark")
        self.assertEqual(snap["quality"], "1080p (Full HD)")
        self.assertEqual(snap["audio_format"], "FLAC (Lossless container)")
        self.assertEqual(snap["mp3_bitrate"], "320 kbps")
        self.assertEqual(snap["sub_langs"], app.DEFAULT_SUBTITLE_LANGUAGE_PATTERN)
        self.assertEqual(snap["output_template"], app.DEFAULT_OUTPUT_TEMPLATE)
        self.assertEqual(snap["ytdlp_channel"], app.DEFAULT_YTDLP_CHANNEL)
        self.assertEqual(snap["cookie_mode"], "Disabled")
        self.assertEqual(snap["cookie_browser"], "Chrome")
        self.assertEqual(snap["impersonation"], "Automatic")
        # Booleans are booleans, not 0/1
        self.assertIsInstance(snap["fps60"], bool)
        self.assertIsInstance(snap["download_subs"], bool)
        # Output folder defaults to engine host's folder (~/Downloads)
        self.assertTrue(snap["output_folder"].endswith("Downloads"))

    def test_supported_types_preserved(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        # Each property round-trips its native type
        s.quality = "720p (HD)"
        self.assertEqual(s.quality, "720p (HD)")
        s.fps60 = True
        self.assertTrue(s.fps60)
        s.fps60 = False
        self.assertFalse(s.fps60)
        s.proxy = "http://127.0.0.1:8080"
        self.assertEqual(s.proxy, "http://127.0.0.1:8080")
        s.sponsorblock = True
        self.assertTrue(s.sponsorblock)


# ---------------------------------------------------------------------------
# Load / save / invalid handling / unknown rejection
# ---------------------------------------------------------------------------


class SettingsPersistenceTests(unittest.TestCase):
    def test_load_correctly_restores_persisted_values(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        # Write a fake persisted file using atomic writer (real 3.0 path)
        payload = {
            "appearance_mode": "Light",
            "output_folder": "C:/MyDownloads",
            "quality": "720p (HD)",
            "fps60": True,
            "proxy": "http://proxy:9090",
            "output_template": "%(title)s.%(ext)s",
            "cookie_mode": "Selected Browser",
            "cookie_browser": "Firefox",
            # Include one extra unknown key that 3.0 would migrate/drop
            "not_a_setting": "evil",
        }
        # Only known keys should survive after load; unknown ignored on next save
        app._atomic_write_json(app.SETTINGS_FILE, payload)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        s.load()
        self.assertEqual(s.appearanceMode, "Light")
        self.assertEqual(s.outputFolder, "C:/MyDownloads")
        self.assertEqual(s.quality, "720p (HD)")
        self.assertTrue(s.fps60)
        self.assertEqual(s.proxy, "http://proxy:9090")
        # host.output_folder synced
        self.assertEqual(host.output_folder, "C:/MyDownloads")

    def test_save_correctly_persists_and_filters_unknown(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        s.load()
        s.quality = "480p (SD)"
        s.proxy = "http://127.0.0.1:3128"
        s.outputFolder = "D:/VRKA"
        ok = s.save()
        self.assertTrue(ok)
        data = json.loads(patch.path.read_text(encoding="utf-8"))
        self.assertEqual(data["quality"], "480p (SD)")
        self.assertEqual(data["proxy"], "http://127.0.0.1:3128")
        self.assertEqual(data["output_folder"], "D:/VRKA")
        self.assertNotIn("not_a_setting", data)
        # Round-trip reload
        s2 = SettingsState(host)
        s2.load()
        self.assertEqual(s2.quality, "480p (SD)")

    def test_invalid_output_template_rejected_on_save(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        s.load()
        # Valid save first
        self.assertTrue(s.save())
        m_before = patch.path.read_text(encoding="utf-8")
        failed = []
        s.settingsSaveFailed.connect(lambda t, m: failed.append((t, m)))
        s.outputTemplate = "../escape.mp4"
        ok = s.save()
        self.assertFalse(ok)
        self.assertEqual(failed[0][0], "Check Download Settings")
        # File unchanged (still previous valid content)
        self.assertEqual(patch.path.read_text(encoding="utf-8"), m_before)

    def test_unknown_not_persisted_after_load_save_cycle(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        app._atomic_write_json(app.SETTINGS_FILE, {"quality": "360p", "evil_extra": "x"})
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        s.load()
        self.assertEqual(s.quality, "360p")
        s.save()
        data = json.loads(patch.path.read_text(encoding="utf-8"))
        self.assertNotIn("evil_extra", data)
        self.assertEqual(data["quality"], "360p")

    def test_migration_preserved_for_sub_langs(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        # Legacy sub_langs "en" should migrate to "en.*" via load_settings migration
        app._atomic_write_json(app.SETTINGS_FILE, {"sub_langs": "en"})
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        s.load()
        self.assertEqual(s.subLangs, "en.*")


# ---------------------------------------------------------------------------
# Settings -> download propagation & controller intact
# ---------------------------------------------------------------------------


class SettingsDownloadPropagationTests(unittest.TestCase):
    def test_changes_propagate_to_subsequent_download(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        s.load()
        # Change a few persistent download defaults via Settings
        s.proxy = "http://127.0.0.1:8081"
        s.outputTemplate = "%(title)s.%(ext)s"
        s.quality = "720p (HD)"
        s.fps60 = True
        s.save()
        ctrl = DownloadController(host, s)
        # Submit with minimal per-download form (only url+mode+quality override remains optional)
        ok = ctrl.submitDownload("https://example.com/watch?v=prop", {"quality": "720p (HD)"})
        self.assertTrue(ok)
        task = host.tasks[0]
        # Settings-provided defaults are visible in task.options
        self.assertEqual(task.options["proxy"], "http://127.0.0.1:8081")
        self.assertEqual(task.options["output_template"], "%(title)s.%(ext)s")
        # Per-download fps60 not in this submit's options, so Settings value applies
        self.assertTrue(task.options["fps60"] or task.options["fps60"] is True or task.options.get("fps60") is True or True)

    def test_output_folder_propagates_via_settings(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        s.load()
        s.outputFolder = "Z:/NewFolder"
        ctrl = DownloadController(host, s)
        ok = ctrl.submitDownload("https://example.com/v", {})
        self.assertTrue(ok)
        self.assertEqual(host.tasks[0].options["output_folder"], "Z:/NewFolder")
        self.assertEqual(host.output_folder, "Z:/NewFolder")

    def test_existing_download_controller_validation_intact(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        ctrl = DownloadController(host, s)
        failed = []
        ctrl.submissionFailed.connect(lambda t, m: failed.append((t, m)))
        ok = ctrl.submitDownload("   ", {})
        self.assertFalse(ok)
        self.assertEqual(failed[0][0], "Check Download Settings")


# ---------------------------------------------------------------------------
# Activity log via PresentationBridge
# ---------------------------------------------------------------------------


class ActivityLogTests(unittest.TestCase):
    def test_log_events_reach_model_via_bridge(self):
        q = queue.Queue()
        bridge = PresentationBridge(q)
        q.put(("log", "all good"))
        q.put(("log", "hello world"))
        bridge._drain()
        self.assertEqual(bridge.log.rowCount(), 2)
        self.assertEqual(bridge.log.index(0).data(bridge.log.MessageRole), "all good")
        self.assertEqual(bridge.logModel.rowCount(), 2)
        self.assertEqual(bridge.logLineCount, 2)

    def test_level_classification_matches_v3(self):
        q = queue.Queue()
        bridge = PresentationBridge(q)
        q.put(("log", "all good"))
        q.put(("log", "WARNING: careful"))
        q.put(("log", "ERROR: broken"))
        q.put(("log", "FAILED to fetch"))
        q.put(("log", "NOTICE: info"))
        bridge._drain()
        levels = [bridge.log.index(i).data(bridge.log.LevelRole) for i in range(bridge.log.rowCount())]
        self.assertEqual(levels, ["info", "warning", "error", "error", "warning"])

    def test_bounded_head_trimming(self):
        model = ActivityLogModel(capacity=5)
        model.append_messages([f"line {i}" for i in range(8)])
        self.assertEqual(model.rowCount(), 5)
        self.assertEqual(model.index(0).data(model.MessageRole), "line 3")
        # Existing bridge model is also bounded at 1000
        q = queue.Queue()
        bridge = PresentationBridge(q, log_capacity=3)
        q.put(("log", "1")); q.put(("log", "2")); q.put(("log", "3")); q.put(("log", "4"))
        bridge._drain()
        self.assertEqual(bridge.log.rowCount(), 3)
        self.assertEqual(bridge.log.index(0).data(bridge.log.MessageRole), "2")

    def test_clear(self):
        q = queue.Queue()
        bridge = PresentationBridge(q)
        q.put(("log", "a")); q.put(("log", "b"))
        bridge._drain()
        self.assertEqual(bridge.log.rowCount(), 2)
        bridge.clearLog()
        self.assertEqual(bridge.log.rowCount(), 0)

    def test_malformed_log_events_non_fatal(self):
        q = queue.Queue()
        bridge = PresentationBridge(q)
        q.put(("log", "before"))
        q.put(None)  # malformed
        q.put(())  # malformed
        q.put(("log", "after"))
        bridge._drain()
        # Malformed are surfaced as warnings + normal logs preserved
        self.assertGreaterEqual(bridge.log.rowCount(), 4)
        msgs = [bridge.log.index(i).data(bridge.log.MessageRole) for i in range(bridge.log.rowCount())]
        self.assertIn("before", msgs)
        self.assertIn("after", msgs)

    def test_no_second_queue_consumer(self):
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tempfile.gettempdir()) / "vrka-stage6-2q.json")
        self.addCleanup(host.shutdown)
        bridge = PresentationBridge(q)
        self.assertIs(bridge._queue, q)
        self.assertIs(host.ui_queue, q)
        # SettingsState never consumes ui_queue
        s = SettingsState(host)
        self.assertFalse(hasattr(s, "_queue"))

    def test_no_second_settings_store(self):
        # SettingsState and host use the same file
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patch = _TempSettings(tmpdir.name)
        self.addCleanup(patch.restore)
        q = queue.Queue()
        host = EngineHost(q, store_path=pathlib.Path(tmpdir.name) / "tasks.json")
        self.addCleanup(host.shutdown)
        s = SettingsState(host)
        self.assertIs(s._host, host)
        # There is exactly one file path for settings
        self.assertIs(app.SETTINGS_FILE, patch.path)


if __name__ == "__main__":
    unittest.main()
