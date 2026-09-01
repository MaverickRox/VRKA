"""Deterministic coverage for the uBlock Origin Lite browser integration.

Covers the pieces that can be tested without a live WebView2 window:
- WebView2 runtime-folder preference (Evergreen before inbox OS component);
- bundled uBOL archive resolution;
- versioned extraction of the unpacked extension (idempotent);
- the pywebview extension-support patch (applies once, idempotent).

No network access is performed by these tests.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from test_vrka import APP


class RuntimeFolderPreferenceTests(unittest.TestCase):
    def test_inbox_component_is_last_candidate(self):
        """The inbox OS component cannot host browser extensions, so it must
        be the last fallback, never the first pick."""
        dirs = APP._webview2_runtime_candidate_dirs()
        self.assertIsInstance(dirs, list)
        self.assertTrue(dirs)
        last = dirs[-1]
        self.assertIn("System32", last)
        self.assertIn("Microsoft-Edge-WebView", last)
        for d in dirs[:-1]:
            self.assertNotIn("System32", d)
            self.assertNotIn("Microsoft-Edge-WebView", d)

    def test_finder_returns_real_runtime_when_present(self):
        """On Windows with any installed runtime the finder must return a
        folder that actually contains the runtime executable."""
        folder = APP._find_webview2_runtime_folder()
        if os.name == "nt":
            self.assertTrue(folder is None or os.path.isfile(os.path.join(folder, "msedgewebview2.exe")))
        else:
            self.assertIsNone(folder)


class BundledUBOLTests(unittest.TestCase):
    def test_bundled_archive_resolves(self):
        archive = APP._bundled_ubol_zip()
        if archive is None:
            self.skipTest("bundled ubol.zip asset is not present in this tree")
        self.assertTrue(Path(archive).is_file())
        self.assertEqual(Path(archive).name, "ubol.zip")
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertIn("manifest.json", names)

    def test_manifest_is_valid_mv3(self):
        archive = APP._bundled_ubol_zip()
        if archive is None:
            self.skipTest("bundled ubol.zip asset is not present in this tree")
        with zipfile.ZipFile(archive) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        self.assertEqual(manifest.get("manifest_version"), 3)
        self.assertIn("declarative_net_request", manifest)
        self.assertIn("background", manifest)


class ExtractionTests(unittest.TestCase):
    def test_extraction_is_versioned_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ext_dir = tmp / "browser-ext"
            archive = tmp / "ubol.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("manifest.json", json.dumps({
                    "name": "uBO Lite (test)",
                    "version": "1.0.0",
                    "manifest_version": 3,
                }))
                zf.writestr("js/background.js", "// test stub")

            with mock.patch.object(APP, "_bundled_ubol_zip", return_value=str(archive)), \
                 mock.patch.object(APP, "BROWSER_EXT_DIR", ext_dir):
                first = APP._prepare_ubol_extension_dir()
                self.assertIsNotNone(first)
                dest = Path(first)
                self.assertTrue((dest / "manifest.json").is_file())
                self.assertTrue((dest / "js" / "background.js").is_file())
                # second call must be idempotent and return the same folder
                second = APP._prepare_ubol_extension_dir()
                self.assertEqual(first, second)

    def test_missing_archive_returns_none(self):
        with mock.patch.object(APP, "_bundled_ubol_zip", return_value=None):
            self.assertIsNone(APP._prepare_ubol_extension_dir())

    def test_corrupt_archive_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            archive = tmp / "bad.zip"
            archive.write_bytes(b"this is not a zip archive at all")
            with mock.patch.object(APP, "_bundled_ubol_zip", return_value=str(archive)):
                self.assertIsNone(APP._prepare_ubol_extension_dir())


@unittest.skipUnless(os.name == "nt", "WebView2 extension support is Windows-only")
class PywebviewPatchTests(unittest.TestCase):
    def test_patch_applies_once_and_idempotently(self):
        first = APP._patch_pywebview_extension_support()
        self.assertIsInstance(first, bool)
        second = APP._patch_pywebview_extension_support()
        self.assertIsInstance(second, bool)
        if first:
            self.assertTrue(second)
            import webview.platforms.edgechromium as _ec
            self.assertTrue(getattr(_ec, "_vrka_extension_support_applied", False))
            self.assertIsNotNone(getattr(_ec.EdgeChrome, "__init__", None))

    def test_environment_created_with_extensions_enabled_before_init(self):
        source = APP.Path(APP.__file__).read_text(encoding="utf-8")
        patch = source[source.index("def _patch_pywebview_extension_support"):]
        self.assertIn("AreBrowserExtensionsEnabled = True", patch)
        self.assertIn("CoreWebView2Environment.CreateAsync", patch)
        self.assertIn("EnsureCoreWebView2Async(env_task.Result)", patch)
        self.assertIn("EnsureCoreWebView2Async(None)", patch)  # fail-open fallback


class UbolReadinessGatingTests(unittest.TestCase):
    """uBOL must be active before the requested page's first document request."""

    def test_guard_prepares_ubol_before_window_creation(self):
        source = APP.Path(APP.__file__).read_text(encoding="utf-8")
        helper = source[source.index("def run_browser_verification_helper"):]
        self.assertIn("_prepare_ubol_extension_dir()", helper)
        self.assertIn("webview.create_window", helper)
        self.assertLess(
            helper.index("_prepare_ubol_extension_dir()"),
            helper.index("webview.create_window"),
        )

    def test_navigation_gated_on_ubol_readiness_event(self):
        source = APP.Path(APP.__file__).read_text(encoding="utf-8")
        helper = source[source.index("def run_browser_verification_helper"):]
        self.assertIn("ubol_ready = threading.Event()", helper)
        self.assertIn("window.load_url(start_url)", helper)
        self.assertNotIn("url=start_url", helper)  # window opens blank
        # The guarded session thread waits for uBOL readiness and only then
        # navigates to the requested page (no post-install reload hack).
        gate = helper[helper.index("def install_session_guard_when_ready"):]
        self.assertIn("ubol_ready.wait(timeout=60)", gate)
        self.assertIn("navigate_to_requested_page()", gate)
        self.assertLess(
            gate.index("ubol_ready.wait(timeout=60)"),
            gate.index("navigate_to_requested_page()"),
        )

    def test_dnr_warmup_settle_precedes_first_navigation(self):
        # DNR ruleset registration lags extension-install completion; the
        # requested page must not be navigated to before the block rules are
        # live (bounded, fail-open).
        self.assertGreaterEqual(APP.UBOL_DNR_WARMUP_SECONDS, 1.0)
        source = APP.Path(APP.__file__).read_text(encoding="utf-8")
        gate = source[source.index("def install_session_guard_when_ready"):]
        self.assertIn("time.sleep(UBOL_DNR_WARMUP_SECONDS)", gate)
        self.assertLess(
            gate.index("time.sleep(UBOL_DNR_WARMUP_SECONDS)"),
            gate.index("navigate_to_requested_page()"),
        )


if __name__ == "__main__":
    unittest.main()
