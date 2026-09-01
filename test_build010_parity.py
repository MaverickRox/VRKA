"""Focused build010 parity verification.

These tests pin the build008 reference behavior to the durable build010 core
adapter: the full build008 options set must reach the central yt-dlp command
builder unchanged through durable submit and restart restore, and the per-task
staging directory must always be cleaned up when a direct attempt fails.
"""

import queue
import re
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from test_vrka import APP, APP_CLASS, standard_options, workspace_temporary_directory

from vrka_core import Build008TaskAdapter, TaskRecord, TaskSpec, TaskStore


def backend(source="managed", version="test"):
    return APP.YTDLPBackend(source, ("yt-dlp-test",), version, "yt-dlp-test")


_STAGING_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _normalize_staging(command):
    """Mask the per-call staging UUID so two built commands compare equal."""
    normalized = []
    for part in command:
        if part.startswith("temp:"):
            base, separator, tail = part.rpartition("\\")
            if separator and _STAGING_UUID.fullmatch(tail):
                part = base + "\\<staging>"
        normalized.append(part)
    return normalized


class Build010ParityTests(unittest.TestCase):
    def test_full_build008_options_reach_command_builder_unchanged_after_durable_restore(self):
        options = standard_options(
            quality="2160p (4K)",
            fps60=True,
            mp3_bitrate="256 kbps",
            audio_format="MP3",
            download_subs=True,
            sub_langs="en.*,ja",
            embed_subs=True,
            embed_thumbnail=True,
            sponsorblock=True,
            sponsorblock_categories="sponsor",
            cookie_mode="browser",
            cookie_browser="firefox",
            proxy="http://127.0.0.1:8080",
            rate_limit="2M",
            restrict_filenames=True,
        )
        task_id = "00000000-0000-4000-8000-000000000501"
        with TemporaryDirectory() as directory:
            store_path = Path(directory) / "tasks.json"
            task = APP.DownloadTask(
                id=task_id, url="https://example.test/parity", mode="video",
                options=options,
            )
            with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()):
                reference_command = APP.build_standard_ytdlp_command(task, directory)[1]

            adapter = Build008TaskAdapter(
                store_path, lambda _record: task, lambda _task, _context: None,
                queue.Queue(), auto_start=False,
            )
            try:
                adapter.submit(task)
                record = adapter.scheduler.get(task_id)
                persisted = record.spec.to_dict()["options"]
                self.assertEqual(persisted["quality"], "2160p (4K)")
                self.assertEqual(persisted["mp3_bitrate"], "256 kbps")
                self.assertEqual(persisted["sub_langs"], "en.*,ja")
                self.assertEqual(persisted["cookie_browser"], "firefox")
                self.assertEqual(persisted["rate_limit"], "2M")

                # Restart: rehydrate the task from the durable store alone and
                # rebuild the exact same yt-dlp command.
                [restored_record] = TaskStore(store_path).load(recover=True)
                restored = APP.DownloadTask(
                    id=restored_record.task_id,
                    url=restored_record.spec.url,
                    mode=restored_record.spec.mode,
                    options=restored_record.spec.to_dict()["options"],
                )
                with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()):
                    restored_command = APP.build_standard_ytdlp_command(
                        restored, directory
                    )[1]
                self.assertEqual(
                    _normalize_staging(restored_command),
                    _normalize_staging(reference_command),
                )
            finally:
                self.assertTrue(adapter.shutdown())

    def test_staging_dir_is_cleaned_up_when_direct_attempt_fails(self):
        task_id = "00000000-0000-4000-8000-000000000502"
        with workspace_temporary_directory() as tmpdir:
            staging = Path(tmpdir) / "staging"
            with (
                mock.patch.object(APP, "STAGING_DIR", staging),
                mock.patch.object(APP, "BROWSER_SESSION_DIR", Path(tmpdir) / "browser-session"),
            ):
                spec = TaskSpec.create(
                    "https://example.test/staging", "custom",
                    {"output_folder": tmpdir}, task_id=task_id,
                )
                record = TaskRecord.pending(spec)
                task = SimpleNamespace(
                    id=task_id, url=spec.url, mode="custom",
                    options={"output_folder": tmpdir},
                    _core_record=record, title="", output_path="", progress=0.0,
                    speed="", eta="", process=None,
                )
                context = SimpleNamespace(
                    check_cancelled=lambda: None,
                    log=lambda _message: None,
                    cancel_event=threading.Event(),
                )
                worker = object.__new__(APP_CLASS)
                worker._protected_browser_launcher = None

                failing_direct = mock.Mock(
                    side_effect=APP.YTDLPCommandError("boom", category="unknown", output="")
                )

                with mock.patch.object(APP_CLASS, "_run_core_direct_attempt", failing_direct):
                    with self.assertRaises(APP.YTDLPCommandError):
                        APP_CLASS._execute_core_task(worker, task, context)
                self.assertNotIn("_staging_dir", task.options)
                self.assertEqual(list(staging.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
