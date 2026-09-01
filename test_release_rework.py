import io
import queue
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import vrka_downloader as app_module


class _FinishedProcess:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(lines))
        self.returncode = 0
        self.pid = 4242

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode


class ReleaseReworkTests(unittest.TestCase):
    def test_queue_worker_waits_for_and_processes_a_wakeup(self):
        app = object.__new__(app_module.VRKADownloader)
        app.tasks = []
        app.tasks_lock = threading.Lock()
        app._shutdown_event = threading.Event()
        app._queue_wakeup = threading.Event()
        processed = threading.Event()

        def process_task(task):
            task.status = "completed"
            processed.set()

        app.process_task = process_task
        worker = threading.Thread(target=app.queue_worker, daemon=True)
        worker.start()
        task = app_module.DownloadTask(
            "wake", "https://example.test/media", "video", {}
        )
        with app.tasks_lock:
            app.tasks.append(task)
        app._queue_wakeup.set()
        self.assertTrue(processed.wait(0.5))
        app._shutdown_event.set()
        app._queue_wakeup.set()
        worker.join(0.5)
        self.assertFalse(worker.is_alive())

    def test_retry_resets_task_and_wakes_worker(self):
        app = object.__new__(app_module.VRKADownloader)
        task = app_module.DownloadTask(
            "retry", "https://example.test/media", "video", {},
            status="canceled", progress=0.7, error="stopped",
        )
        app.tasks = [task]
        app.tasks_lock = threading.Lock()
        app.cancel_events = {"retry": threading.Event()}
        app.cancel_events["retry"].set()
        app.ui_queue = queue.Queue()
        app._queue_wakeup = threading.Event()
        app._core_adapter = None

        app.retry_task("retry")

        self.assertEqual(task.status, "queued")
        self.assertEqual(task.progress, 0.0)
        self.assertEqual(task.error, "")
        self.assertFalse(app.cancel_events["retry"].is_set())
        self.assertTrue(app._queue_wakeup.is_set())

    def test_cancel_terminates_the_tracked_process_tree(self):
        app = object.__new__(app_module.VRKADownloader)
        process = mock.Mock()
        task = app_module.DownloadTask(
            "cancel", "https://example.test/media", "video", {},
            status="downloading", process=process,
        )
        app.tasks = [task]
        app.tasks_lock = threading.Lock()
        app.cancel_events = {"cancel": threading.Event()}
        app._terminate_process_tree = mock.Mock()
        app._core_adapter = None

        app.cancel_task("cancel")

        self.assertTrue(app.cancel_events["cancel"].is_set())
        app._terminate_process_tree.assert_called_once_with(process)

    def test_progress_and_log_events_are_bounded(self):
        lines = [
            f"[download] {index}.0% of 10.00MiB at 2.00MiB/s ETA 00:10\n"
            for index in range(1, 31)
        ]
        process = _FinishedProcess(lines)
        app = object.__new__(app_module.VRKADownloader)
        app.ui_queue = queue.Queue()
        app._terminate_process_tree = mock.Mock()
        task = app_module.DownloadTask(
            "progress", "https://example.test/media", "video", {}
        )
        backend = SimpleNamespace(version="test", source="bundled")

        with mock.patch.object(app_module.subprocess, "Popen", return_value=process):
            app._execute_ytdlp_command(
                task, ["yt-dlp", task.url], threading.Event(), backend
            )

        messages = []
        while not app.ui_queue.empty():
            messages.append(app.ui_queue.get_nowait())
        progress = [message for message in messages if message[0] == "task_progress"]
        progress_logs = [
            message for message in messages
            if message[0] == "log" and "[download]" in message[1]
        ]
        self.assertLessEqual(len(progress), 2)
        self.assertLessEqual(len(progress_logs), 2)
        self.assertEqual(task.speed, "2.00MiB/s")
        self.assertEqual(task.eta, "00:10")

    def test_before_dl_title_marker_signals_validated_transfer_start(self):
        """HLS downloads through the ffmpeg-merge path emit no ``[download]``
        progress lines; the ``before_dl`` __VRKA_TITLE__ marker is the only
        transfer-start evidence and must signal the handoff so the deadline
        does not kill a healthy download."""
        process = _FinishedProcess([
            "__VRKA_TITLE__Some Movie 2026\n",
            "[Merger] Merging formats into \"out.mp4\"\n",
        ])
        app = object.__new__(app_module.VRKADownloader)
        app.ui_queue = queue.Queue()
        app._terminate_process_tree = mock.Mock()
        task = app_module.DownloadTask(
            "hls", "https://example.test/watch", "video", {}
        )
        started = threading.Event()
        task._handoff_transfer_started = started
        backend = SimpleNamespace(version="test", source="bundled")

        with mock.patch.object(app_module.subprocess, "Popen", return_value=process):
            app._execute_ytdlp_command(
                task, ["yt-dlp", task.url], threading.Event(), backend
            )

        self.assertTrue(started.is_set())
        self.assertEqual(task.title, "Some Movie 2026")

    def test_before_dl_marker_alone_does_not_signal_transfer_flow(self):
        """A before_dl marker or ``[download] Destination:`` proves the
        downloader was invoked, NOT that bytes are flowing.  The flow signal
        must stay unset so the protected browser is not closed before
        sustained transfer activity."""
        process = _FinishedProcess([
            "__VRKA_TITLE__Some Movie 2026\n",
            "[download] Destination: C:/out/some.mp4\n",
            "[Merger] Merging formats into \"out.mp4\"\n",
        ])
        app = object.__new__(app_module.VRKADownloader)
        app.ui_queue = queue.Queue()
        app._terminate_process_tree = mock.Mock()
        task = app_module.DownloadTask(
            "flow1", "https://example.test/watch", "video", {}
        )
        started = threading.Event()
        flow = threading.Event()
        task._handoff_transfer_started = started
        task._handoff_transfer_flow = flow
        backend = SimpleNamespace(version="test", source="bundled")

        with mock.patch.object(app_module.subprocess, "Popen", return_value=process):
            app._execute_ytdlp_command(
                task, ["yt-dlp", task.url], threading.Event(), backend
            )

        self.assertTrue(started.is_set())
        self.assertFalse(flow.is_set())

    def test_percentage_progress_and_ffmpeg_time_signal_transfer_flow(self):
        """Real percentage progress (ordinary downloads) and ffmpeg ``time=``
        progression (HLS/ffmpeg-merged downloads with no ``[download]``
        percentage lines) both count as sustained transfer activity."""
        app = object.__new__(app_module.VRKADownloader)
        app.ui_queue = queue.Queue()
        app._terminate_process_tree = mock.Mock()
        backend = SimpleNamespace(version="test", source="bundled")

        percent = _FinishedProcess([
            "[download] Destination: C:/out/some.mp4\n",
            "[download]  12.5% of    5.00MiB at 2.00MiB/s ETA 00:02\n",
        ])
        task = app_module.DownloadTask(
            "flow2", "https://example.test/watch", "video", {}
        )
        started = threading.Event()
        flow = threading.Event()
        task._handoff_transfer_started = started
        task._handoff_transfer_flow = flow
        with mock.patch.object(app_module.subprocess, "Popen", return_value=percent):
            app._execute_ytdlp_command(
                task, ["yt-dlp", task.url], threading.Event(), backend
            )
        self.assertTrue(flow.is_set())

        ffmpeg = _FinishedProcess([
            "__VRKA_TITLE__Live Cam 2026\n",
            "[hls @ 000001] Opening '...seg0.ts' for reading\n",
            "frame=  120 fps= 30 q=28.0 size=     512KiB time=00:00:04.00 "
            "bitrate=1048.5kbits/s speed=1.0x\n",
        ])
        task2 = app_module.DownloadTask(
            "flow3", "https://example.test/watch", "video", {}
        )
        started2 = threading.Event()
        flow2 = threading.Event()
        task2._handoff_transfer_started = started2
        task2._handoff_transfer_flow = flow2
        with mock.patch.object(app_module.subprocess, "Popen", return_value=ffmpeg):
            app._execute_ytdlp_command(
                task2, ["yt-dlp", task2.url], threading.Event(), backend
            )
        self.assertTrue(flow2.is_set())

    def _handoff_app(self):
        app = object.__new__(app_module.VRKADownloader)
        app._terminate_process_tree = mock.Mock()
        task = app_module.DownloadTask(
            "handoff", "https://example.test/watch", "video", {
                "output_folder": "C:/out",
                "browser_fallback_enabled": True,
            },
        )
        context = SimpleNamespace(
            cancel_event=threading.Event(),
            check_cancelled=lambda: None,
            log=lambda _message: None,
        )
        app._validate_media_candidate = mock.Mock(return_value=True)
        bundle = SimpleNamespace(
            media_url="https://cdn.example.test/main.m3u8",
            headers={}, user_agent="", referer="", origin="", cookies=None,
            expected_content_types=("application/vnd.apple.mpegurl",),
        )
        return app, task, context, bundle

    def test_handoff_waits_for_sustained_flow_before_committing(self):
        """The browser may close only after sustained transfer activity; a
        transfer that starts (before_dl) and then shows real progress commits."""
        app, task, context, bundle = self._handoff_app()

        def run_transfer(task_, output_folder_, cancel_event_):
            task._handoff_transfer_started.set()
            time.sleep(0.2)
            task._handoff_transfer_flow.set()

        app._run_standard_task = run_transfer
        self.assertTrue(
            app._resume_protected_browser_transfer(task, "C:/out", bundle, context)
        )
        self.assertTrue(task._handoff_transfer_flow.is_set())
        task._handoff_transfer[1].wait()
        self.assertTrue(task._handoff_transfer[2].is_set())

    def test_handoff_no_flow_terminates_and_recovers_without_commit(self):
        """Transfer start without sustained activity must NOT close the
        browser; the candidate is terminated (task-owned) and the fallback
        tries the next candidate on the same task."""
        app, task, context, bundle = self._handoff_app()
        task.process = mock.Mock()

        def run_transfer(task_, output_folder_, cancel_event_):
            task._handoff_transfer_started.set()
            time.sleep(30.0)

        app._run_standard_task = run_transfer
        with mock.patch.object(app_module, "TRANSFER_FLOW_GRACE_SECONDS", 0.4):
            ok = app._resume_protected_browser_transfer(
                task, "C:/out", bundle, context
            )
        self.assertFalse(ok)
        app._terminate_process_tree.assert_called_once_with(task.process)
        self.assertNotIn("resolved_media_url", task.options)
        self.assertFalse(task._handoff_transfer_flow.is_set())

    def test_handoff_fast_successful_transfer_commits(self):
        """A transfer that finishes successfully before flow is observed is a
        completed download; the browser closes (post-success)."""
        app, task, context, bundle = self._handoff_app()

        def run_transfer(task_, output_folder_, cancel_event_):
            task._handoff_transfer_started.set()
            time.sleep(0.1)

        app._run_standard_task = run_transfer
        self.assertTrue(
            app._resume_protected_browser_transfer(task, "C:/out", bundle, context)
        )

    def test_browser_observed_media_external_replay_rejected(self):
        """Browser-observed media whose external replay the server rejects
        must classify as a TRANSFER limitation (browser-accessible but
        externally non-transferable) instead of candidate decay: the
        protected browser fetched the exact resource with HTTP 200, the
        probe replay was overridden by that observation credit, and the
        external transfer was then refused with a context-bound category."""
        app, task, context, bundle = self._handoff_app()
        bundle.observed_status = 200
        app._validate_media_candidate = mock.Mock(return_value=False)
        task._last_probe_category = "http"

        def run_transfer(task_, output_folder_, cancel_event_):
            task._handoff_transfer_started.set()
            raise app_module.YTDLPCommandError(
                "HTTP Error 403: Forbidden", category="http",
                output="HTTP Error 403: Forbidden",
            )

        app._run_standard_task = run_transfer
        with self.assertRaises(app_module.ExternalReplayRejected):
            app._resume_protected_browser_transfer(task, "C:/out", bundle, context)

    def test_browser_observed_media_replay_rejected_before_transfer_start(self):
        """A 403 during the initial playlist fetch kills yt-dlp BEFORE any
        before_dl marker, so the failure lands on the never-started exit.
        A browser-credited candidate (protected browser fetched it with
        HTTP 200, probe override applied) must classify as external-replay
        rejected there too - not decay into a generic validation failure.
        Reproduces the packaged Anikoto terminal path."""
        app, task, context, bundle = self._handoff_app()
        bundle.observed_status = 200
        app._validate_media_candidate = mock.Mock(return_value=False)
        task._last_probe_category = "http"

        def run_transfer(task_, output_folder_, cancel_event_):
            # Deliberately never sets task._handoff_transfer_started: the
            # replay 403 happens before any transfer-start marker.
            raise app_module.YTDLPCommandError(
                "HTTP Error 403: Forbidden", category="http",
                output="HTTP Error 403: Forbidden",
            )

        app._run_standard_task = run_transfer
        with self.assertRaises(app_module.ExternalReplayRejected):
            app._resume_protected_browser_transfer(task, "C:/out", bundle, context)

    def test_without_browser_credit_no_external_replay_classification(self):
        """Without confirmed browser-fetch credit the override never fires:
        a failed probe stays a plain validation failure (False), so the
        classic candidate-recovery path is preserved."""
        app, task, context, bundle = self._handoff_app()
        bundle.observed_status = 0
        app._validate_media_candidate = mock.Mock(return_value=False)
        task._last_probe_category = "http"
        app._run_standard_task = mock.Mock()

        self.assertFalse(
            app._resume_protected_browser_transfer(task, "C:/out", bundle, context)
        )
        app._run_standard_task.assert_not_called()

    def test_ui_queue_coalesces_latest_task_state(self):
        app = object.__new__(app_module.VRKADownloader)
        app._closing = False
        app._current_page = "Queue"
        app.ui_queue = queue.Queue()
        app._update_task_progress = mock.Mock()
        app._update_task_status = mock.Mock()
        app._update_task_title = mock.Mock()
        app._update_task_metrics = mock.Mock()
        app._append_log_batch = mock.Mock()
        app._refresh_stats = mock.Mock()
        app.after = mock.Mock(return_value="after-id")
        for value in (0.1, 0.4, 0.9):
            app.ui_queue.put(("task_progress", "task", value))
        app.ui_queue.put(("task_title", "task", "first"))
        app.ui_queue.put(("task_title", "task", "latest"))

        app.process_ui_queue()

        app._update_task_progress.assert_called_once_with("task", 0.9)
        app._update_task_title.assert_called_once_with("task", "latest")

    def test_history_is_lazy_and_first_page_is_bounded(self):
        history = [
            {
                "id": str(index),
                "title": f"Item {index}",
                "url": f"https://example.test/{index}",
                "path": f"C:/Downloads/item-{index}.mp4",
                "mode": "video",
                "timestamp": "2026-07-29 12:00",
            }
            for index in range(250)
        ]
        with mock.patch.object(
            app_module.VRKADownloader, "load_history", return_value=history
        ):
            app = app_module.VRKADownloader()
            try:
                self.assertEqual(
                    len(app.history_list_frame.winfo_children()), 0
                )
                app.show_page("History")
                app.update_idletasks()
                self.assertLessEqual(
                    len(app.history_list_frame.winfo_children()),
                    app_module.HISTORY_PAGE_SIZE + 1,
                )
            finally:
                app._on_close()

    def test_output_controls_reject_escape_paths(self):
        self.assertRaises(
            ValueError,
            app_module.validate_output_template,
            "../outside/%(title)s.%(ext)s",
        )
        self.assertRaises(
            ValueError,
            app_module.validate_custom_ytdlp_arguments,
            ["--exec", "cmd.exe"],
        )

    def test_ytdlp_runtime_floor_meets_current_youtube_requirements(self):
        """The pinned yt-dlp runtime must not regress behind the SABR era.

        YouTube killed ``android_vr`` adaptive-stream URLs (yt-dlp #17456):
        stable 2026.07.04 and older resolve formats whose CDN downloads fail
        mid-transfer with HTTP 403.  stable 2026.08.19 switched the default
        extraction client and downloads complete again.  The bundled pin is
        the floor; managed runtimes update through VRKA's verified updater.
        """
        import re as _re

        requirements = (
            Path(__file__).parent / "requirements.txt"
        ).read_text(encoding="utf-8")
        match = _re.search(
            r"yt-dlp\[default\]==(\d+)\.(\d+)\.(\d+)", requirements,
        )
        self.assertIsNotNone(match, "yt-dlp pin missing from requirements.txt")
        version = tuple(int(part) for part in match.groups())
        self.assertGreaterEqual(
            version, (2026, 8, 19),
            "pinned yt-dlp predates the YouTube android_vr/SABR transfer-403 fix",
        )

    def test_direct_path_stays_generic_with_no_player_client_overrides(self):
        """Client-selection fixes must come from yt-dlp runtime updates, never
        from hardcoded extractor arguments in VRKA's command construction."""
        source = Path(app_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("player_client", source)
        self.assertNotIn("player-client", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
