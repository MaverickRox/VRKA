"""Focused build010 frozen-context smoke tests.

These verify what the internal entry points do when invoked as a frozen
executable: the self-invocation yt-dlp CLI mode, the protected-browser and
browser verification helpers, the bundled resource base, and the absence of
the build009 one-file backend extraction packaging design.
"""

import os
import unittest
from pathlib import Path
from unittest import mock

from test_vrka import APP, APP_FILE, workspace_temporary_directory


class Build010FrozenSmokeTests(unittest.TestCase):
    def test_frozen_self_invocation_uses_the_executable_without_a_script_path(self):
        with mock.patch.object(APP.sys, "frozen", True, create=True):
            self.assertEqual(APP.build_self_invocation(), [APP.sys.executable])
        with mock.patch.object(APP.sys, "frozen", False, create=True):
            invocation = APP.build_self_invocation()
            self.assertEqual(invocation[0], APP.sys.executable)
            self.assertEqual(invocation[1], os.path.abspath(APP.__file__))

    def test_frozen_resource_base_uses_the_bundle_extraction_directory(self):
        with mock.patch.object(APP.sys, "frozen", True, create=True), \
             mock.patch.object(APP.sys, "_MEIPASS", "C:/bundle", create=True):
            self.assertEqual(APP.get_resource_base(), Path("C:/bundle"))
        with mock.patch.object(APP.sys, "frozen", False, create=True), \
             mock.patch.object(APP.sys, "_MEIPASS", "C:/bundle", create=True):
            self.assertEqual(
                APP.get_resource_base(), Path(APP.__file__).resolve().parent
            )

    def test_frozen_bundled_backend_self_invokes_the_cli_mode(self):
        with workspace_temporary_directory() as tmpdir:
            with mock.patch.object(APP, "RUNTIME_DIR", Path(tmpdir)):
                with mock.patch.object(APP.sys, "frozen", True, create=True):
                    frozen_backend = APP.resolve_ytdlp_backend()
                    self.assertEqual(frozen_backend.source, "bundled")
                    self.assertEqual(
                        frozen_backend.command,
                        (APP.sys.executable, "__ytdlp_cli__"),
                    )
                with mock.patch.object(APP.sys, "frozen", False, create=True):
                    script_backend = APP.resolve_ytdlp_backend()
                    self.assertEqual(script_backend.source, "bundled")
                    self.assertEqual(
                        script_backend.command,
                        (
                            APP.sys.executable,
                            os.path.abspath(APP.__file__),
                            "__ytdlp_cli__",
                        ),
                    )

    def test_protected_browser_entry_uses_protected_capture(self):
        with mock.patch.object(
            APP, "run_browser_verification_helper", return_value=0
        ) as helper:
            result = APP.run_protected_browser_helper(
                "https://example.test/watch", "C:/tmp/out.json"
            )
        helper.assert_called_once_with(
            "https://example.test/watch", "C:/tmp/out.json",
            protected=True,
        )
        self.assertEqual(result, 0)

    def test_frozen_startup_runtime_path_only_prepends_bundled_deno(self):
        with mock.patch.object(APP, "get_bundled_deno_dir", return_value=None):
            self.assertIsNone(APP.configure_bundled_runtime_path())
        with mock.patch.object(
            APP, "get_bundled_deno_dir", return_value="C:/bundle/deno_bin"
        ):
            with mock.patch.dict(APP.os.environ, {"PATH": "C:/original"}, clear=False):
                self.assertEqual(
                    APP.configure_bundled_runtime_path(), "C:/bundle/deno_bin"
                )
                self.assertTrue(
                    APP.os.environ["PATH"].startswith("C:/bundle/deno_bin;")
                )

    def test_cli_stream_restore_is_a_noop_outside_frozen_windows(self):
        streams = (APP.sys.stdin, APP.sys.stdout, APP.sys.stderr)
        with mock.patch.object(APP.sys, "frozen", False, create=True):
            APP.restore_frozen_cli_streams()
        self.assertEqual((APP.sys.stdin, APP.sys.stdout, APP.sys.stderr), streams)

    def test_cli_stream_restore_never_trusts_broken_handles(self):
        """A windowed EXE can be launched with std handles that are NULL,
        detached, or non-null but broken (closed parent pipe).  Wrapping such
        handles succeeds and the first yt-dlp write dies with OSError [Errno
        22].  The restore must therefore probe handle usability, attach the
        parent console when one exists, fall back to CONOUT$/CONIN$, and use
        devnull only as the final writable fallback."""
        source = APP_FILE.read_text(encoding="utf-8")
        restore = source[source.index("def restore_frozen_cli_streams()"):]
        self.assertIn("GetFileType", restore)
        self.assertIn("AttachConsole", restore)
        self.assertIn("CONOUT$", restore)
        self.assertIn("CONIN$", restore)
        self.assertIn("os.devnull", restore)
        self.assertIn("os.fstat(stream.fileno())", restore)
        # Broken-but-present wrappers must be probed (zero-byte WriteFile),
        # never garbage-collected with unflushable data, and replaced.
        self.assertIn("_frozen_std_stream_broken(existing)", restore)
        self.assertIn("_DISCARDED_STD_STREAMS.append(existing)", restore)

    def test_frozen_cli_survives_broken_stdio_with_meaningful_exit(self):
        """The __ytdlp_cli__ dispatch must degrade to devnull-backed streams
        on an stdio OSError and retry once, preserving the exit code instead
        of crashing inside yt-dlp's writers."""
        source = APP_FILE.read_text(encoding="utf-8")
        main_block = source[source.index('if __name__ == "__main__":'):]
        cli_block = main_block[main_block.index('"__ytdlp_cli__"'):]
        self.assertIn("except OSError:", cli_block)
        self.assertIn("os.devnull", cli_block)
        self.assertIn("sys.__stdout__ = sys.stdout", cli_block)
        # Exactly one retry: no unbounded loop.
        self.assertEqual(cli_block.count("yt_dlp.main(sys.argv[2:])"), 2)

    def test_protected_capture_is_not_window_close_dependent(self):
        """Protected-browser capture must be live (on demand while the window
        stays open), never a side effect of closing the window: the helper owns
        a stdin command channel (capture/commit/cancel), a media-playable
        watcher, and flags premature manual closes as manual_closed."""
        source = APP_FILE.read_text(encoding="utf-8")
        self.assertIn("manual_closed", source)
        self.assertIn("start_stdin_worker", source)
        self.assertIn('command == "capture"', source)
        self.assertIn('command == "commit"', source)
        self.assertIn('command == "cancel"', source)
        self.assertIn("__vrkaMediaPlayable", source)
        # The protected entry still routes through the shared helper.
        self.assertIn("sys.exit(run_protected_browser_helper(", source)

    def test_cli_sentinels_dispatch_before_gui_construction(self):
        source = APP_FILE.read_text(encoding="utf-8")
        main_block = source[source.index('if __name__ == "__main__":'):]
        gui_index = main_block.index("app = VRKADownloader()")
        for sentinel in (
            "__vrka_diagnostics__",
            "__ytdlp_cli__",
            "__vrka_protected_browser__",
            "__vrka_browser__",
        ):
            self.assertLess(main_block.index(sentinel), gui_index)
        # Protected-browser capture is handled before the ordinary verification.
        self.assertLess(
            main_block.index("__vrka_protected_browser__"),
            main_block.index("__vrka_browser__"),
        )
        self.assertIn("sys.exit(run_protected_browser_helper(", main_block)
        self.assertIn("sys.exit(run_browser_verification_helper(", main_block)
        self.assertIn("sys.exit(yt_dlp.main(sys.argv[2:]))", main_block)
        self.assertIn("restore_frozen_cli_streams()", main_block)

    def test_build009_onefile_backend_packaging_design_is_not_reused(self):
        source = APP_FILE.read_text(encoding="utf-8")
        # No bundle-to-disk backend extraction (the build009 startup cost).
        self.assertNotIn("unpack_archive", source)
        self.assertNotIn("extractall", source)
        self.assertNotIn("VRKA-Backend", source)
        # The runtime design is managed-runtime plus self-invocation fallback.
        self.assertIn('build_self_invocation() + ["__ytdlp_cli__"]', source)
        # The Windows recipe bundles ffmpeg/deno/assets but never a yt-dlp binary.
        spec = Path(__file__).with_name("VRKA-Windows.spec").read_text(encoding="utf-8")
        self.assertNotIn("yt-dlp", spec)
        self.assertIn("ffmpeg_bin", spec)
        self.assertIn("deno_bin", spec)
        self.assertIn("console=False", spec)


if __name__ == "__main__":
    unittest.main()
