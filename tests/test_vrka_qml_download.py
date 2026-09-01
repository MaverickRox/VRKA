"""Stage 3 tests: download presentation adapter and engine host integration.

No real network, yt-dlp, FFmpeg, WebView2 or scheduler worker is started.
"""

import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

from vrka_qml.bridge import PresentationBridge
from vrka_qml.download_controller import DOWNLOAD_OPTION_DEFAULTS, DownloadController
from vrka_qml.engine_host import EngineHost

_app = None


def setUpModule():
    global _app
    _app = QCoreApplication.instance() or QCoreApplication([])


class FakeEngine:
    """Deterministic stand-in for the engine host submission seam."""

    def __init__(self):
        self.ui_queue = queue.Queue()
        self.output_folder = os.path.join(tempfile.gettempdir(), "vrka-qml-test")
        self.submitted = []
        self.failure = None

    def submit(self, task):
        if self.failure is not None:
            raise self.failure
        self.submitted.append(task)
        return object()


class ControllerTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.controller = DownloadController(self.engine)
        self.failures = []
        self.accepted = []
        self.controller.submissionFailed.connect(
            lambda title, message: self.failures.append((title, message)))
        self.controller.submissionAccepted.connect(
            lambda task_id, url: self.accepted.append((task_id, url)))


class DownloadValidationTests(ControllerTestBase):
    def test_empty_url_is_rejected(self):
        ok = self.controller.submitDownload("   ", {})
        self.assertFalse(ok)
        self.assertEqual(self.failures, [("Check Download Settings", "Paste a media URL.")])
        self.assertEqual(self.engine.submitted, [])

    def test_invalid_scheme_is_rejected(self):
        ok = self.controller.submitDownload("ftp://example.com/video", {})
        self.assertFalse(ok)
        self.assertEqual(self.failures[0][0], "Check Download Settings")
        self.assertIn("http", self.failures[0][1])
        self.assertEqual(self.engine.submitted, [])

    def test_control_characters_are_rejected(self):
        ok = self.controller.submitDownload("https://example.com/a\x01b", {})
        self.assertFalse(ok)
        self.assertEqual(self.engine.submitted, [])

    def test_valid_submission_invokes_backend_once(self):
        ok = self.controller.submitDownload("  https://example.com/watch?v=1  ", {})
        self.assertTrue(ok)
        self.assertEqual(len(self.engine.submitted), 1)
        task = self.engine.submitted[0]
        self.assertEqual(task.url, "https://example.com/watch?v=1")
        self.assertEqual(task.mode, "video")
        self.assertEqual(self.accepted, [(str(task.id), task.url)])
        self.assertEqual(self.failures, [])
        # The 3.0 submission log line is reproduced through ui_queue.
        self.assertEqual(self.engine.ui_queue.get_nowait(),
                         ("log", f"Added to queue: {task.url}"))

    def test_options_override_defaults_without_inventing_keys(self):
        ok = self.controller.submitDownload(
            "https://example.com/watch?v=2",
            {"mode": "audio", "quality": "720p (HD)", "fps60": True,
             "start_time": "00:00:05", "not_a_real_option": "x"},
        )
        self.assertTrue(ok)
        options = self.engine.submitted[0].options
        self.assertEqual(self.engine.submitted[0].mode, "audio")
        self.assertEqual(options["quality"], "720p (HD)")
        self.assertTrue(options["fps60"])
        self.assertEqual(options["start_time"], "00:00:05")
        self.assertNotIn("not_a_real_option", options)
        self.assertEqual(options["output_template"],
                         DOWNLOAD_OPTION_DEFAULTS["output_template"])

    def test_custom_mode_is_not_offered_by_this_page(self):
        ok = self.controller.submitDownload("https://example.com/v", {"mode": "custom"})
        self.assertFalse(ok)
        self.assertEqual(self.engine.submitted, [])

    def test_task_creation_failure_is_reported(self):
        self.engine.failure = RuntimeError("scheduler is stopped")
        ok = self.controller.submitDownload("https://example.com/v", {})
        self.assertFalse(ok)
        self.assertEqual(self.failures, [("Queue Unavailable", "scheduler is stopped")])
        self.assertEqual(self.accepted, [])

    def test_submission_does_not_block_or_spawn_threads(self):
        before = threading.active_count()
        started = time.monotonic()
        ok = self.controller.submitDownload("https://example.com/v", {})
        elapsed = time.monotonic() - started
        self.assertTrue(ok)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(threading.active_count(), before)


class EngineHostIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = queue.Queue()
        self.host = EngineHost(
            self.queue,
            store_path=Path(self.tmp.name) / "build010_tasks.json",
        )
        self.addCleanup(self.host.shutdown)
        self.controller = DownloadController(self.host)

    def test_submission_reaches_durable_core_and_bridge_model(self):
        ok = self.controller.submitDownload("https://example.com/watch?v=9", {})
        self.assertTrue(ok)

        # Durable record exists exactly once (TaskScheduler path, not a copy).
        records = self.host._core_adapter.scheduler.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].spec.url, "https://example.com/watch?v=9")
        self.assertEqual(len(self.host.tasks), 1)

        # The single bridge consumer turns the same tuples into model rows.
        bridge = PresentationBridge(self.queue)
        self.assertIs(bridge._queue, self.queue)
        bridge._drain()
        model = bridge.tasks
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.index(0).data(model.TaskIdRole), records[0].task_id)
        self.assertEqual(model.index(0).data(model.StatusRole), "queued")
        self.assertEqual(bridge.log.rowCount(), 1)
        self.assertIn("Added to queue", bridge.log.index(0).data(bridge.log.MessageRole))

    def test_duplicate_visible_calls_do_not_duplicate_rows(self):
        self.controller.submitDownload("https://example.com/watch?v=1", {})
        record = self.host._core_adapter.scheduler.records()[0]
        task = self.host.tasks[0]
        # Replaying the adapter visible callback must stay idempotent.
        self.host._core_adapter.visible(task)
        self.assertEqual(len(self.host.tasks), 1)
        self.assertEqual(task.id, record.task_id)

    def test_engine_methods_delegate_to_the_monolith_verbatim(self):
        # The executor is the unchanged monolith function bound to this host.
        from vrka_downloader import VRKADownloader
        delegated = getattr(self.host, "_execute_core_task")
        self.assertIs(delegated.__func__, VRKADownloader._execute_core_task)

        command = self.host._protected_browser_command(
            type("R", (), {"spec": type("S", (), {"url": "https://x"})()})(),
            "result.json",
        )
        self.assertIn("__vrka_protected_browser__", command)

    def test_engine_host_state_is_isolated_from_backend_objects(self):
        # QML receives only the controller; the engine never leaks through it.
        self.assertIsInstance(self.controller._engine, EngineHost)
        self.assertTrue(hasattr(self.host, "ui_queue"))
        self.assertFalse(hasattr(self.controller, "scheduler"))
        self.assertFalse(hasattr(self.controller, "ui_queue"))


if __name__ == "__main__":
    unittest.main()
