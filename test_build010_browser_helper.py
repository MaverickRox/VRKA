"""The protected-browser capture pipeline must pass observations through to
ranking unfiltered: uBOL (not a Python layer) is the content filter.

These tests never touch the network; they run against the real module.
"""

import unittest

from test_vrka import APP


class CapturePipelineWithoutPythonFilterTests(unittest.TestCase):
    def test_observations_rank_without_any_vrka_filter_layer(self):
        # Every observed request is ranked as-is; nothing is rejected by a
        # VRKA ad/popup filter (there is none).  Media wins by type/context,
        # and media-shaped requests are never dropped.
        candidates = APP.rank_media_candidates((
            {"url": "https://doubleclick.net/tag.js", "source": "response"},
            {
                "url": "https://player.example/stream.m3u8",
                "content_type": "application/vnd.apple.mpegurl",
                "source": "dom",
                "playing": True,
            },
            {
                "url": "https://player.example/media.mp4",
                "content_type": "video/mp4",
                "source": "response",
            },
        ))
        urls = [item["url"] for item in candidates]
        self.assertIn("https://player.example/stream.m3u8", urls)
        self.assertIn("https://player.example/media.mp4", urls)
        # The tracker-shaped script is a zero-score non-media request: it is
        # not ranked (no score), but it is also NOT rejected by VRKA.
        self.assertNotIn("https://doubleclick.net/tag.js", urls)

    def test_helper_payload_no_longer_carries_filter_evidence_fields(self):
        source = APP.Path(APP.__file__).read_text(encoding="utf-8")
        helper = source[source.index("def run_browser_verification_helper"):]
        for marker in (
            "request_filter", "protection_snapshot_version",
            "filtered_nuisance_count", "resource_filter_installed",
            "resource_blocked_urls", "contained_overlay_count",
        ):
            self.assertNotIn(marker, helper, marker)
        # uBOL evidence IS carried (the single content filter).
        for marker in ("ubol_extension", "ubol_error", "ubol_dir"):
            self.assertIn(marker, helper, marker)


if __name__ == "__main__":
    unittest.main()
