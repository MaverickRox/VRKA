import subprocess
import sys
import threading
import time
import unittest

from vrka_core.watchdog import (
    ActivityPhase,
    AutomaticFallbackExecutor,
    MeaningfulActivityWatchdog,
    MonitoredProcessRunner,
    ProcessCancelled,
    ProcessInactivity,
    WatchdogPolicy,
)


class WatchdogTests(unittest.TestCase):
    def test_log_noise_does_not_mask_direct_extraction_inactivity(self):
        now = [0.0]
        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=10, transfer_timeout=20,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: now[0],
        )
        now[0] = 7
        self.assertFalse(watchdog.note_line("[debug] heartbeat", now=now[0]))
        now[0] = 10
        with self.assertRaises(ProcessInactivity) as raised:
            watchdog.check(now=now[0])
        self.assertTrue(raised.exception.eligible_for_fallback)
        self.assertEqual(raised.exception.phase, ActivityPhase.DIRECT_EXTRACTION)

    def test_only_advancing_progress_resets_transfer_activity(self):
        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=5, transfer_timeout=10,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: 0,
        )
        self.assertTrue(watchdog.note_line("[download] 10.0% at 1MiB/s", now=2))
        self.assertFalse(watchdog.note_line("[download] 10.0% at 1MiB/s", now=8))
        with self.assertRaises(ProcessInactivity) as raised:
            watchdog.check(now=12)
        self.assertFalse(raised.exception.eligible_for_fallback)
        self.assertEqual(raised.exception.phase, ActivityPhase.TRANSFER)

    def test_postprocessing_has_its_own_timeout_and_never_triggers_fallback(self):
        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=5, transfer_timeout=10,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: 0,
        )
        self.assertTrue(watchdog.note_line("[Merger] Merging formats", now=4))
        watchdog.check(now=33.9)
        with self.assertRaises(ProcessInactivity) as raised:
            watchdog.check(now=34)
        self.assertEqual(raised.exception.phase, ActivityPhase.POST_PROCESSING)
        self.assertFalse(raised.exception.eligible_for_fallback)

    def test_silent_live_child_is_terminated_without_blocking_on_stdout(self):
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        runner = MonitoredProcessRunner(
            WatchdogPolicy(direct_timeout=0.2, transfer_timeout=1,
                           postprocess_timeout=1, poll_interval=0.02),
            terminate=lambda child: child.terminate(),
        )
        started = time.monotonic()
        with self.assertRaises(ProcessInactivity) as raised:
            runner.run(process, cancel_event=threading.Event())
        process.wait(timeout=2)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(raised.exception.eligible_for_fallback)
        self.assertIsNotNone(process.poll())

    def test_cancellation_interrupts_a_silent_child_promptly(self):
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        cancel_event = threading.Event()
        timer = threading.Timer(0.1, cancel_event.set)
        timer.start()
        runner = MonitoredProcessRunner(
            WatchdogPolicy(direct_timeout=10, transfer_timeout=10,
                           postprocess_timeout=10, poll_interval=0.02),
            terminate=lambda child: child.terminate(),
        )
        started = time.monotonic()
        try:
            with self.assertRaises(ProcessCancelled):
                runner.run(process, cancel_event=cancel_event)
        finally:
            timer.cancel()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
        self.assertLess(time.monotonic() - started, 2)


    def test_advancing_progress_prevents_a_false_transfer_timeout(self):
        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=5, transfer_timeout=10,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: 0,
        )
        self.assertTrue(watchdog.note_line("[download] 1.0%", now=2))
        self.assertTrue(watchdog.note_line("[download] 2.0%", now=11))
        watchdog.check(now=20.9)
        with self.assertRaises(ProcessInactivity) as raised:
            watchdog.check(now=21)
        self.assertEqual(raised.exception.phase, ActivityPhase.TRANSFER)
        self.assertFalse(raised.exception.eligible_for_fallback)

    def test_before_dl_marker_marks_transfer_started_and_never_fallback_eligible(self):
        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=5, transfer_timeout=10,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: 0,
        )
        self.assertTrue(watchdog.note_line("__VRKA_TITLE__Some video", now=2))
        with self.assertRaises(ProcessInactivity) as raised:
            watchdog.check(now=12)
        self.assertEqual(raised.exception.phase, ActivityPhase.TRANSFER)
        self.assertFalse(raised.exception.eligible_for_fallback)

    def test_ffmpeg_progress_lines_keep_an_hls_transfer_alive(self):
        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=5, transfer_timeout=10,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: 0,
        )
        self.assertTrue(watchdog.note_line("__VRKA_TITLE__Live stream", now=1))
        self.assertTrue(watchdog.note_line(
            "frame= 1080 fps= 36 q=-1.0 size= 2816KiB time=00:00:36.01 "
            "bitrate= 640.6kbits/s speed= 1.2x",
            now=11,
        ))
        watchdog.check(now=20.9)  # inside transfer_timeout since the ffmpeg line
        with self.assertRaises(ProcessInactivity) as raised:
            watchdog.check(now=21)
        self.assertEqual(raised.exception.phase, ActivityPhase.TRANSFER)
        self.assertFalse(raised.exception.eligible_for_fallback)

    def test_staging_byte_growth_keeps_a_slow_hls_transfer_alive(self):
        """The real rate-limited JAV.GURU case: percentage stays at 0.4% while
        the staging ``.part`` file grows for many seconds.  Byte growth is
        meaningful transfer activity; the watchdog must not declare a stall."""
        now = [0.0]
        bytes_now = [0.0]

        def probe():
            return bytes_now[0]

        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=5, transfer_timeout=10,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: now[0], activity_probe=probe,
        )
        self.assertTrue(watchdog.note_line(
            "[download]   0.4% of ~   4.00GiB at   20.00KiB/s ETA 50:00:00 (frag 4/958)",
            now=1,
        ))
        for seconds, size in ((5, 5_000_000), (12, 12_000_000), (18, 18_000_000)):
            now[0] = float(seconds)
            bytes_now[0] = float(size)
            watchdog.check(now=now[0])  # byte growth refreshes activity
        self.assertGreaterEqual(watchdog.last_meaningful_at, 18)

    def test_stagnant_bytes_still_stall_a_transfer(self):
        """A genuine stall: the percentage never advances AND the staging bytes
        never grow (including stale bytes left from an earlier attempt, which
        must not count - only GROWTH is activity)."""
        now = [0.0]
        bytes_now = [18_000_000.0]  # stale large file, never grows

        def probe():
            return bytes_now[0]

        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=5, transfer_timeout=10,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: now[0], activity_probe=probe,
        )
        self.assertTrue(watchdog.note_line(
            "[download]   0.4% of ~   4.00GiB at      0.00B/s ETA Unknown (frag 4/958)",
            now=1,
        ))
        now[0] = 5
        watchdog.check(now=now[0])
        with self.assertRaises(ProcessInactivity) as raised:
            watchdog.check(now=12)  # 11 s inactive, no byte growth
        self.assertEqual(raised.exception.phase, ActivityPhase.TRANSFER)
        self.assertFalse(raised.exception.eligible_for_fallback)

    def test_normal_percentage_progression_still_works_with_probe_present(self):
        """The file probe must not mask a missing transfer or interfere with
        ordinary percentage advancement."""
        now = [0.0]

        def probe():
            return 0.0

        watchdog = MeaningfulActivityWatchdog(
            WatchdogPolicy(direct_timeout=5, transfer_timeout=10,
                           postprocess_timeout=30, poll_interval=0.01),
            clock=lambda: now[0], activity_probe=probe,
        )
        now[0] = 2
        self.assertTrue(watchdog.note_line("[download] 1.0%", now=2))
        now[0] = 11
        self.assertTrue(watchdog.note_line("[download] 2.0%", now=11))
        watchdog.check(now=20.9)
        with self.assertRaises(ProcessInactivity) as raised:
            watchdog.check(now=21)
        self.assertEqual(raised.exception.phase, ActivityPhase.TRANSFER)
        self.assertFalse(raised.exception.eligible_for_fallback)

    def test_eligible_stall_uses_one_task_and_preserves_fifo_and_options(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from vrka_core import DownloadState, TaskScheduler, TaskSpec, TaskStore

        first_id = "00000000-0000-4000-8000-000000000201"
        second_id = "00000000-0000-4000-8000-000000000202"
        order = []
        fallback_started = threading.Event()
        release_fallback = threading.Event()

        def direct(record, context):
            order.append(("direct", record.task_id))
            if record.task_id == first_id:
                raise ProcessInactivity(
                    ActivityPhase.DIRECT_EXTRACTION, 45,
                    eligible_for_fallback=True,
                )
            context.transition(DownloadState.DOWNLOAD_RUNNING)
            context.progress(0.5)

        def browser(record, context):
            order.append(("browser", record.task_id))
            self.assertEqual(record.spec.options["quality"], "1080p")
            self.assertTrue(record.spec.options["browser_fallback_enabled"])
            fallback_started.set()
            self.assertTrue(release_fallback.wait(2))
            context.transition(DownloadState.BROWSER_WAITING_FOR_MEDIA)
            context.transition(DownloadState.HANDOFF_PREPARING)
            context.transition(DownloadState.HANDOFF_VALIDATING)
            context.transition(DownloadState.DOWNLOADER_RESUMED)
            context.transition(DownloadState.DOWNLOAD_RUNNING)
            context.progress(0.5)

        executor = AutomaticFallbackExecutor(direct, browser)
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(TaskStore(Path(directory) / "tasks.json"), executor)
            try:
                first = TaskSpec.create(
                    "https://example.test/watch/first", "video",
                    {"quality": "1080p", "browser_fallback_enabled": True},
                    task_id=first_id,
                )
                second = TaskSpec.create(
                    "https://example.test/watch/second", "video", {},
                    task_id=second_id,
                )
                scheduler.submit(first)
                self.assertTrue(fallback_started.wait(2))
                scheduler.submit(second)
                self.assertEqual(scheduler.active_task_id, first_id)
                self.assertEqual(scheduler.get(second_id).state, DownloadState.QUEUED)
                release_fallback.set()
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(scheduler.get(first_id).state, DownloadState.COMPLETED)
                self.assertEqual(scheduler.get(second_id).state, DownloadState.COMPLETED)
                self.assertEqual(
                    order,
                    [("direct", first_id), ("browser", first_id), ("direct", second_id)],
                )
                added = [event.task_id for event in scheduler.events.snapshot()
                         if event.kind == "task_added"]
                self.assertEqual(added, [first_id, second_id])
            finally:
                release_fallback.set()
                self.assertTrue(scheduler.shutdown())
    def test_disabled_fallback_leaves_an_eligible_stall_as_one_failed_task(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from vrka_core import DownloadState, TaskScheduler, TaskSpec, TaskStore

        task_id = "00000000-0000-4000-8000-000000000203"
        browser_calls = []

        def direct(_record, _context):
            raise ProcessInactivity(
                ActivityPhase.DIRECT_EXTRACTION, 45,
                eligible_for_fallback=True,
            )

        executor = AutomaticFallbackExecutor(
            direct, lambda *_: browser_calls.append("unexpected"),
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(TaskStore(Path(directory) / "tasks.json"), executor)
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/disabled", "video",
                    {"browser_fallback_enabled": False}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_state(task_id, DownloadState.FAILED, 2))
                self.assertEqual(browser_calls, [])
                self.assertIn("No meaningful", scheduler.get(task_id).error)
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_runner_registers_and_releases_the_owned_process_once(self):
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        registered = []
        released = []

        def register(child):
            registered.append(child.pid)
            return lambda: released.append(child.pid)

        runner = MonitoredProcessRunner(
            WatchdogPolicy(direct_timeout=0.2, transfer_timeout=1,
                           postprocess_timeout=1, poll_interval=0.02),
            terminate=lambda child: child.terminate(),
        )
        try:
            with self.assertRaises(ProcessInactivity):
                runner.run(process, cancel_event=threading.Event(),
                           register_process=register)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
        self.assertEqual(registered, [process.pid])
        self.assertEqual(released, [process.pid])
if __name__ == "__main__":
    unittest.main()
