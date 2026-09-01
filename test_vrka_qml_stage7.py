"""Stage 7 tests: browser fallback, MediaObserver, updater operational integration.

All headless, no real browser, no WebView2, no network. Updater/observer
network calls are stubbed via monkey-patched callables.
"""

from __future__ import annotations

import os
import pathlib
import queue
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

import vrka_downloader as app
from vrka_qml.bridge import PresentationBridge
from vrka_qml.engine_host import EngineHost
from vrka_qml.operational_controller import OperationalController
from vrka_qml.settings_state import SettingsState

_app = None


def setUpModule():
    global _app
    _app = QCoreApplication.instance() or QCoreApplication([])


def _make_stack(tmpdir: str):
    q = queue.Queue()
    host = EngineHost(q, store_path=pathlib.Path(tmpdir) / "tasks.json")
    bridge = PresentationBridge(q)
    settings = SettingsState(host)
    op = OperationalController(host, bridge, settings)
    bridge.start()
    return q, host, bridge, settings, op


# ---------------------------------------------------------------------------
# Settings (Stage 7 extended)
# ---------------------------------------------------------------------------


class SettingsOperationalTests(unittest.TestCase):
    def test_stage7_settings_keys_still_30(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, _, settings, _ = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.assertEqual(len(settings.snapshot()), 30)
        self.assertIn("ytdlp_channel", settings.snapshot())
        self.assertIn("ytdlp_check_on_startup", settings.snapshot())
        self.assertNotIn("browser_fallback", settings.snapshot())

    def test_ytdlp_channel_defaults_and_persists(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        prev = app.SETTINGS_FILE
        app.SETTINGS_FILE = pathlib.Path(tmpdir.name) / "settings.json"
        self.addCleanup(lambda: setattr(app, "SETTINGS_FILE", prev))
        q, host, _, settings, _ = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.assertEqual(settings.ytdlpChannel, "Stable")
        settings.ytdlpChannel = "Nightly"
        self.assertTrue(settings.save())
        s2 = SettingsState(host)
        s2.load()
        self.assertEqual(s2.ytdlpChannel, "Nightly")
        # invalid channel ignored
        settings.ytdlpChannel = "Nightly"
        settings.ytdlpChannel = "Beta"
        self.assertEqual(settings.ytdlpChannel, "Nightly")

    def test_browser_fallback_setting_propagation(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, _, settings, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        # Browser fallback always enabled in this design (custom command disables).
        self.assertTrue(op.browserFallbackEnabled)
        # Operational still reflects no session by default.
        self.assertEqual(op.browserState, "idle")


# ---------------------------------------------------------------------------
# Browser session events via Bridge (single queue consumer)
# ---------------------------------------------------------------------------


class BrowserSessionTests(unittest.TestCase):
    def test_browser_needed_reaches_operational(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        q, host, bridge, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        q.put(("browser_needed", "https://example.com/media", "protected"))
        bridge._drain()
        self.assertEqual(op.browserState, "needed")
        self.assertEqual(op.browserNeededUrl, "https://example.com/media")
        self.assertEqual(op.browserNeededCategory, "protected")

    def test_browser_ready_reaches_operational(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        q, host, bridge, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        q.put(("browser_needed", "https://example.com/a", "protected"))
        bridge._drain()
        q.put(("browser_session_ready", {"ok": True, "media_candidates": [{"url": "https://c"}, {"url": "https://d"}],
                                         "observed_request_count": 42}))
        bridge._drain()
        self.assertEqual(op.browserState, "ready")
        self.assertIn("42", op.browserReadySummary)
        self.assertEqual(op.browserError, "")

    def test_browser_error_reaches_operational(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        q, host, bridge, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        q.put(("browser_session_error", "verification timed out"))
        bridge._drain()
        self.assertEqual(op.browserState, "error")
        self.assertEqual(op.browserError, "verification timed out")

    def test_clear_browser_session(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        q, host, bridge, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        q.put(("browser_needed", "https://x", "protected"))
        bridge._drain()
        self.assertEqual(op.browserState, "needed")
        op.clearBrowserSession()
        self.assertEqual(op.browserState, "idle")
        self.assertEqual(op.browserNeededUrl, "")
        self.assertEqual(op.browserError, "")

    def test_malformed_browser_events_non_fatal(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        q, host, bridge, settings, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        # Malformed: missing category, wrong type, etc handled via Bridge malformed guard -> log warning
        q.put(("browser_needed", "https://x"))  # missing category
        q.put(("browser_session_ready", "not-a-dict"))
        q.put(("browser_session_error",))  # missing message
        q.put(("browser_needed", "https://y", "protected"))
        bridge._drain()
        # Last valid needed should survive
        self.assertEqual(op.browserNeededUrl, "https://y")
        self.assertGreaterEqual(bridge.log.rowCount(), 1)


# ---------------------------------------------------------------------------
# MediaObserver
# ---------------------------------------------------------------------------


class ObserverTests(unittest.TestCase):
    def test_observer_status_snapshot(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, _, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        # Snapshot is non-empty string (either status or unavailable)
        self.assertIsInstance(op.observerStatusText, str)
        self.assertTrue(len(op.observerStatusText) > 0)
        # Health bool present
        self.assertIsInstance(op.observerHealthOk, bool)

    def test_observer_refresh_worker_safe(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, _, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        orig = op.observerStatusText
        op.refreshObserverStatus()
        time.sleep(0.05)
        # After synchronous refresh, status still non-empty and no crash.
        self.assertIsInstance(op.observerStatusText, str)


# ---------------------------------------------------------------------------
# Updater (yt-dlp)
# ---------------------------------------------------------------------------


class UpdaterTests(unittest.TestCase):
    def test_updater_snapshot_current_version(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, _, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.assertIsInstance(op.updaterCurrentVersion, str)
        self.assertTrue(op.updaterCurrentVersion != "")
        self.assertIsInstance(op.updaterStatusText, str)

    def test_check_updater_success(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, bridge, settings, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        prev_check = app.check_ytdlp_update
        def slow_check(ch="Stable"):
            time.sleep(0.12)
            return {"available": True, "available_version": "2099.01.01", "current_version": "2026.08.19"}
        app.check_ytdlp_update = slow_check
        self.addCleanup(lambda: setattr(app, "check_ytdlp_update", prev_check))
        op.checkUpdater()
        # Busy flag set immediately (allow small race for immediate set)
        self.assertTrue(op.updaterBusy)
        deadline = time.time() + 3.0
        while op.updaterBusy and time.time() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        self.assertFalse(op.updaterBusy)
        self.assertTrue(op.updaterUpdateAvailable)
        self.assertEqual(op.updaterAvailableVersion, "2099.01.01")
        self.assertIn("2099", op.updaterStatusText)

    def test_check_updater_failure_handled(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, bridge, settings, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        prev_check = app.check_ytdlp_update
        app.check_ytdlp_update = lambda ch="Stable": (_ for _ in ()).throw(RuntimeError("network down"))
        self.addCleanup(lambda: setattr(app, "check_ytdlp_update", prev_check))
        op.checkUpdater()
        deadline = time.time() + 2.0
        while op.updaterBusy and time.time() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        self.assertFalse(op.updaterBusy)
        self.assertIn("network down", op.updaterStatusText.lower())

    def test_install_update_worker_no_block(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, bridge, settings, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        prev_install = app.install_ytdlp_update
        installed_flag = {}
        def fake_install(ch="Stable"):
            time.sleep(0.15)
            installed_flag["called"] = True
            return {"version": "2099.02.02", "channel": ch}
        app.install_ytdlp_update = fake_install
        self.addCleanup(lambda: setattr(app, "install_ytdlp_update", prev_install))
        start = time.time()
        op.installUpdate()
        elapsed = time.time() - start
        self.assertLess(elapsed, 0.05)
        self.assertTrue(op.updaterBusy)
        deadline = time.time() + 3.0
        while op.updaterBusy and time.time() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        self.assertFalse(op.updaterBusy)
        self.assertTrue(installed_flag.get("called"))
        self.assertIn("2099", op.updaterStatusText)

    def test_rollback_failure_handled(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, bridge, settings, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        prev_rb = app.rollback_ytdlp_update
        app.rollback_ytdlp_update = lambda: (_ for _ in ()).throw(RuntimeError("no backup"))
        self.addCleanup(lambda: setattr(app, "rollback_ytdlp_update", prev_rb))
        op.rollbackUpdate()
        deadline = time.time() + 2.0
        while op.updaterBusy and time.time() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        self.assertFalse(op.updaterBusy)
        self.assertIn("no backup", op.updaterStatusText.lower())

    def test_updater_does_not_block_gui(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, bridge, settings, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        prev_check = app.check_ytdlp_update
        def slow_check(ch="Stable"):
            time.sleep(0.3)
            return {"available": False}
        app.check_ytdlp_update = slow_check
        self.addCleanup(lambda: setattr(app, "check_ytdlp_update", prev_check))
        op.checkUpdater()
        # Should return instantly, busy true, GUI still responsive (processEvents not blocked)
        self.assertTrue(op.updaterBusy)
        # Wait briefly, still busy then completes
        time.sleep(0.05)
        QCoreApplication.processEvents()
        self.assertTrue(op.updaterBusy)
        deadline = time.time() + 2.0
        while op.updaterBusy and time.time() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        self.assertFalse(op.updaterBusy)


# ---------------------------------------------------------------------------
# Invariants: no second queue/store/browser/updater, activity log
# ---------------------------------------------------------------------------


class InvariantsTests(unittest.TestCase):
    def test_no_second_queue_consumer(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        q, host, bridge, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        self.assertIs(host.ui_queue, q)
        self.assertIs(bridge._queue, q)
        self.assertFalse(hasattr(op, "_queue"))
        self.assertFalse(hasattr(op, "ui_queue"))

    def test_no_second_settings_store(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        prev = app.SETTINGS_FILE
        patched = pathlib.Path(tmpdir.name) / "settings.json"
        app.SETTINGS_FILE = patched
        self.addCleanup(lambda: setattr(app, "SETTINGS_FILE", prev))
        q, host, _, settings, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.assertIs(settings._host, host)
        self.assertIs(op._settings, settings)
        self.assertEqual(app.SETTINGS_FILE, patched)

    def test_no_second_browser_subsystem(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        q, host, _, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        # Operational reuses host's verified session dict, not own process.
        self.assertFalse(hasattr(op, "_protected_browser_launcher"))
        self.assertFalse(hasattr(op, "BROWSER_SESSION_DIR"))

    def test_no_second_updater_subsystem(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        _, host, _, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        # Operational delegates to app.check_ytdlp_update etc., no own registry.
        self.assertFalse(hasattr(op, "_ytdlp_registry"))

    def test_activity_log_still_works_with_operational_events(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        q, host, bridge, _, op = _make_stack(tmpdir.name)
        self.addCleanup(host.shutdown)
        self.addCleanup(bridge.shutdown)
        q.put(("log", "browser fallback triggered for https://x"))
        q.put(("browser_needed", "https://x", "protected"))
        bridge._drain()
        self.assertEqual(op.browserState, "needed")
        self.assertGreaterEqual(bridge.log.rowCount(), 1)
        self.assertIn("browser fallback triggered", bridge.log.index(0).data(bridge.log.MessageRole))


if __name__ == "__main__":
    unittest.main()
