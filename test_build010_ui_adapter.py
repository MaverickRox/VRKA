import queue
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from vrka_core import Build008TaskAdapter, DownloadState, TaskSpec


class UIAdapterTests(unittest.TestCase):
    def test_durable_submit_precedes_visible_queue_and_core_events_use_build008_tuples(self):
        task_id = "00000000-0000-4000-8000-000000000401"
        messages = queue.Queue()
        visible = []
        history = []
        task = SimpleNamespace(
            id=task_id, url="https://example.test/adapter", mode="video",
            options={"quality": "1080p"}, status="queued", progress=0.0,
            title="", output_path="", error="", speed="", eta="", stage="Waiting",
        )

        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            adapter = None

            def on_visible(current):
                self.assertTrue(store_path.exists())
                self.assertIn(task_id, store_path.read_text(encoding="utf-8"))
                visible.append(current.id)

            def resolver(record):
                return task

            def execute(current, context):
                self.assertIs(current._core_context, context)
                context.transition(DownloadState.DOWNLOAD_RUNNING)
                context.progress(0.5, title="adapter task", speed="1MiB/s", eta="00:02")

            adapter = Build008TaskAdapter(
                store_path, resolver, execute, messages,
                visible=on_visible, history=lambda current: history.append(current.id),
                auto_start=False,
            )
            try:
                adapter.submit(task)
                self.assertEqual(visible, [task_id])
                adapter.scheduler.start()
                self.assertTrue(adapter.scheduler.wait_for_idle(2))
                self.assertEqual(history, [task_id])
                self.assertEqual(adapter.scheduler.get(task_id).state, DownloadState.COMPLETED)
                observed = []
                while not messages.empty():
                    observed.append(messages.get_nowait())
                self.assertIn(("task_status", task_id, "downloading"), observed)
                self.assertIn(("task_progress", task_id, 0.5), observed)
                self.assertIn(("task_status", task_id, "completed"), observed)
                self.assertIn(("history_refresh", None), observed)
            finally:
                self.assertTrue(adapter.shutdown())

    def test_one_adapter_cancel_keeps_task_identity_and_does_not_duplicate_history(self):
        task_id = "00000000-0000-4000-8000-000000000402"
        messages = queue.Queue()
        started = __import__("threading").Event()
        task = SimpleNamespace(
            id=task_id, url="https://example.test/cancel", mode="audio",
            options={}, status="queued", progress=0.0,
            title="", output_path="", error="", speed="", eta="", stage="Waiting",
        )

        with TemporaryDirectory() as directory:
            adapter = Build008TaskAdapter(
                Path(directory) / "tasks.json", lambda _record: task,
                lambda _task, context: (started.set(), context.cancel_event.wait(3), context.check_cancelled()),
                messages, auto_start=True,
            )
            try:
                adapter.submit(task)
                self.assertTrue(started.wait(2))
                self.assertTrue(adapter.cancel(task_id))
                self.assertFalse(adapter.cancel(task_id))
                self.assertTrue(adapter.scheduler.wait_for_idle(2))
                self.assertEqual(adapter.scheduler.get(task_id).task_id, task_id)
                self.assertEqual(adapter.scheduler.get(task_id).state, DownloadState.CANCELLED)
            finally:
                self.assertTrue(adapter.shutdown())


    def test_restore_existing_record_uses_existing_queue_identity(self):
        task_id = "00000000-0000-4000-8000-000000000403"
        messages = queue.Queue()
        visible = []
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            from vrka_core import TaskRecord, TaskStore
            TaskStore(store_path).save([
                TaskRecord.pending(TaskSpec.create(
                    "https://example.test/restored", "video", {"quality": "best"},
                    task_id=task_id,
                ))
            ])

            def resolver(record):
                return SimpleNamespace(
                    id=record.task_id, url=record.spec.url, mode=record.spec.mode,
                    options=record.spec.to_dict()["options"], status="queued",
                    progress=record.progress, title=record.title,
                    output_path=record.output_path, error=record.error,
                    speed=record.speed, eta=record.eta, stage="Waiting", process=None,
                )

            adapter = Build008TaskAdapter(
                store_path, resolver, lambda _task, _context: None, messages,
                visible=lambda task: visible.append(task.id), auto_start=False,
            )
            try:
                restored = adapter.restore_existing()
                self.assertEqual([task.id for task in restored], [task_id])
                self.assertEqual(visible, [task_id])
                self.assertEqual(adapter.scheduler.get(task_id).task_id, task_id)
            finally:
                self.assertTrue(adapter.shutdown())

    def test_retry_reuses_logical_task_and_history_identity(self):
        task_id = "00000000-0000-4000-8000-000000000404"
        messages = queue.Queue()
        task = SimpleNamespace(
            id=task_id, url="https://example.test/retry", mode="video", options={},
            status="queued", progress=0.0, title="", output_path="", error="",
            speed="", eta="", stage="Waiting", process=None,
        )
        runs = []
        history = []
        with TemporaryDirectory() as directory:
            def execute(current, context):
                runs.append(current.id)
                context.transition(DownloadState.DOWNLOAD_RUNNING)
                context.progress(0.5, title="same logical task")

            adapter = Build008TaskAdapter(
                Path(directory) / "tasks.json", lambda _record: task, execute, messages,
                history=lambda current: history.append(current.id), auto_start=True,
            )
            try:
                adapter.submit(task)
                self.assertTrue(adapter.scheduler.wait_for_idle(2))
                self.assertTrue(adapter.retry(task_id))
                self.assertTrue(adapter.scheduler.wait_for_idle(2))
                self.assertEqual(runs, [task_id, task_id])
                self.assertEqual(history, [task_id])
                self.assertEqual(adapter.scheduler.get(task_id).task_id, task_id)
                self.assertEqual(adapter.scheduler.get(task_id).state, DownloadState.COMPLETED)
            finally:
                self.assertTrue(adapter.shutdown())

    def test_durable_completed_task_does_not_duplicate_history_on_retry(self):
        task_id = "00000000-0000-4000-8000-000000000405"
        messages = queue.Queue()
        history = []
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            from vrka_core import TaskRecord, TaskStore
            record = TaskRecord.pending(TaskSpec.create(
                "https://example.test/completed", "audio", {}, task_id=task_id,
            ))
            record.transition(DownloadState.DIRECT_ATTEMPT)
            record.transition(DownloadState.DOWNLOAD_RUNNING)
            record.transition(DownloadState.COMPLETED)
            record.consume_terminal_event()
            TaskStore(store_path).save([record])

            def resolver(current):
                return SimpleNamespace(
                    id=current.task_id, url=current.spec.url, mode=current.spec.mode,
                    options=current.spec.to_dict()["options"], status="completed",
                    progress=1.0, title="done", output_path="", error="",
                    speed="", eta="", stage="Waiting", process=None,
                )

            def execute(_task, context):
                context.transition(DownloadState.DOWNLOAD_RUNNING)

            adapter = Build008TaskAdapter(
                store_path, resolver, execute, messages,
                history=lambda task: history.append(task.id), auto_start=False,
            )
            try:
                adapter.restore_existing()
                self.assertTrue(adapter.retry(task_id))
                adapter.scheduler.start()
                self.assertTrue(adapter.scheduler.wait_for_idle(2))
                self.assertEqual(history, [])
                self.assertEqual(adapter.scheduler.get(task_id).task_id, task_id)
            finally:
                self.assertTrue(adapter.shutdown())
    def test_cleared_completed_tasks_do_not_resurrect_after_restart(self):
        # Regression B/C: Clear Completed / Remove must reach the durable store.
        messages = queue.Queue()
        history = []
        completed_id = "00000000-0000-4000-8000-000000000406"
        queued_id = "00000000-0000-4000-8000-000000000407"
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"

            def make_task(task_id, url):
                return SimpleNamespace(
                    id=task_id, url=url, mode="video", options={}, status="queued",
                    progress=0.0, title="", output_path="", error="",
                    speed="", eta="", stage="Waiting", process=None,
                )

            def resolver(record):
                return make_task(record.task_id, record.spec.url)

            def execute(_task, context):
                context.transition(DownloadState.DOWNLOAD_RUNNING)

            first = Build008TaskAdapter(
                store_path, resolver, execute, messages,
                history=lambda task: history.append(task.id), auto_start=True,
            )
            try:
                done = make_task(completed_id, "https://example.test/done")
                held = make_task(queued_id, "https://example.test/held")
                first.submit(done)
                self.assertTrue(first.scheduler.wait_for_idle(2))
                first.submit(held)
            finally:
                self.assertTrue(first.shutdown())

            second = Build008TaskAdapter(
                store_path, resolver, lambda _task, _context: None, messages,
                auto_start=False,
            )
            try:
                restored = {task.id for task in second.restore_existing()}
                self.assertEqual(restored, {completed_id, queued_id})
                # Clear Completed drops only terminal durable records.
                self.assertEqual(second.clear_finished(), 1)
                self.assertEqual(
                    [task.id for task in second.restore_existing()], [queued_id],
                )
                # Retry of a forgotten task is refused; history was already
                # emitted exactly once and stays untouched.
                self.assertFalse(second.retry(completed_id))
                self.assertEqual(history, [completed_id])
            finally:
                self.assertTrue(second.shutdown())

            third = Build008TaskAdapter(
                store_path, resolver, lambda _task, _context: None, messages,
                auto_start=False,
            )
            try:
                self.assertEqual(
                    [task.id for task in third.restore_existing()], [queued_id],
                )
                # Single-row removal of the remaining task persists too.
                self.assertTrue(third.remove(queued_id))
                third.clear_finished()
            finally:
                self.assertTrue(third.shutdown())

            fourth = Build008TaskAdapter(
                store_path, resolver, lambda _task, _context: None, messages,
                auto_start=False,
            )
            try:
                self.assertEqual(fourth.restore_existing(), ())
                self.assertEqual(fourth.scheduler.records(), ())
            finally:
                self.assertTrue(fourth.shutdown())
            from vrka_core import TaskStore
            self.assertEqual(TaskStore(store_path).load(), [])

    def test_scheduler_forget_never_drops_the_executing_task(self):
        started = __import__("threading").Event()
        release = __import__("threading").Event()
        task_id = "00000000-0000-4000-8000-000000000408"
        task = SimpleNamespace(
            id=task_id, url="https://example.test/active", mode="video", options={},
            status="queued", progress=0.0, title="", output_path="", error="",
            speed="", eta="", stage="Waiting", process=None,
        )
        messages = queue.Queue()
        with TemporaryDirectory() as directory:
            adapter = Build008TaskAdapter(
                Path(directory) / "tasks.json", lambda _record: task,
                lambda _task, _context: (started.set(), release.wait(3)),
                messages, auto_start=True,
            )
            try:
                adapter.submit(task)
                self.assertTrue(started.wait(2))
                self.assertEqual(adapter.scheduler.active_task_id, task_id)
                self.assertEqual(adapter.scheduler.forget(task_id), ())
                self.assertIsNotNone(adapter.scheduler.get(task_id))
                release.set()
                self.assertTrue(adapter.scheduler.wait_for_idle(2))
                self.assertEqual(adapter.scheduler.get(task_id).state, DownloadState.COMPLETED)
            finally:
                self.assertTrue(adapter.shutdown())

    def test_incomplete_queued_record_survives_clear_completed(self):
        # Regression D: genuine resumable tasks are not deleted by Clear Completed.
        messages = queue.Queue()
        queued_id = "00000000-0000-4000-8000-000000000409"
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            from vrka_core import TaskRecord, TaskStore
            TaskStore(store_path).save([
                TaskRecord.pending(TaskSpec.create(
                    "https://example.test/resume", "video", {}, task_id=queued_id,
                ))
            ])
            adapter = Build008TaskAdapter(
                store_path, lambda _record: None, lambda _task, _context: None,
                messages, auto_start=False,
            )
            try:
                self.assertEqual(adapter.clear_finished(), 0)
                reloaded = TaskStore(store_path).load(recover=True)
                self.assertEqual([record.task_id for record in reloaded], [queued_id])
            finally:
                self.assertTrue(adapter.shutdown())

    def test_build008_execution_seams_call_the_single_core_adapter(self):
        source = Path(__file__).with_name("vrka_downloader.py").read_text(encoding="utf-8")
        self.assertIn("self._core_adapter = Build008TaskAdapter(", source)
        self.assertIn("self._core_adapter.restore_existing()", source)
        self.assertIn("self._core_adapter.scheduler.start()", source)
        self.assertIn("self._core_adapter.submit(task)", source)
        self.assertIn("adapter.cancel(task_id)", source)
        self.assertIn("adapter.retry(task_id)", source)
        self.assertIn("adapter.remove(task_id)", source)
        self.assertIn("adapter.clear_finished()", source)
        self.assertIn("adapter.shutdown(timeout=1.5)", source)
        self.assertIn("MonitoredProcessRunner().run(", source)
        self.assertNotIn("target=self.queue_worker, daemon=True", source)
if __name__ == "__main__":
    unittest.main()
