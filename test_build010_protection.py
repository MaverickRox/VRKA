"""Deterministic coverage for the simplification pass: the homemade VRKA
adblock engine and its Settings card are removed, uBOL is installed before
the requested page loads, and media validation/ranking is unaffected.

These tests never touch the network or launch a browser.
"""

import json
import unittest
from pathlib import Path
from unittest import mock

from test_vrka import APP

REMOVED_SYMBOLS = (
    "UNSOLICITED_POPUP_HOST_SUFFIXES",
    "OBVIOUS_TRACKER_HOST_SUFFIXES",
    "popup_navigation_policy",
    "should_block_top_level_navigation",
    "is_hostile_resource_request",
    "is_unsolicited_popup_host",
    "ProtectionEngine",
    "FilterUpdater",
    "compile_snapshot",
    "bundled_snapshot",
    "BrowserCaptureProtector",
    "load_capture_protector",
    "protection_status_summary",
    "protection_update_from_source",
    "protection_rollback",
    "protection_restore_bundled",
    "browser_protection_level",
    "browser_filter_source",
    "__vrkaNuisanceHidden",
    "resource_filter_installed",
    "filtered_nuisance_count",
    "AddWebResourceRequestedFilter",
)


class AdblockRemovalTests(unittest.TestCase):
    @staticmethod
    def _source() -> str:
        return APP.Path(APP.__file__).read_text(encoding="utf-8")

    def test_obsolete_adblock_mechanisms_are_not_present(self):
        source = self._source()
        for symbol in REMOVED_SYMBOLS:
            self.assertNotIn(symbol, source, symbol)

    def test_adblock_modules_and_snapshot_asset_deleted(self):
        root = Path(APP.__file__).resolve().parent
        self.assertFalse((root / "vrka_core" / "protection.py").exists())
        self.assertFalse((root / "vrka_core" / "capture_protection.py").exists())
        self.assertFalse(
            (root / "assets" / "browser_protection" / "bundled_snapshot.json").exists()
        )

    def test_about_card_remains_without_protection_card(self):
        source = self._source()
        self.assertIn('"About"', source)
        self.assertIn("Licenses & notices", source)
        self.assertIn("def _open_notices", source)
        self.assertNotIn("Browser Protection", source)
        self.assertNotIn("protection_level_menu", source)

    def test_package_spec_bundles_ubol_assets_and_notices(self):
        root = Path(APP.__file__).resolve().parent
        windows_spec = (root / "VRKA-Windows.spec").read_text(encoding="utf-8")
        self.assertIn("assets/browser_protection", windows_spec)
        self.assertIn("THIRD_PARTY_NOTICES.md", windows_spec)
        mac_spec = (root / "VRKA-macOS.spec").read_text(encoding="utf-8")
        self.assertIn("assets/browser_protection", mac_spec)
        # The bundled extension archive is present and valid MV3.
        archive = root / "assets" / "browser_protection" / "ubol.zip"
        self.assertTrue(archive.exists())
        import zipfile
        with zipfile.ZipFile(archive) as handle:
            manifest = json.loads(handle.read("manifest.json"))
        self.assertEqual(manifest["manifest_version"], 3)


class UbolBeforeNavigationTests(unittest.TestCase):
    def test_target_navigation_is_gated_on_ubol_readiness(self):
        source = APP.Path(APP.__file__).read_text(encoding="utf-8")
        helper = source[source.index("def run_browser_verification_helper"):]
        # The window opens on a blank page, never on the target URL.
        self.assertIn('url="about:blank"', helper)
        self.assertNotIn("url=start_url", helper)
        # uBOL installation precedes the first navigation to the target.
        guard = helper[helper.index("def install_webview2_session_guard"):]
        nav = helper[helper.index("def navigate_to_requested_page"):]
        self.assertIn("AddBrowserExtensionAsync", guard)
        self.assertIn("ubol_ready.wait(timeout=60)", helper)
        self.assertLess(
            helper.index("AddBrowserExtensionAsync"),
            helper.index("window.load_url(start_url)"),
        )
        # The requested page is navigated to from the guarded session thread
        # (not at window creation), so no post-install reload is needed.
        gate = helper[helper.index("def install_session_guard_when_ready"):]
        self.assertIn("ubol_ready.wait(timeout=60)", gate)
        self.assertIn("navigate_to_requested_page()", gate)
        self.assertLess(
            gate.index("ubol_ready.wait(timeout=60)"),
            gate.index("navigate_to_requested_page()"),
        )

    def test_new_window_firewall_is_unconditional_and_unclassified(self):
        source = APP.Path(APP.__file__).read_text(encoding="utf-8")
        guard = source[source.index("def install_webview2_session_guard"):]
        self.assertIn("args.set_Handled(True)", guard)
        self.assertNotIn("popup_navigation_policy", guard)
        self.assertNotIn("IsUserInitiated", guard)
        # Evidence instrumentation remains.
        for marker in ("navigation_log", 'event": "popup"', "blocked_urls"):
            self.assertIn(marker, guard)


class MediaValidationUnaffectedTests(unittest.TestCase):
    def test_master_manifest_preference_survives_cleanup(self):
        candidates = APP.rank_media_candidates((
            {
                "url": "https://cdn.example/720p/video.m3u8",
                "content_type": "application/vnd.apple.mpegurl",
            },
            {
                "url": "https://cdn.example/master.m3u8",
                "content_type": "application/vnd.apple.mpegurl",
                "source": "dom",
            },
        ))
        ranked = sorted(candidates, key=lambda item: -int(item.get("score") or 0))
        self.assertEqual(ranked[0]["url"], "https://cdn.example/master.m3u8")

    def test_widget_lineage_and_segment_rejection_are_unchanged(self):
        # A widget-cluster numeric stream id keeps a later 240p cam from being
        # the requested episode, and standalone segments are still rejected -
        # neither behavior depended on the adblock layer.
        cam = "https://edge-hls.growcdnssedge.com/hls/242330696/240p/index.m3u8"
        self.assertFalse(APP._segment_shaped(cam))
        self.assertEqual(APP._numeric_stream_ids(cam), {"242330696"})
        candidates = [{"url": cam, "kind": "HLS", "score": 180}]
        widget_cluster = {"seen": True, "first_seq": 1}
        APP.mark_widget_candidates(
            candidates, {}, widget_cluster, set(), 2,
            cluster_now=False, widgets_still_visible=True,
        )
        self.assertIs(candidates[0]["user_started"], False)
        standalone = APP.rank_media_candidates((
            {"url": "https://cdn.example/seg_007.ts", "content_type": "video/mp2t"},
        ))
        self.assertEqual(standalone, [])

    def test_direct_failure_classification_unchanged_by_cleanup(self):
        # A transfer failure after media resolution is still NOT
        # browser-fallback-eligible; a Cloudflare page-access failure is.
        transfer_error = type(
            "E", (Exception,),
            {"category": "http", "output": "before_dl:__VRKA_TITLE__x"},
        )("transfer 403")
        self.assertFalse(APP.direct_failure_is_browser_recoverable(transfer_error))
        page_error = type(
            "E", (Exception,), {"category": "cloudflare", "output": ""}
        )("challenge")
        self.assertTrue(APP.direct_failure_is_browser_recoverable(page_error))
        # A bare Unsupported URL with no page-fetch evidence stays terminal.
        bare = type(
            "E", (Exception,), {"category": "unsupported", "output": ""}
        )("Unsupported URL")
        self.assertFalse(APP.direct_failure_is_browser_recoverable(bare))


if __name__ == "__main__":
    unittest.main()
