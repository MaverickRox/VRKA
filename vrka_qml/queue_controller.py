"""Queue and history presentation actions (Stage 4).

Thin adapter between QML user actions and the existing backend. No second
state machine, no backend modification. Each Slot delegates to the
``Build008TaskAdapter`` for durable operations and to the ``EngineHost``
for presentation-model housekeeping.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl


class QueueController(QObject):
    """QML-facing actions for the Queue and History views."""

    redownloadRequested = Signal(str)

    def __init__(self, engine_host, bridge, *, parent=None):
        super().__init__(parent)
        self._host = engine_host
        self._bridge = bridge
        self._adapter = engine_host._core_adapter

    # ------------------------------------------------------------------
    # Queue actions
    # ------------------------------------------------------------------

    @Slot(str)
    def cancelTask(self, task_id: str) -> None:
        tid = str(task_id)
        self._adapter.cancel(tid)

    @Slot(str)
    def retryTask(self, task_id: str) -> None:
        tid = str(task_id)
        if self._adapter.retry(tid):
            self._host.ui_queue.put(("task_status", tid, "queued"))
            self._host.ui_queue.put(("task_progress", tid, 0.0))
            self._host.ui_queue.put(("task_metrics", tid, "Waiting", "", ""))

    @Slot(str)
    def removeTask(self, task_id: str) -> None:
        tid = str(task_id)
        self._adapter.remove(tid)
        with self._host.tasks_lock:
            self._host.tasks = [t for t in self._host.tasks if t.id != tid]
        self._bridge.tasks.remove_task(tid)

    @Slot()
    def clearCompleted(self) -> None:
        terminal = {"completed", "error", "canceled"}
        model = self._bridge.tasks
        to_remove = [
            str(r.get("task_id") or r.get("taskId") or "")
            for r in list(model._rows)
            if str(r.get("status", "")).lower() in terminal
        ]
        self._adapter.clear_finished()
        with self._host.tasks_lock:
            self._host.tasks = [
                t for t in self._host.tasks if t.id not in to_remove
            ]
        for tid in to_remove:
            if tid:
                model.remove_task(tid)

    # ------------------------------------------------------------------
    # History actions
    # ------------------------------------------------------------------

    @Slot(str)
    def openHistoryPath(self, path: str) -> None:
        import os
        target = str(path or "")
        if target and os.path.exists(target):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))
        elif target and os.path.exists(os.path.dirname(target)):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(target)))
        else:
            self._host.ui_queue.put(("log", "File not found on disk."))

    @Slot(str)
    def redownloadFromHistory(self, url: str) -> None:
        self.redownloadRequested.emit(str(url))

    @Slot(str)
    def removeHistoryEntry(self, entry_id: str) -> None:
        self._host.remove_history_entry(str(entry_id))

    @Slot()
    def clearAllHistory(self) -> None:
        self._host.history = []
        self._host.save_history()
        self._host.ui_queue.put(("history_refresh", None))
