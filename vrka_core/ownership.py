"""Task-scoped ownership for processes, browser handles, and cleanup callbacks."""

from __future__ import annotations

import os
import subprocess
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Protocol


class ProcessLike(Protocol):
    pid: int

    def poll(self): ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None): ...


def terminate_process_tree(process: ProcessLike) -> None:
    """Terminate only the still-running process tree represented by this handle."""
    if process.poll() is not None:
        return
    pid = int(process.pid)
    if pid <= 0:
        raise ValueError("Refusing to terminate an invalid process ID")
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


@dataclass
class _OwnedResources:
    processes: list[ProcessLike] = field(default_factory=list)
    cancel_callbacks: list[Callable[[], None]] = field(default_factory=list)
    cleanup_callbacks: list[Callable[[], None]] = field(default_factory=list)
    cancelled: bool = False


class OwnedProcessRegistry:
    """Owns resources by logical task ID and releases each registration once."""

    def __init__(self, *, tree_terminator: Callable[[ProcessLike], None] = terminate_process_tree):
        self._tree_terminator = tree_terminator
        self._resources: dict[str, _OwnedResources] = defaultdict(_OwnedResources)
        self._lock = threading.RLock()

    def begin(self, task_id: str) -> None:
        with self._lock:
            if task_id in self._resources and self._has_live_entries(self._resources[task_id]):
                raise RuntimeError(f"Task {task_id} still owns resources")
            self._resources[task_id] = _OwnedResources()

    def register_process(self, task_id: str, process: ProcessLike) -> Callable[[], None]:
        with self._lock:
            owned = self._resources[task_id]
            if owned.cancelled:
                terminate_now = True
            else:
                owned.processes.append(process)
                terminate_now = False
        if terminate_now:
            self._tree_terminator(process)

        def release() -> None:
            with self._lock:
                current = self._resources.get(task_id)
                if current and process in current.processes:
                    current.processes.remove(process)

        return release

    def register_cancel_callback(self, task_id: str, callback: Callable[[], None]) -> None:
        with self._lock:
            owned = self._resources[task_id]
            if owned.cancelled:
                run_now = True
            else:
                owned.cancel_callbacks.append(callback)
                run_now = False
        if run_now:
            callback()

    def register_cleanup(self, task_id: str, callback: Callable[[], None]) -> None:
        with self._lock:
            owned = self._resources[task_id]
            if owned.cancelled:
                run_now = True
            else:
                owned.cleanup_callbacks.append(callback)
                run_now = False
        if run_now:
            callback()

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            owned = self._resources.get(task_id)
            if owned is None or owned.cancelled:
                return False
            owned.cancelled = True
            processes = tuple(owned.processes)
            callbacks = tuple(owned.cancel_callbacks)
            cleanups = tuple(owned.cleanup_callbacks)
            owned.processes.clear()
            owned.cancel_callbacks.clear()
            owned.cleanup_callbacks.clear()
        for callback in callbacks:
            self._call_safely(callback)
        for process in processes:
            try:
                self._tree_terminator(process)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        for callback in cleanups:
            self._call_safely(callback)
        return True

    def finish(self, task_id: str) -> None:
        with self._lock:
            owned = self._resources.pop(task_id, None)
            if owned is None or owned.cancelled:
                return
            cleanups = tuple(owned.cleanup_callbacks)
        for callback in cleanups:
            self._call_safely(callback)

    def owned_process_count(self, task_id: str) -> int:
        with self._lock:
            owned = self._resources.get(task_id)
            return len(owned.processes) if owned else 0

    @staticmethod
    def _call_safely(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            pass

    @staticmethod
    def _has_live_entries(owned: _OwnedResources) -> bool:
        return bool(owned.processes or owned.cancel_callbacks or owned.cleanup_callbacks)
