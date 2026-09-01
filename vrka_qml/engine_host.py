"""Presentation-side engine host for the QML application .

Owns the unchanged build008/build010 engine surface: the durable
``Build008TaskAdapter`` (TaskScheduler + TaskStore inside vrka_core) and the
monolith's download executor. Engine methods are delegated to
``vrka_downloader.VRKADownloader`` verbatim - download execution, yt-dlp,
FFmpeg, browser fallback and handoff code are reused, never copied or
modified. Only the Tk widget layer is absent.

Stage 4 adds history persistence (the same HISTORY_FILE the monolith uses),
restored-task presentation snapshots and a presentation-safe history removal.
"""

from __future__ import annotations

import os
import threading

import vrka_downloader as app


class EngineHost:
    def __init__(self, ui_queue, *, store_path=None):
        self.ui_queue = ui_queue
        self.tasks = []  # build008 row model shared with the core adapter
        self.tasks_lock = threading.Lock()
        self.cancel_events = {}
        self.output_folder = os.path.join(os.path.expanduser("~"), "Downloads")
        self._verified_session = {}
        self._browser_candidate_map = {}
        self._pending_browser_retry_url = ""
        self._browser_verification_process = None
        self._protected_browser_launcher = app.SubprocessBrowserLauncher(
            app.BROWSER_SESSION_DIR,
            self._protected_browser_command,
        )
        self.history: list[dict] = []
        self._core_adapter = app.Build008TaskAdapter(
            store_path or (app.APP_DATA_DIR / "tasks.json"),
            self._resolve_core_task,
            self._execute_core_task,
            ui_queue,
            visible=self._show_task,
            history=self.add_history_entry,
            auto_start=False,
        )

    def __getattr__(self, name):
        """Delegate everything else to the unchanged monolith class.

        Instance state above wins; missing names resolve against
        ``VRKADownloader`` so engine methods run with ``self`` bound to this
        host. Only names present on that class delegate; everything else
        raises AttributeError normally.
        """
        try:
            attribute = vars(app.VRKADownloader)[name]
        except KeyError:
            raise AttributeError(name) from None
        if callable(attribute):
            return attribute.__get__(self)
        return attribute

    # ------------------------------------------------------------------
    # Adapter seams (widget-free mirrors of the CTk callbacks)
    # ------------------------------------------------------------------

    def _protected_browser_command(self, record, result_path):
        return app.build_self_invocation() + [
            "__vrka_protected_browser__", record.spec.url, str(result_path),
        ]

    def _find_task(self, task_id):
        with self.tasks_lock:
            for task in self.tasks:
                if task.id == task_id:
                    return task
        return None

    def _show_task(self, task):
        """Expose a task only after the core made it durable.

        The submit-time QUEUED core event is consumed inside the adapter
        (the task is registered after ``scheduler.submit`` returns), so the
        CTk app renders the row through this callback instead. The QML
        channel for the same information is the existing ``task_status``
        tuple plus a presentation-only ``task_created`` carrying identity so
        the QML model learns url/mode without a second backend lookup.
        """
        if self._find_task(task.id) is not None:
            return
        with self.tasks_lock:
            self.tasks.append(task)
        # Status first (creates the model row), then identity (populates url/mode).
        self.ui_queue.put(("task_status", task.id, task.status))
        self.ui_queue.put(("task_created", task.id, task.url, task.mode))

    # ------------------------------------------------------------------
    # Stage 4: restored-task presentation snapshots
    # ------------------------------------------------------------------

    def restored_task_snapshots(self) -> list[dict]:
        """Plain-dict snapshots for every durable task (presentation init)."""
        with self.tasks_lock:
            return [
                dict(
                    taskId=t.id,
                    title=getattr(t, "title", ""),
                    status=getattr(t, "status", "queued"),
                    progress=float(getattr(t, "progress", 0.0)),
                    stage=getattr(t, "stage", "Waiting"),
                    speed=getattr(t, "speed", ""),
                    eta=getattr(t, "eta", ""),
                    error=getattr(t, "error", ""),
                    outputPath=getattr(t, "output_path", ""),
                    url=getattr(t, "url", ""),
                    mode=getattr(t, "mode", ""),
                )
                for t in self.tasks
            ]

    # ------------------------------------------------------------------
    # Stage 4: history persistence helpers
    # ------------------------------------------------------------------

    def remove_history_entry(self, entry_id: str) -> None:
        """Presentation-safe history removal (no Tk widgets)."""
        self.history = [h for h in self.history if h.get("id") != entry_id]
        self.save_history()
        self.ui_queue.put(("history_refresh", None))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def submit(self, task):
        return self._core_adapter.submit(task)

    def start(self):
        """Restore durable records, load history, and start the scheduler."""
        self.history = self.load_history()
        self._core_adapter.restore_existing()
        self._core_adapter.scheduler.start()

    def shutdown(self, *, timeout: float = 1.5) -> bool:
        return self._core_adapter.shutdown(timeout=timeout)
