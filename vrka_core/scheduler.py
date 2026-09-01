"""Persistent single-worker FIFO scheduler for the VRKA 4.0.0 engine."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .candidates import DownloadState
from .events import CoreEvent, EventBus
from .ownership import OwnedProcessRegistry, ProcessLike
from .persistence import TaskStore
from .tasks import TaskRecord, TaskSpec


class TaskCancelled(RuntimeError):
    pass


class TaskExecutionContext:
    """The only mutation/ownership boundary exposed to a task executor."""

    def __init__(self, scheduler: "TaskScheduler", task_id: str,
                 cancel_event: threading.Event):
        self._scheduler = scheduler
        self.task_id = task_id
        self.cancel_event = cancel_event

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set() or self._scheduler.is_cancel_requested(self.task_id):
            raise TaskCancelled(self.task_id)

    def transition(self, state: DownloadState, *, message: str = "") -> None:
        self.check_cancelled()
        self._scheduler.transition(self.task_id, state, message=message)

    def progress(self, value: float, *, title: str | None = None,
                 output_path: str | None = None, speed: str | None = None,
                 eta: str | None = None) -> None:
        self.check_cancelled()
        self._scheduler.update_progress(
            self.task_id, value, title=title, output_path=output_path,
            speed=speed, eta=eta,
        )

    def log(self, message: str) -> None:
        self._scheduler.events.emit(CoreEvent("log", self.task_id, message=message))

    def emit(self, kind: str, *, message: str = "", data: dict[str, Any] | None = None) -> None:
        self._scheduler.events.emit(CoreEvent(
            kind, self.task_id, message=message, data=data or {},
        ))

    def own_process(self, process: ProcessLike) -> Callable[[], None]:
        return self._scheduler.ownership.register_process(self.task_id, process)

    def on_cancel(self, callback: Callable[[], None]) -> None:
        self._scheduler.ownership.register_cancel_callback(self.task_id, callback)

    def on_cleanup(self, callback: Callable[[], None]) -> None:
        self._scheduler.ownership.register_cleanup(self.task_id, callback)


TaskExecutor = Callable[[TaskRecord, TaskExecutionContext], Any]

# Progress lines can arrive many times per second; persisting the whole task
# file on each one hammers the disk while holding the scheduler lock.  State
# transitions always save immediately; only cosmetic progress values throttle.
PROGRESS_SAVE_INTERVAL_SECONDS = 1.0


class TaskScheduler:
    """Durable strict-FIFO scheduler with one active logical task."""

    def __init__(self, store: TaskStore, executor: TaskExecutor, *,
                 events: EventBus | None = None,
                 ownership: OwnedProcessRegistry | None = None,
                 auto_start: bool = True):
        self.store = store
        self.executor = executor
        self.events = events or EventBus()
        self.ownership = ownership or OwnedProcessRegistry()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._records = store.load(recover=True)
        self._by_id = {record.task_id: record for record in self._records}
        self._pending = deque(
            record.task_id for record in self._records
            if record.state == DownloadState.QUEUED and not record.cancellation_requested
        )
        self._cancel_events: dict[str, threading.Event] = {}
        self._progress_saves: dict[str, float] = {}
        self._active_id: str | None = None
        self._accepting = True
        self._stopping = False
        self._started = False
        self._thread = threading.Thread(
            target=self._worker, name="vrka-fifo-worker", daemon=False,
        )
        if auto_start:
            self.start()

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            if self._stopping:
                raise RuntimeError("Cannot restart a stopped scheduler")
            self._started = True
            self._thread.start()
            self._condition.notify_all()

    def submit(self, spec: TaskSpec) -> TaskRecord:
        record = TaskRecord.pending(spec)
        with self._condition:
            if not self._accepting:
                raise RuntimeError("Task scheduler is shutting down")
            if spec.task_id in self._by_id:
                raise ValueError(f"Duplicate logical task ID: {spec.task_id}")
            updated = [*self._records, record]
            self.store.save(updated)
            self._records.append(record)
            self._by_id[record.task_id] = record
            self._pending.append(record.task_id)
            self._condition.notify_all()
        self.events.emit(CoreEvent("task_added", record.task_id, message="Queued"))
        return record

    def cancel(self, task_id: str) -> bool:
        active = False
        terminal_event: CoreEvent | None = None
        with self._condition:
            record = self._by_id.get(task_id)
            if record is None or not record.request_cancel():
                return False
            active = self._active_id == task_id
            cancel_event = self._cancel_events.get(task_id)
            if cancel_event is not None:
                cancel_event.set()
            record.transition(DownloadState.CANCELLED)
            if record.consume_terminal_event():
                terminal_event = self._state_event(record, "Cancellation confirmed")
            self.store.save(self._records)
            self._condition.notify_all()
        if active:
            self.ownership.cancel(task_id)
        if terminal_event:
            self.events.emit(terminal_event)
        return True

    def retry(self, task_id: str) -> bool:
        """Requeue one terminal logical task without changing its immutable identity."""
        with self._condition:
            record = self._by_id.get(task_id)
            if record is None or not record.terminal:
                return False
            replacement = TaskRecord.pending(record.spec)
            index = self._records.index(record)
            self._records[index] = replacement
            self._by_id[task_id] = replacement
            self._cancel_events.pop(task_id, None)
            self._progress_saves.pop(task_id, None)
            self._pending = deque(
                current.task_id for current in self._records
                if current.state == DownloadState.QUEUED
                and not current.cancellation_requested
            )
            self.store.save(self._records)
            self._condition.notify_all()
        self.events.emit(self._state_event(replacement, "Retry queued"))
        return True
    def forget(self, *task_ids: str) -> tuple[str, ...]:
        """Drop user-deleted tasks from the durable store.

        Only terminal or still-queued records are removable; the currently
        executing task keeps its ownership chain intact. One atomic save
        covers the whole batch.
        """
        dropped: list[str] = []
        with self._condition:
            for task_id in task_ids:
                record = self._by_id.get(task_id)
                if record is None or self._active_id == task_id:
                    continue
                if not (record.terminal or record.state == DownloadState.QUEUED):
                    continue
                self._records.remove(record)
                del self._by_id[task_id]
                self._progress_saves.pop(task_id, None)
                dropped.append(task_id)
            if dropped:
                self._pending = deque(
                    current.task_id for current in self._records
                    if current.state == DownloadState.QUEUED
                    and not current.cancellation_requested
                )
                self.store.save(self._records)
                self._condition.notify_all()
        return tuple(dropped)

    def transition(self, task_id: str, state: DownloadState, *, message: str = "") -> None:
        with self._condition:
            record = self._required(task_id)
            if record.cancellation_requested or record.terminal:
                raise TaskCancelled(task_id)
            record.transition(state)
            self.store.save(self._records)
            event = self._state_event(record, message)
        self.events.emit(event)

    def update_progress(self, task_id: str, value: float, *,
                        title: str | None = None, output_path: str | None = None,
                        speed: str | None = None, eta: str | None = None) -> None:
        with self._condition:
            record = self._required(task_id)
            if record.cancellation_requested or record.terminal:
                raise TaskCancelled(task_id)
            record.progress = min(max(float(value), 0.0), 1.0)
            if title is not None:
                record.title = str(title)
            if output_path is not None:
                record.output_path = str(output_path)
            if speed is not None:
                record.speed = str(speed)
            if eta is not None:
                record.eta = str(eta)
            record.updated_at = time.time()
            now = time.monotonic()
            if now - self._progress_saves.get(task_id, 0.0) >= PROGRESS_SAVE_INTERVAL_SECONDS:
                self._progress_saves[task_id] = now
                self.store.save(self._records)
            event = CoreEvent(
                "task_progress", task_id, record.machine.sequence,
                data={"progress": record.progress, "title": record.title,
                      "output_path": record.output_path, "speed": record.speed,
                      "eta": record.eta},
            )
        self.events.emit(event)

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            record = self._by_id.get(task_id)
            return record is None or record.cancellation_requested

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._by_id.get(task_id)

    def records(self) -> tuple[TaskRecord, ...]:
        with self._lock:
            return tuple(self._records)

    @property
    def active_task_id(self) -> str | None:
        with self._lock:
            return self._active_id

    def wait_for_state(self, task_id: str, state: DownloadState,
                       timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                record = self._by_id.get(task_id)
                if record is not None and record.state == state:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, 0.05))

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._active_id is not None or any(
                record.state == DownloadState.QUEUED for record in self._records
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, 0.05))
            return True

    def shutdown(self, *, timeout: float = 5.0, cancel_active: bool = True) -> bool:
        with self._condition:
            self._accepting = False
            active_id = self._active_id
        if cancel_active and active_id:
            self.cancel(active_id)
        with self._condition:
            self._stopping = True
            self.store.save(self._records)
            self._condition.notify_all()
        if self._started and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        return not self._thread.is_alive() if self._started else True

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._stopping:
                    record = self._next_pending_locked()
                    if record is not None:
                        break
                    self._condition.wait()
                else:
                    return
                self._active_id = record.task_id
                cancel_event = threading.Event()
                self._cancel_events[record.task_id] = cancel_event
                self.ownership.begin(record.task_id)
                record.transition(DownloadState.DIRECT_ATTEMPT)
                self.store.save(self._records)
                start_event = self._state_event(record, "Direct extraction started")
                self._condition.notify_all()
            self.events.emit(start_event)
            context = TaskExecutionContext(self, record.task_id, cancel_event)
            try:
                self.executor(record, context)
                self._complete(record.task_id)
            except TaskCancelled:
                self._confirm_cancelled(record.task_id)
            except Exception as exc:
                self._fail(record.task_id, exc)
            finally:
                self.ownership.finish(record.task_id)
                with self._condition:
                    self._cancel_events.pop(record.task_id, None)
                    if self._active_id == record.task_id:
                        self._active_id = None
                    self._condition.notify_all()

    def _next_pending_locked(self) -> TaskRecord | None:
        while self._pending:
            task_id = self._pending.popleft()
            record = self._by_id.get(task_id)
            if record and record.state == DownloadState.QUEUED and not record.cancellation_requested:
                return record
        return None

    def _complete(self, task_id: str) -> None:
        event: CoreEvent | None = None
        with self._condition:
            record = self._required(task_id)
            if record.cancellation_requested or record.state == DownloadState.CANCELLED:
                return
            if not record.terminal:
                record.progress = 1.0
                record.transition(DownloadState.COMPLETED)
            if record.consume_terminal_event():
                event = self._state_event(record, "Completed")
            self.store.save(self._records)
        if event:
            self.events.emit(event)

    def _confirm_cancelled(self, task_id: str) -> None:
        event: CoreEvent | None = None
        with self._condition:
            record = self._required(task_id)
            if not record.terminal:
                record.request_cancel()
                record.transition(DownloadState.CANCELLED)
            if record.consume_terminal_event():
                event = self._state_event(record, "Cancellation confirmed")
            self.store.save(self._records)
        if event:
            self.events.emit(event)

    def _fail(self, task_id: str, exc: Exception) -> None:
        event: CoreEvent | None = None
        with self._condition:
            record = self._required(task_id)
            if record.cancellation_requested or record.state == DownloadState.CANCELLED:
                pass
            elif not record.terminal:
                record.error = str(exc)
                record.transition(DownloadState.FAILED)
            if record.consume_terminal_event():
                event = self._state_event(record, record.error or "Failed")
            self.store.save(self._records)
        if event:
            self.events.emit(event)

    def _required(self, task_id: str) -> TaskRecord:
        record = self._by_id.get(task_id)
        if record is None:
            raise KeyError(task_id)
        return record

    @staticmethod
    def _state_event(record: TaskRecord, message: str) -> CoreEvent:
        return CoreEvent(
            "task_state", record.task_id, record.machine.sequence,
            message=message, data={"state": record.state.value},
        )
