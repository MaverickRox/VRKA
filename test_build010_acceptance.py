"""Focused build010 startup/shutdown/adapter acceptance gates.

Covers durable startup restore (one visible Queue row and one History entry
per logical task), shutdown cancellation, adapter teardown that terminates
owned processes, and the no-duplicate Queue/History invariants around
restart and close.
"""

import queue
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from test_vrka import APP_CLASS

from vrka_core import (
    Build008TaskAdapter,
    DownloadState,
    OwnedProcessRegistry,
    TaskRecord,
    TaskScheduler,
    TaskSpec,
    TaskStore,
)


def _task(task_id, url, mode="video", options=None):
    return SimpleNamespace(
        id=task_id, url=url, mode=mode, options=options or {},
        status="queued", progress=0.0, title="", output_path="", error="",
        speed="", eta="", stage="Waiting", process=None,
    )


class Build010AcceptanceTests(unittest.TestCase):
    def test_startup_restore_processes_restored_queue_once_with_one_history_per_task(self):
        task_ids = [
            "00000000-0000-4000-8000-000000000601",
            "00000000-0000-4000-8000-000000000602",
            "00000000-0000-4000-8000-000000000603",
        ]
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            first = TaskRecord.pending(TaskSpec.create(
                "https://example.test/first", "video", {}, task_id=task_ids[0],
            ))
            second = TaskRecord.pending(TaskSpec.create(
                "https://example.test/second", "audio", {}, task_id=task_ids[1],
            ))
            third = TaskRecord.pending(TaskSpec.create(
                "https://example.test/third", "video", {}, task_id=task_ids[2],
            ))
            for state in (
                DownloadState.DIRECT_ATTEMPT,
                DownloadState.DOWNLOAD_RUNNING,
                DownloadState.COMPLETED,
            ):
                third.transition(state)
            third.consume_terminal_event()
            TaskStore(store_path).save([first, second, third])

            messages = queue.Queue()
            visible = []
            history = []
            resolved = {
                task_ids[0]: _task(task_ids[0], "https://example.test/first"),
                task_ids[1]: _task(task_ids[1], "https://example.test/second"),
                task_ids[2]: _task(task_ids[2], "https://example.test/third"),
            }

            def resolver(record):
                return resolved[record.task_id]

            def execute(current, context):
                context.transition(DownloadState.DOWNLOAD_RUNNING)
                context.progress(1.0, title=f"done-{current.id}")

            adapter = Build008TaskAdapter(
                store_path, resolver, execute, messages,
                visible=lambda task: visible.append(task.id),
                history=lambda task: history.append(task.id),
                auto_start=False,
            )
            try:
                restored = adapter.restore_existing()
                self.assertEqual([task.id for task in restored], task_ids)
                self.assertEqual(visible, task_ids)
                adapter.scheduler.start()
                self.assertTrue(adapter.scheduler.wait_for_idle(3))
                # The two restored QUEUED records each produce exactly one
                # History entry; the already-completed record must not re-emit.
                self.assertEqual(history, task_ids[:2])
                for task_id in task_ids:
                    self.assertEqual(
                        adapter.scheduler.get(task_id).state,
                        DownloadState.COMPLETED,
                    )
                reloaded = TaskStore(store_path).load(recover=True)
                self.assertEqual([record.task_id for record in reloaded], task_ids)
                self.assertTrue(
                    all(record.state == DownloadState.COMPLETED for record in reloaded)
                )
            finally:
                self.assertTrue(adapter.shutdown())

    def test_shutdown_cancels_active_task_and_cleans_ownership_without_history(self):
        task_id = "00000000-0000-4000-8000-000000000604"
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            messages = queue.Queue()
            started = threading.Event()
            cancel_ran = threading.Event()
            cleanup_ran = threading.Event()
            history = []
            task = _task(task_id, "https://example.test/shutdown")

            def execute(current, context):
                context.on_cancel(cancel_ran.set)
                context.on_cleanup(cleanup_ran.set)
                started.set()
                context.cancel_event.wait(3)
                context.check_cancelled()

            adapter = Build008TaskAdapter(
                store_path, lambda _record: task, execute, messages,
                history=lambda current: history.append(current.id),
                auto_start=True,
            )
            try:
                adapter.submit(task)
                self.assertTrue(started.wait(2))
                self.assertTrue(adapter.shutdown(timeout=2))
                self.assertEqual(
                    adapter.scheduler.get(task_id).state, DownloadState.CANCELLED
                )
                self.assertTrue(cancel_ran.is_set())
                self.assertTrue(cleanup_ran.is_set())
                self.assertEqual(history, [])
                self.assertIsNone(adapter.scheduler.active_task_id)
                self.assertEqual(
                    adapter.scheduler.ownership.owned_process_count(task_id), 0
                )
                reloaded = TaskStore(store_path).load(recover=True)
                self.assertEqual([record.task_id for record in reloaded], [task_id])
                self.assertEqual(reloaded[0].state, DownloadState.CANCELLED)
                self.assertTrue(adapter.shutdown(timeout=1))
            finally:
                adapter.shutdown(timeout=1)

    def test_shutdown_terminates_the_owned_process_tree(self):
        task_id = "00000000-0000-4000-8000-000000000605"
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            started = threading.Event()
            terminated = []
            process = SimpleNamespace(pid=4242, poll=lambda: None)
            ownership = OwnedProcessRegistry(
                tree_terminator=lambda current: terminated.append(current)
            )

            def execute(current, context):
                context.own_process(process)
                started.set()
                context.cancel_event.wait(3)
                context.check_cancelled()

            scheduler = TaskScheduler(
                TaskStore(store_path), execute, ownership=ownership,
                auto_start=False,
            )
            try:
                scheduler.start()
                scheduler.submit(TaskSpec.create(
                    "https://example.test/process", "video", {}, task_id=task_id,
                ))
                self.assertTrue(started.wait(2))
                self.assertTrue(scheduler.shutdown(timeout=2))
                self.assertEqual(terminated, [process])
                self.assertEqual(
                    scheduler.get(task_id).state, DownloadState.CANCELLED
                )
                self.assertEqual(ownership.owned_process_count(task_id), 0)
            finally:
                scheduler.shutdown(timeout=1)

    def test_startup_restore_never_duplicates_queue_rows_in_the_ui(self):
        task_ids = [
            "00000000-0000-4000-8000-000000000606",
            "00000000-0000-4000-8000-000000000607",
        ]
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            TaskStore(store_path).save([
                TaskRecord.pending(TaskSpec.create(
                    "https://example.test/a", "video", {}, task_id=task_ids[0],
                )),
                TaskRecord.pending(TaskSpec.create(
                    "https://example.test/b", "audio", {}, task_id=task_ids[1],
                )),
            ])
            app = object.__new__(APP_CLASS)
            app.tasks = []
            app.tasks_lock = threading.Lock()
            app.task_widgets = {}
            app.add_task_row = lambda _task: None
            app._refresh_stats = lambda: None

            messages = queue.Queue()
            resolved = {
                task_ids[0]: _task(task_ids[0], "https://example.test/a"),
                task_ids[1]: _task(task_ids[1], "https://example.test/b"),
            }

            def resolver(record):
                return resolved[record.task_id]

            adapter = Build008TaskAdapter(
                store_path, resolver, lambda _task, _context: None, messages,
                visible=app._show_core_task, auto_start=False,
            )
            try:
                adapter.restore_existing()
                adapter.restore_existing()
                self.assertEqual([task.id for task in app.tasks], task_ids)
                adapter.scheduler.start()
                self.assertTrue(adapter.scheduler.wait_for_idle(3))
                self.assertEqual([task.id for task in app.tasks], task_ids)
            finally:
                self.assertTrue(adapter.shutdown())


if __name__ == "__main__":
    unittest.main()
