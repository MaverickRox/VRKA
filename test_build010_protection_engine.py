"""Deterministic coverage for the adblock-cleanup contract: the homemade
browser-protection engine is gone, uBOL is the single content filter installed
before the requested page loads, media validation works without the adblock
layer, and transfer command construction (concurrent fragments / aria2c)
is unchanged.

These tests never touch the network.
"""

from __future__ import annotations

import unittest
from unittest import mock

from test_vrka import APP


SOURCE_CACHE = {}


def _module_source() -> str:
    if "source" not in SOURCE_CACHE:
        SOURCE_CACHE["source"] = APP.Path(APP.__file__).read_text(encoding="utf-8")
    return SOURCE_CACHE["source"]


class ObsoleteAdblockRemovedTests(unittest.TestCase):
    """The protected browser must have exactly one content filter: uBOL.

    No VRKA-side ad/popup/tracker host lists, resource rules, Python filter
    engines, or DOM cosmetics may remain, because Chrome + uBOL already
    achieves the target behavior without any of them.
    """

    def test_homemade_host_list_engine_removed_from_source(self):
        source = _module_source()
        for symbol in (
            "UNSOLICITED_POPUP_HOST_SUFFIXES",
            "OBVIOUS_TRACKER_HOST_SUFFIXES",
            "ADBLOCK_PROBE_HOST_SUFFIXES",
            "popup_navigation_policy",
            "should_block_top_level_navigation",
            "is_hostile_resource_request",
            "is_unsolicited_popup_host",
            "_obvious_tracker_reason",
            "ProtectionEngine",
            "FilterUpdater",
            "compile_snapshot",
            "bundled_snapshot",
            "BrowserCaptureProtector",
            "load_capture_protector",
            "_protection_engine",
            "_protection_paths",
            "protection_status_summary",
            "protection_update_from_source",
            "protection_rollback",
            "protection_restore_bundled",
            "browser_protection_level",
            "AddWebResourceRequestedFilter",
            "CreateWebResourceResponse",
            "__vrkaNuisanceHidden",
            "smallCornerVideos",
            "resource_filter_installed",
            "filtered_nuisance_count",
            "contained_overlay_count",
            "mayzaent",
            "ruddy-pass",
            "pemsrv",
        ):
            self.assertNotIn(symbol, source, symbol)

    def test_engine_modules_deleted_from_package(self):
        root = APP.Path(APP.__file__).resolve().parent
        self.assertFalse((root / "vrka_core" / "protection.py").exists())
        self.assertFalse((root / "vrka_core" / "capture_protection.py").exists())
        self.assertFalse(
            (root / "assets" / "browser_protection" / "bundled_snapshot.json").exists()
        )

    def test_no_protection_settings_card_remains(self):
        source = _module_source()
        for marker in (
            "protection_level_menu",
            "protection_status_var",
            "Default protection level",
            "compiled network entries",
            "media exceptions",
            "browser_filter_source",
            "Protection runs locally",
        ):
            self.assertNotIn(marker, source, marker)

    def test_no_tracker_classification_in_candidate_pipeline(self):
        record = {"url": "https://doubleclick.net/tag.js", "source": "response"}
        classification = APP.classify_browser_request(record)
        self.assertNotIn("rejected", classification)
        self.assertNotIn("rejection_reason", classification)
        # The pipeline keeps every media request (uBOL is the filter), so a
        # tracker-shaped URL is still a zero-score non-media candidate, never
        # an explicit rejection by VRKA.
        self.assertEqual(classification["score"], 0)
        self.assertEqual(classification["kind"], "")


class UbolBeforeNavigationTests(unittest.TestCase):
    """uBOL must be installed before the requested page's first document
    request: the window opens blank and navigates only after the uBOL
    readiness gate."""

    def test_window_opens_blank_and_navigates_after_ubol_gate(self):
        source = _module_source()
        helper = source[source.index("def run_browser_verification_helper"):]
        self.assertIn('url="about:blank"', helper)
        # The only navigation to the requested page happens in the guard
        # thread, after waiting for uBOL readiness.
        self.assertIn("window.load_url(start_url)", helper)
        # The requested page is navigated to from the guarded session thread
        # after the uBOL readiness gate, never at window creation.
        gate = helper[helper.index("def install_session_guard_when_ready"):]
        self.assertIn("ubol_ready.wait(timeout=60)", gate)
        self.assertIn("navigate_to_requested_page()", gate)
        self.assertLess(
            gate.index("ubol_ready.wait(timeout=60)"),
            gate.index("navigate_to_requested_page()"),
        )

    def test_guard_blocks_new_windows_without_ad_classification(self):
        source = _module_source()
        guard = source[source.index("def install_webview2_session_guard"):]
        self.assertIn("args.set_Handled(True)", guard)
        # Evidence instrumentation is retained (never ad classification).
        for marker in ("navigation_log", "blocked_urls", 'event": "popup"'):
            self.assertIn(marker, guard)
        self.assertNotIn("IsUserInitiated", guard)

    def test_ubol_failure_is_recorded_and_navigation_still_proceeds(self):
        source = _module_source()
        guard = source[source.index("def install_webview2_session_guard"):]
        self.assertIn('"uBOL install failed (extension support unavailable?)"', guard)
        self.assertIn("ubol_error", guard)
        # The readiness event is set on failure so navigation is never stuck.
        self.assertIn("ubol_ready.set()", guard)

    def test_media_validation_works_without_the_adblock_layer(self):
        # The ranker still classifies and orders real media correctly with no
        # Python filter applied (uBOL owns filtering).
        candidates = APP.rank_media_candidates((
            {"url": "https://cdn.example/seg_001.ts", "content_type": "video/mp2t"},
            {
                "url": "https://cdn.example/master.m3u8",
                "content_type": "application/vnd.apple.mpegurl",
                "source": "dom",
            },
            {
                "url": "https://cdn.example/video.mp4",
                "content_type": "video/mp4",
                "source": "dom",
            },
        ))
        urls = [item["url"] for item in candidates]
        self.assertIn("https://cdn.example/master.m3u8", urls)
        self.assertIn("https://cdn.example/video.mp4", urls)
        # A standalone segment without a captured parent manifest is rejected.
        self.assertNotIn("https://cdn.example/seg_001.ts", urls)

    def test_widget_cam_cannot_win_merely_because_adblock_cleanup_removed_filters(self):
        # A playable 240p sidebar cam is legitimate media, never rejected by a
        # shape/adblock heuristic: it is kept out of the requested-media role
        # by media identity (numeric stream-id lineage from the widget
        # cluster), which is independent of any filter layer.
        url = "https://edge-hls.growcdnssedge.com/hls/242330696/240p/index.m3u8"
        self.assertFalse(APP._segment_shaped(url))
        self.assertEqual(APP._numeric_stream_ids(url), {"242330696"})
        candidates = [{"url": url, "kind": "HLS", "score": 180}]
        widget_cluster = {"seen": True, "first_seq": 1}
        APP.mark_widget_candidates(
            candidates, {}, widget_cluster, set(), 2,
            cluster_now=False, widgets_still_visible=True,
        )
        self.assertIs(candidates[0]["user_started"], False)
        # A later, unrelated episode master (no numeric stream id) keeps the
        # default user_started=True and outranks the widget lineage.
        episode = {
            "url": "https://media-hls.growcdnssedge.com/javclan/master.m3u8",
            "kind": "HLS", "score": 188,
        }
        APP.mark_widget_candidates(
            [episode], {}, widget_cluster, {"242330696"}, 5,
            cluster_now=False, widgets_still_visible=True,
        )
        self.assertIs(episode.get("user_started"), None)
        self.assertGreater(episode["score"], candidates[0]["score"])


class TransferConcurrencyTests(unittest.TestCase):
    class Task:
        mode = "video"
        url = "https://example.test/playlist.m3u8"
        options = {"cookie_mode": "none", "impersonation": "Automatic"}

    def _args(self, **option_overrides):
        options = dict(self.Task.options)
        options.update(option_overrides)
        task = type("T", (), {"mode": "video",
                              "url": self.Task.url, "options": options})()
        return APP._standard_ytdlp_arguments(task, "out")

    def test_default_concurrent_fragments_4(self):
        args = self._args()
        self.assertIn("--concurrent-fragments", args)
        self.assertEqual(
            args[args.index("--concurrent-fragments") + 1], "4")

    def test_override_and_cap(self):
        self.assertEqual(
            self._args(concurrent_fragments=8)[
                self._args(concurrent_fragments=8).index("--concurrent-fragments") + 1],
            "8")
        args = self._args(concurrent_fragments=12)
        self.assertEqual(
            args[args.index("--concurrent-fragments") + 1], "8")
        args = self._args(concurrent_fragments=1)
        self.assertNotIn("--concurrent-fragments", args)

    def test_aria2c_backend_engaged_only_when_binary_present(self):
        with mock.patch.object(APP, "_find_aria2c", return_value=r"C:\bin\aria2c.exe"):
            args = self._args(transport_backend="aria2c")
            self.assertIn("--downloader", args)
            self.assertEqual(args[args.index("--downloader") + 1], "aria2c")
        # No binary -> backend stays dormant, native concurrency remains.
        with mock.patch.object(APP, "_find_aria2c", return_value=None):
            args = self._args(transport_backend="aria2c")
            self.assertNotIn("--downloader", args)
            self.assertIn("--concurrent-fragments", args)

    def test_default_backend_native(self):
        args = self._args()
        self.assertNotIn("--downloader", args)


if __name__ == "__main__":
    unittest.main()
