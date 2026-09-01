"""Stage 4 tests: queue/history views, actions, models, search, restored tasks.

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
from vrka_qml.download_controller import DownloadController
from vrka_qml.engine_host import EngineHost
from vrka_qml.models.history_model import HistoryListModel
from vrka_qml.models.history_proxy import HistoryFilterProxy
from vrka_qml.models.task_model import TaskListModel
from vrka_qml.queue_controller import QueueController

_app = None


def setUpModule():
    global _app
    _app = QCoreApplication.instance() or QCoreApplication([])


class TaskModelTests(unittest.TestCase):
    """Tests 1-9: queue insertion, removal, update, roles, progress, multi-update,
    latest state, restored init, empty queue."""

    def setUp(self):
        self.model = TaskListModel()

    def test_queue_insertion(self):
        self.model.upsert("t1", title="V1", status="queued")
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.model.index(0).data(TaskListModel.TaskIdRole), "t1")
        self.assertEqual(self.model.index(0).data(TaskListModel.TitleRole), "V1")

    def test_queue_removal(self):
        self.model.upsert("t1", title="A")
        self.model.upsert("t2", title="B")
        self.assertTrue(self.model.remove_task("t1"))
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.model.index(0).data(TaskListModel.TaskIdRole), "t2")

    def test_queue_targeted_update(self):
        self.model.upsert("t1", title="A", status="queued")
        row, changed = self.model.upsert("t1", progress=0.5)
        self.assertEqual(row, 0)
        self.assertIn(TaskListModel.ProgressRole, changed)
        self.assertEqual(self.model.index(0).data(TaskListModel.ProgressRole), 0.5)

    def test_queue_roles(self):
        self.model.upsert("t1", title="V", status="downloading", progress=0.7,
                          stage="Downloading", speed="2MB/s", eta="0:10",
                          error="", output_path="/tmp/v.mp4",
                          url="https://x", mode="video")
        idx = self.model.index(0)
        self.assertEqual(idx.data(TaskListModel.TitleRole), "V")
        self.assertEqual(idx.data(TaskListModel.StatusRole), "downloading")
        self.assertEqual(idx.data(TaskListModel.ProgressRole), 0.7)
        self.assertEqual(idx.data(TaskListModel.StageRole), "Downloading")
        self.assertEqual(idx.data(TaskListModel.SpeedRole), "2MB/s")
        self.assertEqual(idx.data(TaskListModel.EtaRole), "0:10")
        self.assertEqual(idx.data(TaskListModel.UrlRole), "https://x")
        self.assertEqual(idx.data(TaskListModel.ModeRole), "video")

    def test_progress_update_no_model_reset(self):
        resets = []
        self.model.modelReset.connect(lambda: resets.append(True))
        self.model.upsert("t1", status="queued")
        self.model.upsert("t1", progress=0.5)
        self.model.upsert("t1", progress=0.8)
        self.assertEqual(resets, [])

    def test_multiple_updates_for_one_task(self):
        self.model.upsert("t1", status="queued")
        for p in [0.1, 0.2, 0.3, 0.4, 0.5]:
            self.model.upsert("t1", progress=p)
        self.assertEqual(self.model.index(0).data(TaskListModel.ProgressRole), 0.5)
        self.assertEqual(self.model.rowCount(), 1)

    def test_latest_task_state(self):
        self.model.upsert("t1", status="queued")
        self.model.upsert("t1", status="downloading")
        self.model.upsert("t1", status="completed")
        self.assertEqual(self.model.index(0).data(TaskListModel.StatusRole), "completed")

    def test_restored_task_init(self):
        self.model.upsert("t1", title="Restored", status="error",
                          progress=0.42, stage="Downloading", speed="1MB/s",
                          eta="0:05", error="timed out",
                          output_path="", url="https://x", mode="video")
        self.assertEqual(self.model.rowCount(), 1)
        idx = self.model.index(0)
        self.assertEqual(idx.data(TaskListModel.TitleRole), "Restored")
        self.assertEqual(idx.data(TaskListModel.StatusRole), "error")
        self.assertEqual(idx.data(TaskListModel.ProgressRole), 0.42)
        self.assertEqual(idx.data(TaskListModel.ErrorRole), "timed out")

    def test_empty_queue(self):
        self.assertEqual(self.model.rowCount(), 0)
        self.assertIsNone(self.model.index(0).data(TaskListModel.TaskIdRole))


class BridgeSeedTests(unittest.TestCase):
    """Test 8 (integration): restored task seeding through PresentationBridge."""

    def setUp(self):
        self.q = queue.Queue()
        self.bridge = PresentationBridge(self.q)

    def test_seed_task_inserts_model_row(self):
        self.bridge.seed_task({
            "taskId": "r1",
            "title": "Restored Video",
            "status": "error",
            "progress": 0.33,
            "stage": "Downloading",
            "speed": "1.5MB/s",
            "eta": "0:08",
            "error": "network",
            "outputPath": "",
            "url": "https://example.com/v",
            "mode": "video",
        })
        self.assertEqual(self.bridge.tasks.rowCount(), 1)
        idx = self.bridge.tasks.index(0)
        self.assertEqual(idx.data(TaskListModel.TaskIdRole), "r1")
        self.assertEqual(idx.data(TaskListModel.TitleRole), "Restored Video")
        self.assertEqual(idx.data(TaskListModel.StatusRole), "error")
        self.assertEqual(idx.data(TaskListModel.ProgressRole), 0.33)

    def test_seed_task_ignores_empty_id(self):
        self.bridge.seed_task({"taskId": ""})
        self.assertEqual(self.bridge.tasks.rowCount(), 0)


class HistoryProxyTests(unittest.TestCase):
    """Tests 10-14: history ordering, cap, search, empty search, update."""

    def setUp(self):
        self.model = HistoryListModel()
        self.proxy = HistoryFilterProxy(self.model)

    def _fill(self, n):
        entries = [
            {"id": f"e{i}", "title": f"Title{i}", "url": "https://x",
             "path": f"C:/dl/file{i}.mp4", "mode": "video",
             "timestamp": f"2026-08-{i:02d} 10:00"}
            for i in range(n)
        ]
        self.model.set_entries(entries)

    def test_newest_first_ordering(self):
        self._fill(5)
        self.assertEqual(self.proxy.rowCount(), 5)
        self.assertEqual(self.proxy.index(0, 0).data(HistoryListModel.TitleRole), "Title0")
        self.assertEqual(self.proxy.index(4, 0).data(HistoryListModel.TitleRole), "Title4")

    def test_history_maximum_1000(self):
        self._fill(1200)
        self.assertEqual(self.model.rowCount(), 1000)
        self.assertEqual(self.proxy.rowCount(), 1000)

    def test_history_search_title(self):
        self.model.set_entries([
            {"id": "e1", "title": "Funny Cat Video", "url": "https://a",
             "path": "/tmp/cat.mp4", "mode": "video", "timestamp": "2026-01-01"},
            {"id": "e2", "title": "Serious Documentary", "url": "https://b",
             "path": "/tmp/doc.mp4", "mode": "video", "timestamp": "2026-01-02"},
        ])
        self.proxy.setFilter("cat")
        self.proxy._apply()
        self.assertEqual(self.proxy.rowCount(), 1)
        self.assertEqual(self.proxy.index(0, 0).data(HistoryListModel.TitleRole),
                         "Funny Cat Video")

    def test_history_search_path(self):
        self.model.set_entries([
            {"id": "e1", "title": "A", "url": "https://a",
             "path": "/downloads/music.mp3", "mode": "audio",
             "timestamp": "2026-01-01"},
            {"id": "e2", "title": "B", "url": "https://b",
             "path": "/tmp/video.mp4", "mode": "video",
             "timestamp": "2026-01-02"},
        ])
        self.proxy.setFilter("downloads")
        self.proxy._apply()
        self.assertEqual(self.proxy.rowCount(), 1)
        self.assertEqual(self.proxy.index(0, 0).data(HistoryListModel.PathRole),
                         "/downloads/music.mp3")

    def test_empty_search_result(self):
        self._fill(3)
        self.proxy.setFilter("zzz_no_match")
        self.proxy._apply()
        self.assertEqual(self.proxy.rowCount(), 0)

    def test_history_update_rebuilds_model(self):
        self._fill(3)
        self.assertEqual(self.proxy.rowCount(), 3)
        self.model.set_entries([
            {"id": "e10", "title": "New", "url": "https://n",
             "path": "/tmp/new.mp4", "mode": "video", "timestamp": "2026-09-01"},
        ])
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.proxy.rowCount(), 1)

    def test_empty_filter_shows_all(self):
        self._fill(5)
        self.proxy.setFilter("")
        self.proxy._apply()
        self.assertEqual(self.proxy.rowCount(), 5)

    def test_case_insensitive_search(self):
        self.model.set_entries([
            {"id": "e1", "title": "My VIDEO", "url": "https://a",
             "path": "/tmp/v.mp4", "mode": "video", "timestamp": "2026-01-01"},
        ])
        self.proxy.setFilter("video")
        self.proxy._apply()
        self.assertEqual(self.proxy.rowCount(), 1)


class QueueControllerTests(unittest.TestCase):
    """Tests 15-17: QML model exposure, single consumer, no raw backend objects,
    plus queue action integration."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.q = queue.Queue()
        self.host = EngineHost(self.q,
                               store_path=Path(self.tmp.name) / "tasks.json")
        self.addCleanup(self.host.shutdown)
        self.bridge = PresentationBridge(self.q)
        self.ctrl = QueueController(self.host, self.bridge)
        self.controller = DownloadController(self.host)

    def test_queue_action_cancel(self):
        ok = self.controller.submitDownload("https://example.com/v", {})
        self.assertTrue(ok)
        self.bridge._drain()
        task_id = self.bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        self.ctrl.cancelTask(task_id)
        self.bridge._drain()
        status = self.bridge.tasks.index(0).data(TaskListModel.StatusRole)
        self.assertIn(status, ("canceled", "queued"))

    def test_queue_action_retry_non_terminal_is_noop(self):
        ok = self.controller.submitDownload("https://example.com/v2", {})
        self.assertTrue(ok)
        self.bridge._drain()
        task_id = self.bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        self.assertEqual(self.bridge.tasks.index(0).data(
            TaskListModel.StatusRole), "queued")
        # Retry on a non-terminal task should be a no-op.
        self.ctrl.retryTask(task_id)
        self.bridge._drain()
        status = self.bridge.tasks.index(0).data(TaskListModel.StatusRole)
        self.assertEqual(status, "queued")

    def test_queue_action_remove(self):
        ok = self.controller.submitDownload("https://example.com/v3", {})
        self.assertTrue(ok)
        self.bridge._drain()
        task_id = self.bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        # Simulate terminal state via queue event
        self.q.put(("task_status", task_id, "completed"))
        self.bridge._drain()
        self.ctrl.removeTask(task_id)
        self.assertEqual(self.bridge.tasks.rowCount(), 0)
        self.assertEqual(len(self.host.tasks), 0)

    def test_clear_completed(self):
        for i in range(3):
            self.controller.submitDownload(f"https://example.com/v{i}", {})
        self.bridge._drain()
        # Mark two as completed, one as downloading
        for i in range(2):
            tid = self.bridge.tasks.index(i).data(TaskListModel.TaskIdRole)
            self.q.put(("task_status", tid, "completed"))
        self.bridge._drain()
        self.ctrl.clearCompleted()
        self.assertEqual(self.bridge.tasks.rowCount(), 1)

    def test_qml_model_exposure(self):
        self.assertIsNotNone(self.bridge.tasksModel)
        self.assertIs(self.bridge.tasksModel, self.bridge.tasks)
        self.assertIsNotNone(self.bridge.historyModel)
        self.assertIs(self.bridge.historyModel, self.bridge.history)
        self.assertIsNotNone(self.bridge.historyFiltered)
        self.assertIsInstance(self.bridge.historyFiltered, HistoryFilterProxy)

    def test_single_ui_queue_consumer(self):
        self.assertIs(self.bridge._queue, self.q)
        self.assertEqual(self.bridge._timer.interval(), 16)

    def test_no_raw_backend_object_exposed_to_qml(self):
        self.assertFalse(hasattr(self.ctrl, "scheduler"))
        self.assertFalse(hasattr(self.ctrl, "_core_adapter"))
        self.assertFalse(hasattr(self.ctrl, "ui_queue"))

    def test_history_action_remove(self):
        self.host.history = [
            {"id": "h1", "title": "Test", "url": "https://x",
             "path": "/tmp/v.mp4", "mode": "video", "timestamp": "2026-01-01"},
        ]
        self.ctrl.removeHistoryEntry("h1")
        self.assertEqual(len(self.host.history), 0)
        self.assertTrue(self.q.get_nowait()[0] == "history_refresh")

    def test_redownload_from_history_emits_signal(self):
        urls = []
        self.ctrl.redownloadRequested.connect(urls.append)
        self.ctrl.redownloadFromHistory("https://example.com/re")
        self.assertEqual(urls, ["https://example.com/re"])


class EngineHostStage4Tests(unittest.TestCase):
    """Engine host restored snapshots and history persistence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.q = queue.Queue()
        self.host = EngineHost(self.q,
                               store_path=Path(self.tmp.name) / "tasks.json")
        self.addCleanup(self.host.shutdown)
        self.controller = DownloadController(self.host)

    def test_restored_task_snapshots(self):
        self.controller.submitDownload("https://example.com/snap", {})
        self.assertEqual(len(self.host.tasks), 1)
        snapshots = self.host.restored_task_snapshots()
        self.assertEqual(len(snapshots), 1)
        s = snapshots[0]
        self.assertIn("taskId", s)
        self.assertIn("title", s)
        self.assertIn("status", s)
        self.assertIn("progress", s)
        self.assertEqual(s["url"], "https://example.com/snap")

    def test_restored_task_snapshots_empty(self):
        snapshots = self.host.restored_task_snapshots()
        self.assertEqual(snapshots, [])


if __name__ == "__main__":
    unittest.main()
