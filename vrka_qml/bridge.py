"""Stage 2 presentation bridge: ui_queue tuples -> Qt presentation models.

Consumer-side twin of ``VRKADownloader.process_ui_queue``
(vrka_downloader.py:5859): same tuple protocol, same latest-wins per-task
coalescing, same batch cap - but the drain runs on a Qt timer on the GUI
thread (~16 ms tick) and lands in QAbstractListModel updates instead of Tk
widgets.

The bridge owns no backend work. Producers are untouched.
"""

from __future__ import annotations

import queue

from PySide6.QtCore import (
    Property,
    QObject,
    QTimer,
    Signal,
    Slot,
)

from .models import ActivityLogModel, HistoryListModel, TaskListModel
from .models.history_proxy import HistoryFilterProxy

TICK_MS = 16  # ~60 UI updates/second ceiling
BATCH_LIMIT = 250  # mirrors UI_QUEUE_BATCH_LIMIT


class PresentationBridge(QObject):
    """Consumes the existing ui_queue protocol and feeds presentation models."""

    # Presentation signals for the rare, non-list events (typed payloads;
    # QML never sees raw tuples).
    runtimeUpdateDone = Signal()
    browserSessionReady = Signal(dict)
    browserNeeded = Signal(str, str)
    browserSessionError = Signal(str)
    # The bridge cannot read persisted history itself; the app layer serves
    # entries into HistoryListModel when this fires (mirrors history_refresh).
    historyRefreshRequested = Signal()
    taskCountChanged = Signal()
    logLineCountChanged = Signal()
    logTextChanged = Signal()
    historyCountChanged = Signal()
    historyFilteredCountChanged = Signal()

    def __init__(self, ui_queue: "queue.Queue", *, log_capacity: int = 1000,
                 parent=None):
        super().__init__(parent)
        self._queue = ui_queue
        self._history_refresh = False
        self.tasks = TaskListModel(self)
        self.history = HistoryListModel(self)
        self.log = ActivityLogModel(capacity=log_capacity, parent=self)
        self._history_proxy = HistoryFilterProxy(self.history, parent=self)
        self._history_proxy.filteredCountChanged.connect(
            self.historyFilteredCountChanged
        )

        self.tasks.rowsInserted.connect(self.taskCountChanged)
        self.tasks.rowsRemoved.connect(self.taskCountChanged)
        self.tasks.modelReset.connect(self.taskCountChanged)
        self.tasks.dataChanged.connect(self.taskCountChanged)
        self.tasks.layoutChanged.connect(self.taskCountChanged)
        self.log.rowsInserted.connect(self.logLineCountChanged)
        self.log.rowsRemoved.connect(self.logLineCountChanged)
        self.log.modelReset.connect(self.logLineCountChanged)
        self.log.textChanged.connect(self.logTextChanged)
        self.history.rowsInserted.connect(self.historyCountChanged)
        self.history.rowsRemoved.connect(self.historyCountChanged)
        self.history.modelReset.connect(self.historyCountChanged)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._drain)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._timer.start()

    def shutdown(self) -> bool:
        self._timer.stop()
        return not self._timer.isActive()

    @Property(int, notify=taskCountChanged)
    def taskCount(self) -> int:
        return self.tasks.rowCount()

    @Property(int, notify=taskCountChanged)
    def queuedCount(self) -> int:
        c = 0
        for i in range(self.tasks.rowCount()):
            st = str(self.tasks.index(i).data(self.tasks.StatusRole) or "").lower()
            if st in ("queued", "waiting", "pending"):
                c += 1
        return c

    @Property(int, notify=taskCountChanged)
    def activeCount(self) -> int:
        c = 0
        for i in range(self.tasks.rowCount()):
            st = str(self.tasks.index(i).data(self.tasks.StatusRole) or "").lower()
            if st in ("downloading", "running", "active", "processing"):
                c += 1
        return c

    @Property(int, notify=taskCountChanged)
    def completedCount(self) -> int:
        c = 0
        for i in range(self.tasks.rowCount()):
            st = str(self.tasks.index(i).data(self.tasks.StatusRole) or "").lower()
            if st in ("completed", "done", "finished"):
                c += 1
        return c

    @Property(int, notify=historyCountChanged)
    def historyCount(self) -> int:
        return self.history.rowCount()

    @Property(int, notify=logLineCountChanged)
    def logLineCount(self) -> int:
        return self.log.rowCount()

    @Property(QObject, constant=True)
    def tasksModel(self) -> TaskListModel:
        return self.tasks

    @Property(QObject, constant=True)
    def historyModel(self) -> HistoryListModel:
        return self.history

    @Property(QObject, constant=True)
    def historyFiltered(self) -> HistoryFilterProxy:
        return self._history_proxy

    @Property(int, notify=historyFilteredCountChanged)
    def historyFilteredCount(self) -> int:
        return self._history_proxy.rowCount()

    @Property(QObject, constant=True)
    def logModel(self) -> ActivityLogModel:
        return self.log

    @Property(str, notify=logTextChanged)
    def logPlainText(self) -> str:
        return self.log.get_plain_text()

    @Slot()
    def clearLog(self) -> None:
        self.log.clear()

    @Slot("QVariantMap")
    def seed_task(self, info: dict) -> None:
        """Insert a restored durable task into the presentation model."""
        task_id = str(info.get("taskId", ""))
        if not task_id:
            return
        self.tasks.upsert(
            task_id,
            title=str(info.get("title", "")),
            status=str(info.get("status", "queued")),
            progress=float(info.get("progress", 0.0)),
            stage=str(info.get("stage", "Waiting")),
            speed=str(info.get("speed", "")),
            eta=str(info.get("eta", "")),
            error=str(info.get("error", "")),
            output_path=str(info.get("outputPath", "")),
            url=str(info.get("url", "")),
            mode=str(info.get("mode", "")),
        )

    @Slot(str)
    def filterHistory(self, text: str) -> None:
        self._history_proxy.setFilter(text)

    # ------------------------------------------------------------------
    # Consumption
    # ------------------------------------------------------------------

    def _drain(self) -> None:
        """Drain one bounded batch and apply latest-wins state once."""
        latest_title: dict[str, str] = {}
        latest_progress: dict[str, float] = {}
        latest_status: dict[str, str] = {}
        latest_metrics: dict[str, tuple] = {}
        log_batch: list[str] = []
        self._history_refresh = False
        processed = 0

        try:
            while processed < BATCH_LIMIT:
                message = self._queue.get_nowait()
                processed += 1
                try:
                    self._handle(
                        message, latest_title, latest_progress,
                        latest_status, latest_metrics, log_batch,
                    )
                except Exception as exc:
                    log_batch.append(
                        f"WARNING: Malformed UI queue event ignored ({exc})."
                    )
                finally:
                    self._queue.task_done()
        except queue.Empty:
            pass

        # Latest-wins apply order mirrors process_ui_queue.
        for task_id, title in latest_title.items():
            self.tasks.upsert(task_id, title=title)
        for task_id, progress in latest_progress.items():
            self.tasks.upsert(task_id, progress=float(progress))
        for task_id, metrics in latest_metrics.items():
            self.tasks.upsert(task_id, stage=metrics[0], speed=metrics[1],
                              eta=metrics[2])
        for task_id, status in latest_status.items():
            st_lower = str(status).lower()
            if st_lower == "completed":
                updates = {"status": "completed", "stage": "Completed", "speed": "", "eta": "", "error": ""}
                if task_id not in latest_progress:
                    updates["progress"] = 1.0
                self.tasks.upsert(task_id, **updates)
            elif st_lower == "canceled":
                self.tasks.upsert(task_id, status="canceled", stage="Canceled", speed="", eta="")
            elif st_lower == "error":
                self.tasks.upsert(task_id, status="error", stage="Error", speed="", eta="")
            else:
                self.tasks.upsert(task_id, status=str(status))
        if self._history_refresh:
            self.historyRefreshRequested.emit()
        if log_batch:
            self.log.append_messages(log_batch)

    def _handle(self, message, latest_title, latest_progress, latest_status,
                 latest_metrics, log_batch) -> None:
        if not isinstance(message, tuple) or not message:
            raise TypeError("event is not a non-empty tuple")
        kind = message[0]

        if kind == "log":
            log_batch.append(str(message[1]))
        elif kind == "task_title":
            latest_title[str(message[1])] = str(message[2])
        elif kind == "task_progress":
            latest_progress[str(message[1])] = float(message[2])
        elif kind == "task_status":
            latest_status[str(message[1])] = str(message[2])
        elif kind == "task_metrics":
            latest_metrics[str(message[1])] = (str(message[2]), str(message[3]),
                                                 str(message[4]))
        elif kind == "task_created":
            # Presentation-only identity for a newly visible task: (task_created, id, url, mode)
            tid = str(message[1]) if len(message) > 1 else ""
            if tid:
                self.tasks.upsert(tid, url=str(message[2]) if len(message) > 2 else "",
                                  mode=str(message[3]) if len(message) > 3 else "")
        elif kind == "history_refresh":
            self._history_refresh = True
        elif kind == "runtime_done":
            self.runtimeUpdateDone.emit()
        elif kind == "browser_session_ready":
            self.browserSessionReady.emit(dict(message[1]))
        elif kind == "browser_needed":
            self.browserNeeded.emit(str(message[1]), str(message[2]))
        elif kind == "browser_session_error":
            self.browserSessionError.emit(str(message[1]))
        else:
            # Unknown events are reported, never fatal, and never re-queued.
            log_batch.append(f"WARNING: Ignored unknown UI queue event: {kind!r}")
