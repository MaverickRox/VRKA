"""Authoritative persistent task model for the VRKA 4.0.0 engine."""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .candidates import DownloadState, DownloadStateMachine, TERMINAL_STATES


VALID_TASK_MODES = frozenset({"video", "audio", "custom"})
_TRANSIENT_OPTION_KEYS = frozenset({
    "_staging_dir", "resolved_media_url", "resolved_media_headers",
    "resolved_media_title", "session_media_candidates",
})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return copy.deepcopy(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class TaskSpec:
    """Immutable submission data; every retry and fallback reuses this object."""

    task_id: str
    url: str
    mode: str
    options: Mapping[str, Any] = field(default_factory=dict, repr=False)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        uuid.UUID(str(self.task_id))
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("Task URL must use HTTP or HTTPS")
        if self.mode not in VALID_TASK_MODES:
            raise ValueError(f"Unsupported task mode: {self.mode}")
        clean_options = {
            str(key): value for key, value in dict(self.options).items()
            if str(key) not in _TRANSIENT_OPTION_KEYS
        }
        object.__setattr__(self, "options", _freeze(clean_options))

    @classmethod
    def create(cls, url: str, mode: str, options: Mapping[str, Any], *,
               task_id: str | None = None, now: float | None = None) -> "TaskSpec":
        return cls(
            task_id=task_id or str(uuid.uuid4()),
            url=str(url).strip(),
            mode=mode,
            options=options,
            created_at=float(now if now is not None else time.time()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "url": self.url,
            "mode": self.mode,
            "options": _thaw(self.options),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskSpec":
        return cls(
            task_id=str(data["task_id"]),
            url=str(data["url"]),
            mode=str(data["mode"]),
            options=dict(data.get("options") or {}),
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclass
class TaskRecord:
    """Mutable runtime state for one immutable TaskSpec and one Queue identity."""

    spec: TaskSpec
    machine: DownloadStateMachine
    title: str = ""
    progress: float = 0.0
    output_path: str = ""
    error: str = ""
    speed: str = ""
    eta: str = ""
    updated_at: float = field(default_factory=time.time)
    cancellation_requested: bool = False
    terminal_event_emitted: bool = False
    recovery_count: int = 0

    @classmethod
    def pending(cls, spec: TaskSpec) -> "TaskRecord":
        return cls(spec=spec, machine=DownloadStateMachine(spec.task_id))

    @property
    def task_id(self) -> str:
        return self.spec.task_id

    @property
    def state(self) -> DownloadState:
        return self.machine.state

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def active(self) -> bool:
        return self.state != DownloadState.QUEUED and not self.terminal

    def transition(self, target: DownloadState, *, now: float | None = None) -> int:
        sequence = self.machine.transition(target)
        self.updated_at = float(now if now is not None else time.time())
        return sequence

    def request_cancel(self, *, now: float | None = None) -> bool:
        if self.terminal or self.cancellation_requested:
            return False
        self.cancellation_requested = True
        self.updated_at = float(now if now is not None else time.time())
        return True

    def consume_terminal_event(self) -> bool:
        if not self.terminal or self.terminal_event_emitted:
            return False
        self.terminal_event_emitted = True
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "state": self.state.value,
            "sequence": self.machine.sequence,
            "attempts": self.machine.attempts,
            "title": self.title,
            "progress": min(max(float(self.progress), 0.0), 1.0),
            "output_path": self.output_path,
            "error": self.error,
            "speed": self.speed,
            "eta": self.eta,
            "updated_at": self.updated_at,
            "cancellation_requested": self.cancellation_requested,
            "terminal_event_emitted": self.terminal_event_emitted,
            "recovery_count": self.recovery_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskRecord":
        spec = TaskSpec.from_dict(dict(data["spec"]))
        machine = DownloadStateMachine(
            spec.task_id,
            DownloadState(str(data.get("state", DownloadState.QUEUED.value))),
            int(data.get("sequence", 0)),
            int(data.get("attempts", 0)),
        )
        return cls(
            spec=spec,
            machine=machine,
            title=str(data.get("title") or ""),
            progress=float(data.get("progress") or 0.0),
            output_path=str(data.get("output_path") or ""),
            error=str(data.get("error") or ""),
            speed=str(data.get("speed") or ""),
            eta=str(data.get("eta") or ""),
            updated_at=float(data.get("updated_at") or time.time()),
            cancellation_requested=bool(data.get("cancellation_requested")),
            terminal_event_emitted=bool(data.get("terminal_event_emitted")),
            recovery_count=max(0, int(data.get("recovery_count", 0))),
        )
