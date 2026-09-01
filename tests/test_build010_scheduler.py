import tempfile
import threading
import unittest
from pathlib import Path

from vrka_core import (
    DownloadState,
    EventBus,
    OwnedProcessRegistry,
    TaskRecord,
    TaskScheduler,
    TaskSpec,
    TaskStore,
)


TASK_A = "00000000-0000-4000-8000-000000000101"
TASK_B = "00000000-0000-4000-8000-000000000102"


def spec(task_id, suffix):
    return TaskSpec.create(
        f"https://example.test/watch/{suffix}", "video", {"quality": "best"},
        task_id=task_id,
    )


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.running = True

    def poll(self):
        return None if self.running else 0


class PersistentSchedulerTests(unittest.TestCase):
    def test_active_cancel_releases_worker_and_second_fifo_task_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.json")
            events = EventBus()
            order = []
            first_started = threading.Event()
            second_started = threading.Event()

            def execute(record, context):
                order.append(record.task_id)
                context.transition(DownloadState.DOWNLOAD_RUNNING)
                if record.task_id == TASK_A:
                    first_started.set()
                    context.cancel_event.wait(3)
                    context.check_cancelled()
                else:
                    second_started.set()
                    context.progress(0.5, title="second")

            scheduler = TaskScheduler(store, execute, events=events)
            try:
                scheduler.submit(spec(TASK_A, "a"))
                scheduler.submit(spec(TASK_B, "b"))
                self.assertTrue(first_started.wait(2), "first task never started")
                self.assertEqual(scheduler.active_task_id, TASK_A)
                self.assertTrue(scheduler.cancel(TASK_A))
                self.assertFalse(scheduler.cancel(TASK_A))
                self.assertTrue(second_started.wait(2), "pending task did not start")
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(order, [TASK_A, TASK_B])
                self.assertEqual(scheduler.get(TASK_A).state, DownloadState.CANCELLED)
                self.assertEqual(scheduler.get(TASK_B).state, DownloadState.COMPLETED)
                cancelled_events = [
                    event for event in events.snapshot()
                    if event.task_id == TASK_A
                    and event.data.get("state") == DownloadState.CANCELLED.value
                ]
                self.assertEqual(len(cancelled_events), 1)
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_pending_tasks_survive_restart_in_original_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            scheduler = TaskScheduler(TaskStore(path), lambda *_: None, auto_start=False)
            scheduler.submit(spec(TASK_A, "a"))
            scheduler.submit(spec(TASK_B, "b"))
            self.assertTrue(scheduler.shutdown())

            recovered = TaskScheduler(TaskStore(path), lambda *_: None, auto_start=False)
            try:
                self.assertEqual(
                    [record.task_id for record in recovered.records()],
                    [TASK_A, TASK_B],
                )
                self.assertTrue(all(
                    record.state == DownloadState.QUEUED for record in recovered.records()
                ))
            finally:
                self.assertTrue(recovered.shutdown())

    def test_interrupted_active_task_recovers_as_same_pending_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.json")
            interrupted = TaskRecord.pending(spec(TASK_A, "a"))
            interrupted.transition(DownloadState.DIRECT_ATTEMPT)
            interrupted.transition(DownloadState.DOWNLOAD_RUNNING)
            interrupted.progress = 0.4
            store.save([interrupted])

            recovered = store.load(recover=True)
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0].task_id, TASK_A)
            self.assertIs(recovered[0].spec, recovered[0].spec)
            self.assertEqual(recovered[0].state, DownloadState.QUEUED)
            self.assertEqual(recovered[0].recovery_count, 1)
            self.assertEqual(recovered[0].progress, 0.0)

    def test_cancel_requested_before_crash_is_never_restarted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.json")
            interrupted = TaskRecord.pending(spec(TASK_A, "a"))
            interrupted.transition(DownloadState.DIRECT_ATTEMPT)
            interrupted.request_cancel()
            store.save([interrupted])

            recovered = store.load(recover=True)
            self.assertEqual(recovered[0].state, DownloadState.CANCELLED)
            scheduler = TaskScheduler(store, lambda *_: self.fail("cancelled task restarted"),
                                      auto_start=False)
            try:
                self.assertIsNone(scheduler.active_task_id)
                self.assertEqual(scheduler.get(TASK_A).state, DownloadState.CANCELLED)
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_executor_failure_in_browser_phase_does_not_strand_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            def execute(record, context):
                context.transition(DownloadState.DIRECT_FAILED_ELIGIBLE_FOR_FALLBACK)
                context.transition(DownloadState.BROWSER_STARTING)
                context.transition(DownloadState.BROWSER_WAITING_FOR_MEDIA)
                raise RuntimeError("fixture failure")

            scheduler = TaskScheduler(TaskStore(Path(directory) / "tasks.json"), execute)
            try:
                scheduler.submit(spec(TASK_A, "a"))
                self.assertTrue(scheduler.wait_for_state(TASK_A, DownloadState.FAILED, 2))
                self.assertEqual(scheduler.get(TASK_A).error, "fixture failure")
                self.assertTrue(scheduler.wait_for_idle(2))
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_store_replaces_atomically_without_orphaned_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            store = TaskStore(path)
            store.save([TaskRecord.pending(spec(TASK_A, "a"))])
            store.save([
                TaskRecord.pending(spec(TASK_A, "a")),
                TaskRecord.pending(spec(TASK_B, "b")),
            ])
            self.assertEqual(len(store.load()), 2)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_owned_processes_and_cleanup_are_task_scoped_and_idempotent(self):
        terminated = []
        cleaned = []
        registry = OwnedProcessRegistry(tree_terminator=lambda proc: terminated.append(proc.pid))
        process_a = FakeProcess(101)
        process_b = FakeProcess(102)
        registry.begin(TASK_A)
        registry.begin(TASK_B)
        registry.register_process(TASK_A, process_a)
        registry.register_process(TASK_B, process_b)
        registry.register_cleanup(TASK_A, lambda: cleaned.append("a"))
        registry.register_cleanup(TASK_B, lambda: cleaned.append("b"))

        self.assertTrue(registry.cancel(TASK_A))
        self.assertFalse(registry.cancel(TASK_A))
        self.assertEqual(terminated, [101])
        self.assertEqual(cleaned, ["a"])
        self.assertEqual(registry.owned_process_count(TASK_B), 1)
        registry.finish(TASK_B)
        self.assertEqual(cleaned, ["a", "b"])
        self.assertEqual(terminated, [101])

    def test_progress_updates_throttle_durable_saves_but_deliver_every_event(self):
        # Regression: progress lines must not fsync the whole task file on
        # every call; state transitions still persist immediately.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            store = TaskStore(path)
            saves = []
            original_save = store.save

            def counting_save(records):
                saves.append(len(saves))
                return original_save(records)

            store.save = counting_save
            events = EventBus()
            seen_progress = []

            def execute(record, context):
                context.transition(DownloadState.DOWNLOAD_RUNNING)
                for step in range(25):
                    context.progress(step / 30.0, speed="1MiB/s", eta="00:10")
                    seen_progress.append(step)

            scheduler = TaskScheduler(store, execute, events=events)
            try:
                scheduler.submit(spec(TASK_A, "throttle"))
                self.assertTrue(scheduler.wait_for_idle(5))
                self.assertEqual(seen_progress, list(range(25)))
                self.assertEqual(scheduler.get(TASK_A).state, DownloadState.COMPLETED)
                self.assertLessEqual(
                    len(saves), 6,
                    f"too many durable saves for 25 progress updates: {len(saves)}",
                )
                reloaded = store.load(recover=True)
                self.assertEqual(reloaded[0].progress, 1.0)
            finally:
                scheduler.shutdown()


if __name__ == "__main__":
    unittest.main()
