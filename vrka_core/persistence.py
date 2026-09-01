"""Atomic persistent storage for logical build010 tasks."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterable

from .candidates import DownloadState, DownloadStateMachine
from .tasks import TaskRecord


class TaskStoreError(RuntimeError):
    pass


class TaskStore:
    """Versioned bounded JSON store written atomically beside its destination."""

    FORMAT_VERSION = 1

    def __init__(self, path: str | os.PathLike[str], *, max_records: int = 5000):
        if not 1 <= max_records <= 50000:
            raise ValueError("Task record bound must be between 1 and 50000")
        self.path = Path(path)
        self.max_records = max_records
        self._lock = threading.RLock()

    def save(self, records: Iterable[TaskRecord]) -> None:
        snapshot = list(records)
        if len(snapshot) > self.max_records:
            raise TaskStoreError("Persistent task queue exceeded its configured bound")
        payload = {
            "format": self.FORMAT_VERSION,
            "tasks": [record.to_dict() for record in snapshot],
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_name = ""
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="\n",
                    dir=self.path.parent, prefix=f".{self.path.name}.",
                    suffix=".tmp", delete=False,
                ) as temporary:
                    temporary_name = temporary.name
                    json.dump(payload, temporary, ensure_ascii=False, separators=(",", ":"))
                    temporary.write("\n")
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, self.path)
            except (OSError, TypeError, ValueError) as exc:
                if temporary_name:
                    try:
                        Path(temporary_name).unlink(missing_ok=True)
                    except OSError:
                        pass
                raise TaskStoreError(f"Could not persist task queue: {exc}") from exc

    def load(self, *, recover: bool = False) -> list[TaskRecord]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                with self.path.open("r", encoding="utf-8") as source:
                    payload = json.load(source)
                if payload.get("format") != self.FORMAT_VERSION:
                    raise TaskStoreError("Unsupported persistent task queue format")
                raw_tasks = payload.get("tasks")
                if not isinstance(raw_tasks, list) or len(raw_tasks) > self.max_records:
                    raise TaskStoreError("Invalid or oversized persistent task queue")
                records = [TaskRecord.from_dict(item) for item in raw_tasks]
            except TaskStoreError:
                raise
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise TaskStoreError(f"Could not read persistent task queue: {exc}") from exc

            task_ids = [record.task_id for record in records]
            if len(task_ids) != len(set(task_ids)):
                raise TaskStoreError("Persistent task queue contains duplicate task IDs")
            if not recover:
                return records

            recovered: list[TaskRecord] = []
            changed = False
            for record in records:
                if record.terminal:
                    recovered.append(record)
                    continue
                if record.cancellation_requested:
                    record.transition(DownloadState.CANCELLED)
                    record.terminal_event_emitted = False
                    recovered.append(record)
                    changed = True
                    continue
                if record.state == DownloadState.QUEUED:
                    recovered.append(record)
                    continue
                replacement = TaskRecord.pending(record.spec)
                replacement.machine = DownloadStateMachine(
                    record.task_id,
                    DownloadState.QUEUED,
                    record.machine.sequence + 1,
                    record.machine.attempts,
                )
                replacement.title = record.title
                replacement.output_path = record.output_path
                replacement.updated_at = record.updated_at
                replacement.recovery_count = record.recovery_count + 1
                recovered.append(replacement)
                changed = True
            if changed:
                self.save(recovered)
            return recovered
