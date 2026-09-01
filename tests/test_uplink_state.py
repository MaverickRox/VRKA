"""Regression tests for UPLINK state machine and task count notifications."""

import os
import queue
import unittest
from pathlib import Path

# Ensure Qt offscreen platform for headless test execution
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from vrka_qml.bridge import PresentationBridge


class TestUplinkStateMachine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.queue = queue.Queue()
        self.bridge = PresentationBridge(self.queue)
        self.signal_events = []
        self.bridge.taskCountChanged.connect(self._on_task_count_changed)

    def _on_task_count_changed(self):
        self.signal_events.append({
            "task_count": self.bridge.taskCount,
            "queued_count": self.bridge.queuedCount,
            "active_count": self.bridge.activeCount,
            "completed_count": self.bridge.completedCount,
        })

    def _get_uplink_state(self) -> str:
        """Mirror MainShell.qml deterministic UPLINK state calculation."""
        if self.bridge.activeCount > 0:
            return "UPLINK ACTIVE"
        elif self.bridge.queuedCount > 0:
            return "UPLINK QUEUED"
        else:
            return "UPLINK LIVE"

    def test_initial_state_is_live(self):
        """A. Fresh launch -> UPLINK LIVE."""
        self.assertEqual(self.bridge.taskCount, 0)
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self.bridge.activeCount, 0)
        self.assertEqual(self.bridge.completedCount, 0)
        self.assertEqual(self._get_uplink_state(), "UPLINK LIVE")

    def test_single_task_lifecycle_returns_to_live(self):
        """B, C, D: Queued -> Active -> Completed transitions correctly to LIVE."""
        # 1. Insert queued task
        self.bridge.tasks.upsert("task-1", title="Video 1", status="queued")
        self.assertEqual(self.bridge.queuedCount, 1)
        self.assertEqual(self.bridge.activeCount, 0)
        self.assertEqual(self._get_uplink_state(), "UPLINK QUEUED")

        # 2. Task begins execution
        self.bridge.tasks.upsert("task-1", status="downloading")
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self.bridge.activeCount, 1)
        self.assertEqual(self._get_uplink_state(), "UPLINK ACTIVE")

        # 3. Task completes
        self.bridge.tasks.upsert("task-1", status="completed")
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self.bridge.activeCount, 0)
        self.assertEqual(self.bridge.completedCount, 1)
        self.assertEqual(self._get_uplink_state(), "UPLINK LIVE")

        # Verify signals were emitted on each state transition
        self.assertGreaterEqual(len(self.signal_events), 3)

    def test_failed_task_returns_to_live(self):
        """E. Task fails -> UPLINK LIVE."""
        self.bridge.tasks.upsert("task-1", status="queued")
        self.assertEqual(self._get_uplink_state(), "UPLINK QUEUED")

        self.bridge.tasks.upsert("task-1", status="downloading")
        self.assertEqual(self._get_uplink_state(), "UPLINK ACTIVE")

        self.bridge.tasks.upsert("task-1", status="error")
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self.bridge.activeCount, 0)
        self.assertEqual(self._get_uplink_state(), "UPLINK LIVE")

    def test_canceled_task_returns_to_live(self):
        """F. Task canceled -> UPLINK LIVE."""
        self.bridge.tasks.upsert("task-1", status="queued")
        self.assertEqual(self._get_uplink_state(), "UPLINK QUEUED")

        self.bridge.tasks.upsert("task-1", status="canceled")
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self.bridge.activeCount, 0)
        self.assertEqual(self._get_uplink_state(), "UPLINK LIVE")

    def test_multiple_tasks_remain_queued(self):
        """G, H. First task completes while second remains queued -> UPLINK QUEUED."""
        self.bridge.tasks.upsert("task-1", status="queued")
        self.bridge.tasks.upsert("task-2", status="queued")
        self.assertEqual(self.bridge.queuedCount, 2)
        self.assertEqual(self._get_uplink_state(), "UPLINK QUEUED")

        # Task 1 starts
        self.bridge.tasks.upsert("task-1", status="downloading")
        self.assertEqual(self.bridge.queuedCount, 1)
        self.assertEqual(self.bridge.activeCount, 1)
        self.assertEqual(self._get_uplink_state(), "UPLINK ACTIVE")

        # Task 1 finishes while Task 2 is still waiting
        self.bridge.tasks.upsert("task-1", status="completed")
        self.assertEqual(self.bridge.queuedCount, 1)
        self.assertEqual(self.bridge.activeCount, 0)
        self.assertEqual(self._get_uplink_state(), "UPLINK QUEUED")

        # Task 2 starts
        self.bridge.tasks.upsert("task-2", status="downloading")
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self.bridge.activeCount, 1)
        self.assertEqual(self._get_uplink_state(), "UPLINK ACTIVE")

        # Task 2 finishes
        self.bridge.tasks.upsert("task-2", status="completed")
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self.bridge.activeCount, 0)
        self.assertEqual(self._get_uplink_state(), "UPLINK LIVE")

    def test_queue_drain_event_batching(self):
        """I. Processing queue event batches correctly drives UPLINK transitions."""
        # Enqueue events into ui_queue
        self.queue.put(("task_created", "task-batch-1", "https://example.com/1", "video"))
        self.queue.put(("task_status", "task-batch-1", "queued"))
        self.bridge._drain()
        self.assertEqual(self.bridge.queuedCount, 1)
        self.assertEqual(self._get_uplink_state(), "UPLINK QUEUED")

        # Active transition
        self.queue.put(("task_status", "task-batch-1", "downloading"))
        self.bridge._drain()
        self.assertEqual(self.bridge.activeCount, 1)
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self._get_uplink_state(), "UPLINK ACTIVE")

        # Completed transition
        self.queue.put(("task_status", "task-batch-1", "completed"))
        self.bridge._drain()
        self.assertEqual(self.bridge.activeCount, 0)
        self.assertEqual(self.bridge.queuedCount, 0)
        self.assertEqual(self.bridge.completedCount, 1)
        self.assertEqual(self._get_uplink_state(), "UPLINK LIVE")


if __name__ == "__main__":
    unittest.main()
