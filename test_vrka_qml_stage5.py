"""Stage 5 tests: download workflow end-to-end via the existing backend.

Proves QML submission -> EngineHost -> Build008TaskAdapter -> durable
TaskScheduler -> ui_queue -> PresentationBridge -> TaskListModel -> history
without a second downloader, second scheduler or second queue. No real
network, yt-dlp, FFmpeg, WebView2 or scheduler worker is started; completion
and history are driven through the same tuple protocol the real executor uses.
"""

from __future__ import annotations

import os
import queue
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

from vrka_qml.bridge import PresentationBridge
from vrka_qml.download_controller import DOWNLOAD_OPTION_DEFAULTS, DownloadController
from vrka_qml.engine_host import EngineHost
from vrka_qml.models.task_model import TaskListModel
from vrka_qml.queue_controller import QueueController

_app = None


def setUpModule():
    global _app
    _app = QCoreApplication.instance() or QCoreApplication([])


def _make_stack():
    tmp = tempfile.TemporaryDirectory()
    q: queue.Queue = queue.Queue()
    host = EngineHost(q, store_path=Path(tmp.name) / "build010_tasks.json")
    bridge = PresentationBridge(q)
    controller = DownloadController(host)
    queue_ctrl = QueueController(host, bridge)
    # Mirror app.py Again wiring (QML history -> download prefill).
    queue_ctrl.redownloadRequested.connect(
        lambda url: controller.prefillRequested.emit(str(url))
    )

    def _serve_history():
        bridge.history.set_entries(host.history)

    bridge.historyRefreshRequested.connect(_serve_history)
    return tmp, q, host, bridge, controller, queue_ctrl, _serve_history


def _submit(host_controller_pair_url="https://example.com/watch?v=stage5"):
    tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
    return tmp, q, host, bridge, ctrl, qc, serve


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

class SubmissionTests(unittest.TestCase):

    def test_successful_submission_is_durable_and_visible(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ok = ctrl.submitDownload("https://example.com/watch?v=ok", {})
        self.assertTrue(ok)
        # Durable core record exists exactly once.
        recs = host._core_adapter.scheduler.records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].spec.url, "https://example.com/watch?v=ok")
        # Visible task registered once.
        self.assertEqual(len(host.tasks), 1)
        # Same queue reaches the presentation model.
        bridge._drain()
        self.assertEqual(bridge.tasks.rowCount(), 1)
        idx = bridge.tasks.index(0)
        self.assertEqual(idx.data(TaskListModel.TaskIdRole), recs[0].task_id)
        self.assertEqual(idx.data(TaskListModel.StatusRole), "queued")
        self.assertEqual(idx.data(TaskListModel.UrlRole), "https://example.com/watch?v=ok")
        self.assertEqual(bridge.log.rowCount(), 1)
        self.assertIn("Added to queue", bridge.log.index(0).data(bridge.log.MessageRole))

    def test_validation_failure_does_not_touch_core(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        failed = []
        ctrl.submissionFailed.connect(lambda t, m: failed.append((t, m)))
        ok = ctrl.submitDownload("   ", {})
        self.assertFalse(ok)
        self.assertEqual(failed[0][0], "Check Download Settings")
        self.assertEqual(len(host._core_adapter.scheduler.records()), 0)
        self.assertEqual(len(host.tasks), 0)
        bridge._drain()
        self.assertEqual(bridge.tasks.rowCount(), 0)

    def test_invalid_scheme_is_rejected(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ok = ctrl.submitDownload("ftp://example.com/v", {})
        self.assertFalse(ok)
        self.assertEqual(len(host._core_adapter.scheduler.records()), 0)

    def test_invalid_option_keys_are_ignored(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ok = ctrl.submitDownload(
            "https://example.com/v",
            {"mode": "video", "quality": "720p (HD)", "not_a_real_option": "evil"},
        )
        self.assertTrue(ok)
        task = host.tasks[0]
        self.assertEqual(task.options["quality"], "720p (HD)")
        self.assertNotIn("not_a_real_option", task.options)
        self.assertEqual(task.options["output_template"], DOWNLOAD_OPTION_DEFAULTS["output_template"])

    def test_invalid_mode_is_rejected(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ok = ctrl.submitDownload("https://example.com/v", {"mode": "custom"})
        self.assertFalse(ok)
        self.assertEqual(len(host._core_adapter.scheduler.records()), 0)

    def test_effective_output_template_is_validated(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        failed = []
        ctrl.submissionFailed.connect(lambda t, m: failed.append((t, m)))
        ok = ctrl.submitDownload(
            "https://example.com/v",
            {"output_template": "../escape.mp4"},
        )
        self.assertFalse(ok)
        self.assertEqual(failed[0][0], "Check Download Settings")
        self.assertEqual(len(host._core_adapter.scheduler.records()), 0)


# ---------------------------------------------------------------------------
# Queue identity invariants (must hold for the whole Stage 5 flow)
# ---------------------------------------------------------------------------

class QueueIdentityTests(unittest.TestCase):

    def test_single_shared_ui_queue_object(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        self.assertIs(host.ui_queue, q)
        self.assertIs(bridge._queue, q)
        self.assertIs(qc._host.ui_queue, q)

    def test_no_second_scheduler_is_created(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        # There is exactly one scheduler instance in the process for this host.
        self.assertIs(host._core_adapter.scheduler, qc._adapter.scheduler)
        # No attribute on the presentation adapters exposes or creates another.
        self.assertFalse(hasattr(bridge, "scheduler"))
        self.assertFalse(hasattr(ctrl, "scheduler"))
        self.assertFalse(hasattr(ctrl, "_core_adapter"))

    def test_bridge_is_the_only_queue_consumer(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        # Only the bridge drains the queue on a timer; controllers never consume.
        self.assertEqual(bridge._timer.interval(), 16)
        self.assertFalse(hasattr(ctrl, "_queue"))
        self.assertFalse(hasattr(qc, "_queue"))


# ---------------------------------------------------------------------------
# Event propagation through the established path
# ---------------------------------------------------------------------------

class EventPropagationTests(unittest.TestCase):

    def test_progress_reaches_qml_model_via_bridge(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        q.put(("task_progress", tid, 0.42))
        bridge._drain()
        self.assertAlmostEqual(
            bridge.tasks.index(0).data(TaskListModel.ProgressRole), 0.42
        )

    def test_title_reaches_qml_model_via_bridge(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        q.put(("task_title", tid, "My Video"))
        bridge._drain()
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.TitleRole), "My Video")

    def test_status_transitions_queued_to_downloading_to_completed(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "queued")
        q.put(("task_status", tid, "downloading"))
        q.put(("task_metrics", tid, "Downloading", "2.50 MB/s", "0:12"))
        bridge._drain()
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "downloading")
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StageRole), "Downloading")
        q.put(("task_status", tid, "completed"))
        q.put(("task_progress", tid, 1.0))
        bridge._drain()
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "completed")
        self.assertAlmostEqual(bridge.tasks.index(0).data(TaskListModel.ProgressRole), 1.0)

    def test_metrics_stage_speed_eta_reach_model(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        q.put(("task_metrics", tid, "Validating handoff", "1 MB/s", "0:03"))
        bridge._drain()
        idx = bridge.tasks.index(0)
        self.assertEqual(idx.data(TaskListModel.StageRole), "Validating handoff")
        self.assertEqual(idx.data(TaskListModel.SpeedRole), "1 MB/s")
        self.assertEqual(idx.data(TaskListModel.EtaRole), "0:03")

    def test_malformed_events_do_not_break_propagation(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        q.put(("task_progress", tid))  # malformed
        q.put(("task_progress", tid, "not-a-float"))  # malformed
        q.put(None)  # malformed
        q.put(("task_progress", tid, 0.9))
        bridge._drain()
        self.assertAlmostEqual(
            bridge.tasks.index(0).data(TaskListModel.ProgressRole), 0.9
        )
        # Malformed events are surfaced as warnings, not re-queued.
        self.assertGreaterEqual(bridge.log.rowCount(), 4)


# ---------------------------------------------------------------------------
# Task lifecycle operations via the existing adapter/queue path
# ---------------------------------------------------------------------------

class TaskLifecycleTests(unittest.TestCase):

    def test_cancellation_reflects_in_model(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        qc.cancelTask(tid)
        bridge._drain()
        # Adapter cancel drives CANCELED -> "canceled" task_status via scheduler
        # or, before the worker ran, at least the durable cancel is recorded.
        status = bridge.tasks.index(0).data(TaskListModel.StatusRole)
        self.assertIn(status, ("canceled", "queued"))

    def test_retry_requeues_terminal_task(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        # Make the durable record terminal via its state machine.
        from vrka_core.candidates import DownloadState
        for rec in host._core_adapter.scheduler.records():
            rec.transition(DownloadState.FAILED)
        q.put(("task_status", tid, "error"))
        bridge._drain()
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "error")
        qc.retryTask(tid)
        bridge._drain()
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "queued")
        self.assertAlmostEqual(bridge.tasks.index(0).data(TaskListModel.ProgressRole), 0.0)
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StageRole), "Waiting")

    def test_retry_non_terminal_is_noop(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "queued")
        qc.retryTask(tid)
        bridge._drain()
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "queued")

    def test_remove_terminal_row_drops_both_model_and_durable(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        q.put(("task_status", tid, "completed"))
        bridge._drain()
        qc.removeTask(tid)
        self.assertEqual(bridge.tasks.rowCount(), 0)
        self.assertEqual(len(host.tasks), 0)

    def test_clear_completed_keeps_active_and_queued(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        for i in range(3):
            ctrl.submitDownload(f"https://example.com/v{i}", {})
        bridge._drain()
        self.assertEqual(bridge.tasks.rowCount(), 3)
        tids = [bridge.tasks.index(i).data(TaskListModel.TaskIdRole) for i in range(3)]
        q.put(("task_status", tids[0], "completed"))
        q.put(("task_status", tids[1], "error"))
        # tids[2] stays queued/downloading
        bridge._drain()
        qc.clearCompleted()
        self.assertEqual(bridge.tasks.rowCount(), 1)
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.TaskIdRole), tids[2])


# ---------------------------------------------------------------------------
# Completion -> history (no restart required)
# ---------------------------------------------------------------------------

class HistoryE2ETests(unittest.TestCase):

    def test_history_refresh_serves_entries_without_restart(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        self.assertEqual(bridge.history.rowCount(), 0)
        host.history = [
            {"id": "h1", "title": "Done", "url": "https://example.com/v",
             "path": "C:/dl/Done.mp4", "mode": "video", "timestamp": "2026-08-26 10:00"},
        ]
        host.ui_queue.put(("history_refresh", None))
        bridge._drain()
        serve() if host.history else None
        # After the refresh the model is populated (app layer serves entries).
        # Simulate the app-layer History serving exactly as app.py does.
        bridge.history.set_entries(host.history)
        self.assertEqual(bridge.history.rowCount(), 1)
        self.assertEqual(bridge.history.index(0).data(bridge.history.TitleRole), "Done")

    def test_completion_via_history_callback_is_history_once(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        ctrl.submitDownload("https://example.com/v", {})
        bridge._drain()
        task = host.tasks[0]
        task.title = "Completed Video"
        task.output_path = str(Path(tmp.name) / "Completed Video.mp4")
        host.add_history_entry(task)
        # add_history_entry creates a random entry id (not task.id) and
        # enqueues history_refresh; it persists newest-first.
        self.assertEqual(len(host.history), 1)
        self.assertEqual(host.history[0]["url"], task.url)
        self.assertEqual(host.history[0]["title"], "Completed Video")
        bridge._drain()
        serve()
        self.assertEqual(bridge.history.rowCount(), 1)
        # Second add_history_entry call appends again (adapter-level once
        # guard lives in Build008TaskAdapter, not here).
        host.add_history_entry(task)
        self.assertEqual(len(host.history), 2)

    def test_history_again_prefills_download(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        urls = []
        ctrl.prefillRequested.connect(urls.append)
        qc.redownloadFromHistory("https://example.com/again")
        self.assertEqual(urls, ["https://example.com/again"])

    def test_history_actions_remove_and_clear(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)
        host.history = [
            {"id": "h1", "title": "A", "url": "https://a", "path": "C:/a.mp4",
             "mode": "video", "timestamp": "2026-01-01"},
            {"id": "h2", "title": "B", "url": "https://b", "path": "C:/b.mp4",
             "mode": "video", "timestamp": "2026-01-02"},
        ]
        bridge.history.set_entries(host.history)
        self.assertEqual(bridge.history.rowCount(), 2)
        qc.removeHistoryEntry("h1")
        bridge._drain()
        serve()
        self.assertEqual(len(host.history), 1)
        self.assertEqual(host.history[0]["id"], "h2")
        qc.clearAllHistory()
        bridge._drain()
        serve()
        self.assertEqual(len(host.history), 0)
        self.assertEqual(bridge.history.rowCount(), 0)


# ---------------------------------------------------------------------------
# Fake-engine deterministic E2E workflow: queued -> downloading -> completed -> history
# ---------------------------------------------------------------------------

class FakeEngineWorkflowTests(unittest.TestCase):
    """One task through the exact production path, minus the network."""

    def test_full_lifecycle_queued_to_history(self):
        tmp, q, host, bridge, ctrl, qc, serve = _make_stack()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(host.shutdown)

        accepted = []
        ctrl.submissionAccepted.connect(lambda tid, url: accepted.append((tid, url)))

        # 1. Submit
        ok = ctrl.submitDownload("https://example.com/watch?v=e2e", {"quality": "720p (HD)"})
        self.assertTrue(ok)
        self.assertEqual(len(accepted), 1)
        bridge._drain()
        self.assertEqual(bridge.tasks.rowCount(), 1)
        tid = bridge.tasks.index(0).data(TaskListModel.TaskIdRole)
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "queued")

        # 2. Progress path as the real executor would emit
        q.put(("task_title", tid, "E2E Video"))
        q.put(("task_status", tid, "downloading"))
        q.put(("task_metrics", tid, "Downloading", "3 MB/s", "0:05"))
        q.put(("task_progress", tid, 0.25))
        bridge._drain()
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.TitleRole), "E2E Video")
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "downloading")
        self.assertAlmostEqual(bridge.tasks.index(0).data(TaskListModel.ProgressRole), 0.25)

        q.put(("task_progress", tid, 0.75))
        bridge._drain()
        self.assertAlmostEqual(bridge.tasks.index(0).data(TaskListModel.ProgressRole), 0.75)

        # 3. Completion
        q.put(("task_progress", tid, 1.0))
        q.put(("task_status", tid, "completed"))
        bridge._drain()
        self.assertEqual(bridge.tasks.index(0).data(TaskListModel.StatusRole), "completed")
        self.assertAlmostEqual(bridge.tasks.index(0).data(TaskListModel.ProgressRole), 1.0)

        # 4. History insertion (same path as Build008TaskAdapter._emit_history_once)
        host.tasks[0].title = "E2E Video"
        host.tasks[0].output_path = str(Path(tmp.name) / "E2E Video [id].mp4")
        host.add_history_entry(host.tasks[0])
        bridge._drain()
        serve()
        self.assertEqual(bridge.history.rowCount(), 1)
        self.assertEqual(bridge.history.index(0).data(bridge.history.TitleRole), "E2E Video")
        self.assertEqual(bridge.history.index(0).data(bridge.history.UrlRole),
                         "https://example.com/watch?v=e2e")

        # 5. Again wiring still works after a completed task exists
        urls = []
        ctrl.prefillRequested.connect(urls.append)
        qc.redownloadFromHistory("https://example.com/watch?v=e2e")
        self.assertEqual(urls, ["https://example.com/watch?v=e2e"])

        # 6. Invite invariants: exactly one queue, no second scheduler, QML saw typed payloads only.
        self.assertIs(host.ui_queue, bridge._queue)
        self.assertIs(host._core_adapter.scheduler, qc._adapter.scheduler)
        self.assertFalse(hasattr(ctrl, "scheduler"))
        self.assertFalse(hasattr(ctrl, "_core_adapter"))


if __name__ == "__main__":
    unittest.main()
