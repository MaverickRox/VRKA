"""Stage 2 tests: presentation bridge and models (offscreen Qt)."""

import os
import queue
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

from vrka_qml.bridge import BATCH_LIMIT, TICK_MS, PresentationBridge
from vrka_qml.models import ActivityLogModel, HistoryListModel, TaskListModel

_app = None


def setUpModule():
    global _app
    _app = QCoreApplication.instance() or QCoreApplication([])


class BridgeTestBase(unittest.TestCase):
    def setUp(self):
        self.queue = queue.Queue()
        self.bridge = PresentationBridge(self.queue)
        self.changes = []
        self.bridge.tasks.dataChanged.connect(
            lambda top_left, bottom_right, roles: self.changes.append(
                (top_left.row(), bottom_right.row(), list(roles))
            )
        )

    def drain(self):
        self.bridge._drain()

    def put(self, *message):
        self.queue.put(message)


class BridgeInitializationTests(BridgeTestBase):
    def test_bridge_initialization_wires_models_and_timer(self):
        self.assertEqual(self.bridge.tasks.rowCount(), 0)
        self.assertEqual(self.bridge.history.rowCount(), 0)
        self.assertEqual(self.bridge.log.rowCount(), 0)
        self.assertEqual(self.bridge._timer.interval(), TICK_MS)
        self.assertFalse(self.bridge._timer.isActive())

    def test_start_and_shutdown_control_the_tick_timer(self):
        self.bridge.start()
        self.assertTrue(self.bridge._timer.isActive())
        self.assertTrue(self.bridge.shutdown())
        self.assertFalse(self.bridge._timer.isActive())

    def test_empty_queue_drain_is_a_no_op(self):
        self.drain()
        self.assertEqual(self.bridge.tasks.rowCount(), 0)
        self.assertEqual(self.bridge.log.rowCount(), 0)
        self.assertEqual(self.changes, [])


class TaskEventTests(BridgeTestBase):
    def test_status_event_inserts_task_row(self):
        self.put("task_status", "t1", "queued")
        self.drain()
        model = self.bridge.tasks
        self.assertEqual(model.rowCount(), 1)
        index = model.index(0)
        self.assertEqual(index.data(model.TaskIdRole), "t1")
        self.assertEqual(index.data(model.StatusRole), "queued")

    def test_progress_update_is_targeted_data_changed(self):
        self.put("task_status", "t1", "queued")
        self.drain()
        self.changes.clear()

        self.put("task_progress", "t1", 0.5)
        self.drain()

        self.assertEqual(len(self.changes), 1)
        row, _, roles = self.changes[0]
        self.assertEqual(row, 0)
        self.assertIn(self.bridge.tasks.ProgressRole, roles)
        self.assertEqual(self.bridge.tasks.index(0).data(
            self.bridge.tasks.ProgressRole), 0.5)

    def test_status_update_changes_status_role_only(self):
        self.put("task_status", "t1", "queued")
        self.drain()
        self.changes.clear()

        self.put("task_status", "t1", "downloading")
        self.drain()

        self.assertEqual(len(self.changes), 1)
        self.assertIn(self.bridge.tasks.StatusRole, self.changes[0][2])
        self.assertEqual(self.bridge.tasks.index(0).data(
            self.bridge.tasks.StatusRole), "downloading")

    def test_title_update_changes_title_role(self):
        self.put("task_title", "t1", "My Video")
        self.drain()
        self.assertEqual(self.bridge.tasks.index(0).data(
            self.bridge.tasks.TitleRole), "My Video")

    def test_metrics_update_changes_stage_speed_eta(self):
        self.put("task_metrics", "t1", "Downloading", "2.50 MB/s", "0:12")
        self.drain()
        index = self.bridge.tasks.index(0)
        self.assertEqual(index.data(self.bridge.tasks.StageRole), "Downloading")
        self.assertEqual(index.data(self.bridge.tasks.SpeedRole), "2.50 MB/s")
        self.assertEqual(index.data(self.bridge.tasks.EtaRole), "0:12")

    def test_latest_wins_progress_coalescing_single_tick(self):
        self.put("task_progress", "t1", 0.1)
        self.put("task_progress", "t1", 0.2)
        self.put("task_progress", "t2", 0.9)
        self.put("task_progress", "t1", 0.3)
        self.drain()

        model = self.bridge.tasks
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.index(0).data(model.ProgressRole), 0.3)
        self.assertEqual(model.row_for("t2"), 1)
        self.assertEqual(model.index(1).data(model.ProgressRole), 0.9)
        # One insert per new row; no intermediate progress notifications.
        progress_changes = [
            change for change in self.changes
            if model.ProgressRole in change[2]
        ]
        self.assertEqual(progress_changes, [])

    def test_batch_limit_bounds_the_drain(self):
        for index in range(BATCH_LIMIT + 50):
            self.put("log", f"line {index}")
        self.drain()
        self.assertFalse(self.queue.empty())
        self.assertEqual(self.bridge.log.rowCount(), BATCH_LIMIT)
        self.drain()
        self.assertTrue(self.queue.empty())


class HistoryEventTests(BridgeTestBase):
    def test_history_refresh_requests_a_history_serve(self):
        requested = []
        self.bridge.historyRefreshRequested.connect(lambda: requested.append(True))
        self.put("history_refresh", None)
        self.drain()
        self.assertEqual(requested, [True])

    def test_history_model_set_entries_normalizes_and_bounds(self):
        model = self.bridge.history
        entries = [
            {"id": f"e{i}", "title": f"T{i}", "url": "https://x",
             "path": f"C:/dl/T{i}.mp4", "mode": "video",
             "timestamp": "2026-08-26 10:00"}
            for i in range(1200)
        ]
        model.set_entries(entries)
        self.assertEqual(model.rowCount(), 1000)
        self.assertEqual(model.index(0).data(model.EntryIdRole), "e0")
        self.assertEqual(model.index(0).data(model.TitleRole), "T0")

    def test_history_model_ignores_non_dict_entries(self):
        model = self.bridge.history
        model.set_entries(["junk", {"id": "e1", "title": "ok"}])
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.index(0).data(model.TitleRole), "ok")


class ActivityLogTests(BridgeTestBase):
    def test_log_events_are_appended_with_levels(self):
        self.put("log", "all good")
        self.put("log", "WARNING: careful")
        self.put("log", "ERROR: broken")
        self.drain()
        model = self.bridge.log
        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(model.index(0).data(model.LevelRole), "info")
        self.assertEqual(model.index(1).data(model.LevelRole), "warning")
        self.assertEqual(model.index(2).data(model.LevelRole), "error")
        self.assertEqual(model.index(2).data(model.MessageRole), "ERROR: broken")

    def test_log_model_is_bounded_with_head_trimming(self):
        model = ActivityLogModel(capacity=1000)
        removed = []
        model.rowsRemoved.connect(
            lambda parent, first, last: removed.append((first, last))
        )
        model.append_messages([f"line {i}" for i in range(1200)])
        self.assertEqual(model.rowCount(), 1000)
        self.assertEqual(model.index(0).data(model.MessageRole), "line 200")
        self.assertEqual(removed, [(0, 199)])

        model.append_messages([f"more {i}" for i in range(5)])
        self.assertEqual(model.rowCount(), 1000)
        self.assertEqual(model.index(0).data(model.MessageRole), "line 205")


class ErrorHandlingTests(BridgeTestBase):
    def test_unknown_event_is_reported_not_fatal(self):
        self.put("totally_unknown", 1, 2, 3)
        self.drain()
        model = self.bridge.log
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.index(0).data(model.LevelRole), "warning")
        self.assertIn("totally_unknown", model.index(0).data(model.MessageRole))

    def test_malformed_events_do_not_crash_the_bridge(self):
        self.put("task_progress", "t1")  # missing value
        self.put("task_progress", "t2", "not-a-float")  # bad value
        self.put(None)  # not a tuple
        self.put(())  # empty tuple
        self.drain()
        model = self.bridge.log
        self.assertEqual(model.rowCount(), 4)
        for row in range(model.rowCount()):
            self.assertEqual(model.index(row).data(model.LevelRole), "warning")
        self.assertFalse(self.queue.unfinished_tasks)

    def test_malformed_event_does_not_poison_the_rest_of_the_batch(self):
        self.put("task_progress", "bad")
        self.put("log", "still alive")
        self.drain()
        self.assertEqual(self.bridge.tasks.rowCount(), 0)
        self.assertEqual(self.bridge.log.rowCount(), 2)
        self.assertEqual(self.bridge.log.index(1).data(ActivityLogModel.MessageRole),
                         "still alive")


class TypedSignalTests(BridgeTestBase):
    def test_runtime_done_signal(self):
        seen = []
        self.bridge.runtimeUpdateDone.connect(lambda: seen.append(True))
        self.put("runtime_done", None)
        self.drain()
        self.assertEqual(seen, [True])

    def test_browser_signals_carry_typed_payloads(self):
        ready = []
        needed = []
        errors = []
        self.bridge.browserSessionReady.connect(ready.append)
        self.bridge.browserNeeded.connect(lambda url, category: needed.append((url, category)))
        self.bridge.browserSessionError.connect(errors.append)
        self.put("browser_session_ready", {"ok": True, "media_candidates": []})
        self.put("browser_needed", "https://example.com", "video")
        self.put("browser_session_error", "boom")
        self.drain()
        self.assertEqual(ready, [{"ok": True, "media_candidates": []}])
        self.assertEqual(needed, [("https://example.com", "video")])
        self.assertEqual(errors, ["boom"])

    def test_task_removal_updates_row_count(self):
        self.put("task_status", "t1", "queued")
        self.put("task_status", "t2", "queued")
        self.drain()
        self.assertEqual(self.bridge.taskCount, 2)
        self.assertTrue(self.bridge.tasks.remove_task("t1"))
        self.assertEqual(self.bridge.taskCount, 1)
        self.assertEqual(self.bridge.tasks.index(0).data(
            self.bridge.tasks.TaskIdRole), "t2")


if __name__ == "__main__":
    unittest.main()
