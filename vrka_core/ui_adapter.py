"""Thin build008-facing adapter for the UI-neutral build010 core."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .candidates import DownloadState
from .events import CoreEvent, EventBus
from .persistence import TaskStore
from .scheduler import TaskExecutionContext, TaskScheduler
from .tasks import TaskRecord, TaskSpec


_ACTIVE_STATES = frozenset({
    state for state in DownloadState
    if state not in {DownloadState.QUEUED, DownloadState.COMPLETED,
                     DownloadState.FAILED, DownloadState.CANCELLED}
})


class Build008TaskAdapter:
    """Maps core events to the existing build008 queue tuple protocol.

    The adapter owns no widgets and does not create a second queue. ``visible``
    is called only after durable submission succeeds; all other callbacks are
    expected to be lightweight and thread-safe (the existing UI queue is one).
    """

    def __init__(self, store_path: str | Path,
                 task_resolver: Callable[[TaskRecord], Any],
                 execute_task: Callable[[Any, TaskExecutionContext], None],
                 ui_queue: queue.Queue,
                 *, visible: Callable[[Any], None] | None = None,
                 history: Callable[[Any], None] | None = None,
                 events: EventBus | None = None,
                 auto_start: bool = True):
        self.ui_queue = ui_queue
        self.task_resolver = task_resolver
        self.execute_task = execute_task
        self.visible = visible
        self.history = history
        self.events = events or EventBus()
        self._tasks: dict[str, Any] = {}
        self._history_emitted: set[str] = set()
        self._lock = threading.RLock()
        self.scheduler = TaskScheduler(
            TaskStore(store_path), self._execute, events=self.events,
            auto_start=False,
        )
        # Completed durable tasks have already produced their one build008
        # History entry; a later Retry keeps the same logical identity.
        self._history_emitted.update(
            record.task_id for record in self.scheduler.records()
            if record.state == DownloadState.COMPLETED
        )
        self._unsubscribe = self.events.subscribe(self._on_event)
        if auto_start:
            self.scheduler.start()

    def restore_existing(self) -> tuple[Any, ...]:
        """Materialize durable records in the existing build008 Queue UI once."""
        restored: list[Any] = []
        for record in self.scheduler.records():
            with self._lock:
                task = self._tasks.get(record.task_id)
            if task is None:
                task = self.task_resolver(record)
                with self._lock:
                    self._tasks[record.task_id] = task
            restored.append(task)
            if self.visible:
                self.visible(task)
        return tuple(restored)
    def submit(self, task: Any) -> TaskRecord:
        spec = TaskSpec.create(
            task.url, task.mode, task.options, task_id=str(task.id),
        )
        record = self.scheduler.submit(spec)
        with self._lock:
            self._tasks[record.task_id] = task
        if self.visible:
            self.visible(task)
        return record

    def remove(self, task_id: str) -> bool:
        """User-requested Queue removal: drop the durable record as well."""
        dropped = bool(self.scheduler.forget(task_id))
        with self._lock:
            self._tasks.pop(task_id, None)
        return dropped

    def clear_finished(self) -> int:
        """Drop every terminal durable record in one save (Clear Completed)."""
        terminal = tuple(
            record.task_id for record in self.scheduler.records()
            if record.terminal
        )
        if not terminal:
            return 0
        dropped = self.scheduler.forget(*terminal)
        with self._lock:
            for task_id in dropped:
                self._tasks.pop(task_id, None)
        return len(dropped)

    def cancel(self, task_id: str) -> bool:
        return self.scheduler.cancel(task_id)

    def retry(self, task_id: str) -> bool:
        """Preserve the build008 Retry affordance on the same logical task."""
        if not self.scheduler.retry(task_id):
            return False
        with self._lock:
            task = self._tasks.get(task_id)
        if task is not None:
            task.status = "queued"
            task.progress = 0.0
            task.error = ""
            task.process = None
            task.stage = "Waiting"
            task.speed = ""
            task.eta = ""
        self.ui_queue.put(("log", f"Retry queued: {getattr(task, 'url', task_id)}"))
        return True
    def shutdown(self, *, timeout: float = 5.0) -> bool:
        self._unsubscribe()
        return self.scheduler.shutdown(timeout=timeout, cancel_active=True)

    def _execute(self, record: TaskRecord, context: TaskExecutionContext) -> None:
        with self._lock:
            task = self._tasks.get(record.task_id)
        if task is None:
            task = self.task_resolver(record)
            with self._lock:
                self._tasks[record.task_id] = task
        task._core_context = context
        task._core_record = record
        self.execute_task(task, context)

    def _on_event(self, event: CoreEvent) -> None:
        with self._lock:
            task = self._tasks.get(event.task_id)
        if event.kind == "log":
            self.ui_queue.put(("log", event.message))
            return
        if task is None:
            return
        if event.kind == "task_progress":
            data = event.data
            task.progress = float(data.get("progress", task.progress))
            if data.get("title"):
                task.title = str(data["title"])
            if data.get("output_path"):
                task.output_path = str(data["output_path"])
            task.speed = str(data.get("speed") or task.speed)
            task.eta = str(data.get("eta") or task.eta)
            self.ui_queue.put(("task_progress", task.id, task.progress))
            self.ui_queue.put(("task_metrics", task.id, task.stage, task.speed, task.eta))
            return
        if event.kind != "task_state":
            if event.kind == "browser_protection_stats":
                self.ui_queue.put(("log", "Protected browser activity was filtered safely."))
            return
        state = DownloadState(str(event.data.get("state")))
        if state == DownloadState.COMPLETED:
            task.status = "completed"
            task.progress = 1.0
            task.stage = "Completed"
            task.speed = ""
            task.eta = ""
            self.ui_queue.put(("task_progress", task.id, 1.0))
            self.ui_queue.put(("task_metrics", task.id, "Completed", "", ""))
            self.ui_queue.put(("task_status", task.id, "completed"))
            self._emit_history_once(task)
        elif state == DownloadState.CANCELLED:
            task.status = "canceled"
            self.ui_queue.put(("task_status", task.id, "canceled"))
        elif state == DownloadState.FAILED:
            task.status = "error"
            task.error = task.error or event.message
            self.ui_queue.put(("task_status", task.id, "error"))
        elif state in _ACTIVE_STATES:
            task.status = "downloading"
            task.stage = self._stage_for(state)
            self.ui_queue.put(("task_status", task.id, "downloading"))
            self.ui_queue.put(("task_metrics", task.id, task.stage, task.speed, task.eta))
        elif state == DownloadState.QUEUED:
            task.status = "queued"
            self.ui_queue.put(("task_status", task.id, "queued"))

    def _emit_history_once(self, task: Any) -> None:
        with self._lock:
            if task.id in self._history_emitted:
                return
            self._history_emitted.add(task.id)
        if self.history:
            self.history(task)
        self.ui_queue.put(("history_refresh", None))

    @staticmethod
    def _stage_for(state: DownloadState) -> str:
        if state in {DownloadState.BROWSER_STARTING, DownloadState.BROWSER_WAITING_FOR_MEDIA,
                     DownloadState.BROWSER_INTERACTION_REQUIRED,
                     DownloadState.BROWSER_STABILIZING_CANDIDATES,
                     DownloadState.CANDIDATE_SELECTION_REQUIRED}:
            return "Browser Fallback"
        if state in {DownloadState.HANDOFF_PREPARING, DownloadState.HANDOFF_VALIDATING,
                     DownloadState.FALLBACK_RECOVERING}:
            return "Validating handoff"
        if state in {DownloadState.DOWNLOADER_RESUMED, DownloadState.DOWNLOAD_RUNNING}:
            return "Downloading"
        if state == DownloadState.POST_PROCESSING:
            return "Finalizing"
        return "Starting"
