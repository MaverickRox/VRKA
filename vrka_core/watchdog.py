"""Non-blocking subprocess output and phase-aware meaningful-activity watchdog."""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TextIO

from .candidates import DownloadState
from .ownership import terminate_process_tree


_PERCENT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)%")


class ActivityPhase(str, Enum):
    DIRECT_EXTRACTION = "direct_extraction"
    TRANSFER = "transfer"
    POST_PROCESSING = "post_processing"


@dataclass(frozen=True)
class WatchdogPolicy:
    direct_timeout: float = 45.0
    transfer_timeout: float = 120.0
    postprocess_timeout: float = 300.0
    poll_interval: float = 0.2

    def __post_init__(self) -> None:
        for name, value in (
            ("direct_timeout", self.direct_timeout),
            ("transfer_timeout", self.transfer_timeout),
            ("postprocess_timeout", self.postprocess_timeout),
            ("poll_interval", self.poll_interval),
        ):
            if float(value) <= 0:
                raise ValueError(f"{name} must be positive")


class ProcessCancelled(RuntimeError):
    pass


class ProcessInactivity(TimeoutError):
    def __init__(self, phase: ActivityPhase, seconds: float, *,
                 eligible_for_fallback: bool):
        self.phase = phase
        self.seconds = float(seconds)
        self.eligible_for_fallback = bool(eligible_for_fallback)
        super().__init__(
            f"No meaningful {phase.value.replace('_', ' ')} activity for "
            f"{self.seconds:.1f} seconds"
        )


class DirectPathEligibleForFallback(Exception):
    """A fast direct-path failure that protected-browser fallback can recover.

    Raised by the app when the direct extraction attempt ended in a
    browser-recoverable error category (e.g. a Cloudflare challenge, a
    cookie wall, or an extractor-level failure that followed a
    browser-relevant first error).  ``category`` carries the app's error
    taxonomy label for logging only.
    """

    def __init__(self, message: str, *, category: str = "unknown"):
        super().__init__(message)
        self.category = category


class MeaningfulActivityWatchdog:
    """Tracks state-changing output rather than treating log noise as progress."""

    def __init__(self, policy: WatchdogPolicy | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 activity_probe: Callable[[], float | None] | None = None):
        self.policy = policy or WatchdogPolicy()
        self._clock = clock
        self.activity_probe = activity_probe
        self._last_probe: float | None = None
        self._last_probe_at = 0.0
        self.started_at = float(clock())
        self.last_meaningful_at = self.started_at
        self.phase = ActivityPhase.DIRECT_EXTRACTION
        self.highest_progress = -1.0
        self.observed_transfer = False

    def _refresh_from_file_probe(self, timestamp: float) -> None:
        """Treat real byte growth in the downloader's staging area as transfer
        activity.  A rate-limited HLS/CDN transfer can grow a ``.part`` file
        for many seconds without yt-dlp emitting a NEW percentage (the same
        ``0.4%`` fragment line repeats); percentage advancement alone would
        falsely kill a genuinely flowing transfer."""
        if not self.activity_probe:
            return
        if timestamp - self._last_probe_at < 1.0:
            return
        self._last_probe_at = timestamp
        try:
            current = self.activity_probe()
        except Exception:
            return
        if current is None:
            return
        try:
            grew = self._last_probe is not None and float(current) > float(self._last_probe)
        except (TypeError, ValueError):
            grew = False
        if grew:
            self.phase = ActivityPhase.TRANSFER
            self.observed_transfer = True
            self.last_meaningful_at = timestamp
        self._last_probe = float(current)

    def note_line(self, line: str, *, now: float | None = None) -> bool:
        text = line.strip()
        timestamp = float(self._clock() if now is None else now)
        if not text:
            return False

        if text.startswith(("[Merger]", "[ExtractAudio]", "[VideoConvertor]",
                            "[Metadata]", "[EmbedThumbnail]", "[EmbedSubtitle]")):
            self.phase = ActivityPhase.POST_PROCESSING
            self.last_meaningful_at = timestamp
            return True

        progress = _PERCENT_RE.search(text)
        if progress:
            value = min(max(float(progress.group(1)), 0.0), 100.0)
            self.phase = ActivityPhase.TRANSFER
            self.observed_transfer = True
            if value > self.highest_progress:
                self.highest_progress = value
                self.last_meaningful_at = timestamp
                return True
            return False

        if text.startswith("[download] Destination:"):
            self.phase = ActivityPhase.TRANSFER
            self.observed_transfer = True
            self.last_meaningful_at = timestamp
            return True

        if text.startswith(("__VRKA_TITLE__", "__VRKA_OUTPUT__")):
            # The app's own before_dl/after_move markers mean the transfer was
            # validated to have started; later silence is a transfer concern
            # (transfer_timeout), never a fallback-eligible direct stall.
            self.phase = ActivityPhase.TRANSFER
            self.observed_transfer = True
            self.last_meaningful_at = timestamp
            return True

        if text.startswith("[info] Available formats"):
            self.last_meaningful_at = timestamp
            return True

        if " time=" in text and re.search(r"time=\d+:\d{2}", text):
            # ffmpeg downloader/transcoder progress: HLS streams downloaded
            # through ffmpeg emit no ``[download]`` progress lines, so the
            # ffmpeg ``time=`` clock is the only transfer-progress evidence.
            if self.phase == ActivityPhase.DIRECT_EXTRACTION:
                self.phase = ActivityPhase.TRANSFER
                self.observed_transfer = True
            self.last_meaningful_at = timestamp
            return True

        return False

    def check(self, *, now: float | None = None) -> None:
        timestamp = float(self._clock() if now is None else now)
        self._refresh_from_file_probe(timestamp)
        timeout = {
            ActivityPhase.DIRECT_EXTRACTION: self.policy.direct_timeout,
            ActivityPhase.TRANSFER: self.policy.transfer_timeout,
            ActivityPhase.POST_PROCESSING: self.policy.postprocess_timeout,
        }[self.phase]
        inactive = timestamp - self.last_meaningful_at
        if inactive >= timeout:
            raise ProcessInactivity(
                self.phase, inactive,
                eligible_for_fallback=(
                    self.phase == ActivityPhase.DIRECT_EXTRACTION
                    and not self.observed_transfer
                ),
            )


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    output_tail: tuple[str, ...]


class MonitoredProcessRunner:
    """Reads child output off-thread so silence cannot block cancellation/watchdog checks."""

    _EOF = object()

    def __init__(self, policy: WatchdogPolicy | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 terminate: Callable[[object], None] | None = None,
                 tail_lines: int = 200,
                 activity_probe: Callable[[], float | None] | None = None):
        if not 16 <= tail_lines <= 5000:
            raise ValueError("Output tail bound must be between 16 and 5000")
        self.policy = policy or WatchdogPolicy()
        self._clock = clock
        self._terminate = terminate or terminate_process_tree
        self.tail_lines = tail_lines
        self.activity_probe = activity_probe

    def run(self, process, *, cancel_event: threading.Event,
            register_process: Callable[[object], Callable[[], None]] | None = None,
            on_line: Callable[[str], None] | None = None) -> ProcessResult:
        stdout: TextIO | None = process.stdout
        if stdout is None:
            raise ValueError("Monitored process must expose stdout")
        watchdog = MeaningfulActivityWatchdog(
            self.policy, clock=self._clock, activity_probe=self.activity_probe,
        )
        lines: queue.Queue[object] = queue.Queue()
        tail: deque[str] = deque(maxlen=self.tail_lines)
        release = register_process(process) if register_process else (lambda: None)

        def read_output() -> None:
            try:
                for raw_line in stdout:
                    lines.put(raw_line)
            except (OSError, ValueError):
                # The parent may close the pipe after a watchdog cancellation.
                pass
            finally:
                lines.put(self._EOF)

        reader = threading.Thread(
            target=read_output, name=f"vrka-output-{getattr(process, 'pid', 'child')}",
            daemon=True,
        )
        reader.start()
        saw_eof = False
        try:
            while True:
                if cancel_event.is_set():
                    self._terminate_safely(process)
                    raise ProcessCancelled("Task cancellation requested")
                try:
                    item = lines.get(timeout=self.policy.poll_interval)
                except queue.Empty:
                    item = None
                if item is self._EOF:
                    saw_eof = True
                elif isinstance(item, str):
                    line = item.rstrip("\r\n")
                    if line:
                        tail.append(line)
                        watchdog.note_line(line)
                        if on_line:
                            on_line(line)
                if process.poll() is not None and (saw_eof or lines.empty()):
                    break
                try:
                    watchdog.check()
                except ProcessInactivity:
                    self._terminate_safely(process)
                    raise
            returncode = int(process.wait())
            return ProcessResult(returncode, tuple(tail))
        finally:
            release()
            try:
                stdout.close()
            except (OSError, ValueError):
                pass
            reader.join(timeout=0.5)

    def _terminate_safely(self, process) -> None:
        try:
            if process.poll() is None:
                self._terminate(process)
            if process.poll() is None:
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    @staticmethod
    def _terminate_process(process) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
class AutomaticFallbackExecutor:
    """Routes an eligible direct stall/failure through Browser Fallback on the same task."""

    def __init__(self, direct_attempt: Callable, browser_fallback: Callable, *,
                 enabled: Callable[[object], bool] | None = None):
        self._direct_attempt = direct_attempt
        self._browser_fallback = browser_fallback
        self._enabled = enabled or self._option_enabled

    def __call__(self, record, context) -> None:
        try:
            self._direct_attempt(record, context)
        except ProcessInactivity as stalled:
            if not stalled.eligible_for_fallback or not self._enabled(record):
                raise
            self._start_fallback(
                record, context,
                log_message="Direct extraction was inactive; continuing this task in Browser Fallback.",
                transition_message="Direct extraction inactive; Browser Fallback eligible",
            )
        except DirectPathEligibleForFallback as eligible:
            if not self._enabled(record):
                raise
            self._start_fallback(
                record, context,
                log_message=(f"Direct extraction failed ({eligible.category}); "
                             "continuing this task in Browser Fallback."),
                transition_message="Direct extraction failed; Browser Fallback eligible",
            )

    def _start_fallback(self, record, context, *, log_message: str,
                        transition_message: str) -> None:
        context.log(log_message)
        context.transition(
            DownloadState.DIRECT_FAILED_ELIGIBLE_FOR_FALLBACK,
            message=transition_message,
        )
        context.transition(
            DownloadState.BROWSER_STARTING,
            message="Browser Fallback started for the same task",
        )
        self._browser_fallback(record, context)

    @staticmethod
    def _option_enabled(record) -> bool:
        return bool(record.spec.options.get("browser_fallback_enabled", True))
