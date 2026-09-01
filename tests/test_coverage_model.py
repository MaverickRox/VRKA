"""Pure coverage-model regressions derived from the proven Anikoto evidence.

Fixture (authoritative manual-session facts):
- finite VOD playlist: 321 segments, ~4.6 s each, single muxed lineage;
- after initial playback: captured 0-11;
- after the manual ~50% seek: captured additionally 161-181;
- missing: 12-160 and 182-320 (288 segments).
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from vrka_core.coverage import (
    CoverageModel,
    model_from_urls,
    parse_playlist,
)

ANIKOTO_SEGMENT_COUNT = 321
ANIKOTO_SEGMENT_SECONDS = 4.6


def anikoto_playlist_text() -> str:
    lines = ["#EXTM3U"]
    for index in range(ANIKOTO_SEGMENT_COUNT):
        lines.append(f"#EXTINF:{ANIKOTO_SEGMENT_SECONDS:.3f},")
        lines.append(f"https://cdn.example/anime/vod/seg{index:04d}.ts?sig=abc")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines)


def anikoto_model(captured_ranges: list[tuple[int, int]]) -> CoverageModel:
    text = anikoto_playlist_text()
    times, urls, _total = parse_playlist(
            text, "https://cdn.example/anime/vod/index.m3u8")
    captured = set()
    for first, last in captured_ranges:
        for index in range(first, last + 1):
            captured.add(urls[index])
    return model_from_urls(times, urls, captured)


class AnikotoCoverageTests(unittest.TestCase):
    def test_playlist_parse_yields_321_indexed_segments(self):
        times, urls, _total = parse_playlist(
            anikoto_playlist_text(), "https://cdn.example/anime/vod/index.m3u8")
        self.assertEqual(len(times), 321)
        self.assertAlmostEqual(_total, 321 * 4.6, places=1)
        self.assertEqual(len(urls), 321)
        self.assertAlmostEqual(times[1], 4.6, places=2)
        # Signed query strings must not break index identity.
        self.assertTrue(urls[0].endswith(".ts"))

    def test_initial_playback_coverage(self):
        model = anikoto_model([(0, 11)])
        self.assertAlmostEqual(model.coverage_fraction(), 12 / 321)
        ranges = model.missing_ranges()
        self.assertEqual(len(ranges), 1)
        self.assertEqual((ranges[0].first, ranges[0].last), (12, 320))
        self.assertFalse(model.is_complete())
        self.assertAlmostEqual(model.next_target_time(),
                                12 * ANIKOTO_SEGMENT_SECONDS, places=2)

    def test_manual_seek_disjoint_windows(self):
        model = anikoto_model([(0, 11), (161, 181)])
        ranges = model.missing_ranges()
        self.assertEqual(
            [(r.first, r.last) for r in ranges], [(12, 160), (182, 320)])
        self.assertEqual(sum(r.length for r in ranges), 288)
        # First gap wins: the controller fills in assembly order.
        self.assertEqual(model.first_missing().first, 12)
        self.assertAlmostEqual(model.next_target_time(),
                                12 * ANIKOTO_SEGMENT_SECONDS, places=2)
        self.assertAlmostEqual(model.coverage_fraction(), 33 / 321, places=4)

    def test_fill_progress_and_completion(self):
        model = anikoto_model([(0, 11), (161, 181)])
        # Fill the first gap in two seeks.
        model.mark_range_captured(12, 90)
        self.assertEqual(model.first_missing().first, 91)
        model.mark_range_captured(91, 160)
        ranges = model.missing_ranges()
        self.assertEqual([(r.first, r.last) for r in ranges], [(182, 320)])
        # Fill the tail: completion only when EVERY segment exists.
        model.mark_range_captured(182, 320)
        self.assertTrue(model.is_complete())
        self.assertIsNone(model.next_target_time())
        self.assertEqual(model.missing_ranges(), [])

    def test_duplicates_and_out_of_order_arrival(self):
        model = anikoto_model([])
        for index in (5, 5, 1, 3, 1, 5):
            model.mark_captured(index)
        self.assertEqual(model.captured, {1, 3, 5})
        ranges = model.missing_ranges()
        self.assertEqual([(r.first, r.last) for r in ranges],
                         [(0, 0), (2, 2), (4, 4), (6, 320)])

    def test_largest_missing_region(self):
        model = anikoto_model([(0, 11), (161, 181)])
        largest = model.largest_missing()
        self.assertEqual((largest.first, largest.last, largest.length),
                         (12, 160, 149))

    def test_multiple_lineages_are_independent_models(self):
        times_a = [0.0, 4.0, 8.0]
        times_b = [0.0, 4.0, 8.0]
        urls_a = [f"https://cdn.test/a/seg{i}.ts" for i in range(3)]
        urls_b = [f"https://cdn.test/b/seg{i}.ts" for i in range(3)]
        lineage_a = model_from_urls(times_a, urls_a, {urls_a[0]})
        lineage_b = model_from_urls(times_b, urls_b, set())
        self.assertTrue(lineage_a.captured)
        self.assertFalse(lineage_b.captured)
        self.assertEqual(lineage_a.next_target_time(), 4.0)
        self.assertEqual(lineage_b.next_target_time(), 0.0)

    def test_ext_x_map_inserts_init_first(self):
        text = ("#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n"
                "#EXTINF:4.0,\nseg0.m4s\n#EXT-X-ENDLIST\n")
        times, urls, _total = parse_playlist(text, "https://cdn.test/hls/v.m3u8")
        self.assertEqual(urls[0], "https://cdn.test/hls/init.mp4")
        self.assertEqual(len(times), 2)


    def test_discontinuity_playlist_parses_and_tracks_flag(self):
        text = ("#EXTM3U\n#EXT-X-DISCONTINUITY\n"
                "#EXTINF:4.0,\nseg0.ts\n#EXT-X-DISCONTINUITY\n"
                "#EXTINF:4.0,\nseg1.ts\n#EXT-X-ENDLIST\n")
        times, urls, total = parse_playlist(text, "https://cdn.test/d/v.m3u8")
        self.assertEqual(len(urls), 2)
        self.assertAlmostEqual(total, 8.0, places=2)
        model = model_from_urls(times, urls, {urls[0]})
        self.assertEqual(model.next_target_time(), 4.0)
        # Discontinuity boundaries behave as ordinary segment boundaries:
        # coverage accounting is index-based and unaffected.
        model.mark_captured(1)
        self.assertTrue(model.is_complete())

    def test_finite_vod_endlist_detected(self):
        text = anikoto_playlist_text()
        self.assertIn("#EXT-X-ENDLIST", text)
        model = anikoto_model([(0, 320)])
        self.assertTrue(model.is_complete())

    def test_playlist_completeness_not_ffprobe_duration(self):
        """A file whose ffprobe duration matches the declared stream length
        must NOT count as complete when playlist segments are missing - the
        playlist-derived segment set is the only completeness evidence."""
        from vrka_core import assemble_browser_capture
        with tempfile.TemporaryDirectory() as tmp:
            objects = Path(tmp)
            body = b"S" * 2048
            import hashlib
            name = "obj-" + hashlib.sha256(body).hexdigest()[:16]
            (objects / name).write_bytes(body)
            # 3-segment playlist; only seg0 captured.  A naive fMP4 concat of
            # the captured portion can still carry the DECLARED duration.
            playlist = (b"#EXTM3U\n#EXTINF:4.0,\nseg0.m4s\n"
                        b"#EXTINF:4.0,\nseg1.m4s\n#EXTINF:4.0,\nseg2.m4s\n"
                        b"#EXT-X-ENDLIST\n")
            pname = "obj-" + hashlib.sha256(playlist).hexdigest()[:16]
            (objects / pname).write_bytes(playlist)
            manifest = [
                {"url": "https://p.test/s/v.m3u8", "status": 200,
                 "bytes": len(playlist),
                 "content_type": "application/vnd.apple.mpegurl",
                 "object": pname},
                {"url": "https://p.test/s/seg0.m4s", "status": 200,
                 "bytes": len(body), "content_type": "video/mp4",
                 "object": name},
            ]
            out = Path(tmp) / "out.bin"
            report = assemble_browser_capture(manifest, objects, out)
            # Playlist mode with missing segments must refuse completion
            # regardless of what ffprobe would report about the remainder.
            self.assertTrue(report.get("assembled"))
            self.assertEqual(report["segments"], 1)
            self.assertEqual(report["playlist_segments"], 3)
            self.assertEqual(len(report["missing"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
