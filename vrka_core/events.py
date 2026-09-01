"""Bounded UI-neutral events emitted by the build010 core."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class CoreEvent:
    kind: str
    task_id: str = ""
    sequence: int = 0
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict, repr=False)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


class EventBus:
    """Small thread-safe fan-out; GUI adapters marshal callbacks to their UI thread."""

    def __init__(self, max_events: int = 1000):
        if not 16 <= max_events <= 10000:
            raise ValueError("Event bound must be between 16 and 10000")
        self._events: deque[CoreEvent] = deque(maxlen=max_events)
        self._subscribers: list[Callable[[CoreEvent], None]] = []
        self._lock = threading.RLock()

    def subscribe(self, callback: Callable[[CoreEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def emit(self, event: CoreEvent) -> None:
        with self._lock:
            self._events.append(event)
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                # Presentation adapters must never be able to stop the engine worker.
                continue

    def snapshot(self) -> tuple[CoreEvent, ...]:
        with self._lock:
            return tuple(self._events)
