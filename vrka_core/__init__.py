"""Production domain core for VRKA 4.0.0."""

from .browser_capture import MediaBodyCapture
from .browser_fallback import (
    BrowserContextCancelled,
    BrowserFallbackCancelled,
    BrowserFallbackError,
    ExternalReplayRejected,
    BrowserSelectionRequired,
    JsonFileBrowserEpisode,
    ProtectedBrowserFallback,
    SubprocessBrowserLauncher,
)
from .candidates import (
    CandidateKind,
    CandidateLifecycle,
    CandidateRanker,
    CandidateStore,
    DownloadState,
    HandoffBundle,
    is_master_manifest,
)
from .events import CoreEvent, EventBus
from .media_assembly import assemble as assemble_browser_capture
from .media_assembly import classify as classify_capture_entry
from .ownership import OwnedProcessRegistry
from .persistence import TaskStore, TaskStoreError
from .scheduler import TaskCancelled, TaskExecutionContext, TaskScheduler
from .tasks import TaskRecord, TaskSpec
from .ui_adapter import Build008TaskAdapter
from .watchdog import (
    ActivityPhase,
    AutomaticFallbackExecutor,
    DirectPathEligibleForFallback,
    MeaningfulActivityWatchdog,
    MonitoredProcessRunner,
    ProcessCancelled,
    ProcessInactivity,
    ProcessResult,
    WatchdogPolicy,
)

__all__ = [
    "ActivityPhase", "AutomaticFallbackExecutor", "BrowserContextCancelled",
    "BrowserFallbackCancelled",
    "BrowserFallbackError", "BrowserSelectionRequired", "Build008TaskAdapter", "CandidateKind",
    "ClassifyCaptureEntry", "ExternalReplayRejected", "MediaBodyCapture",
    "CandidateLifecycle", "CandidateRanker", "CandidateStore", "CoreEvent",
    "DirectPathEligibleForFallback", "DownloadState", "EventBus", "HandoffBundle",
    "JsonFileBrowserEpisode", "MeaningfulActivityWatchdog", "MonitoredProcessRunner",
    "OwnedProcessRegistry", "ProcessCancelled", "ProcessInactivity", "ProcessResult",
    "ProtectedBrowserFallback", "SubprocessBrowserLauncher", "TaskCancelled",
    "TaskExecutionContext", "TaskRecord", "TaskScheduler", "TaskSpec", "TaskStore",
    "TaskStoreError", "WatchdogPolicy", "assemble_browser_capture",
    "classify_capture_entry",
]
