"""Focused VRKA 2.0 regression coverage.

These tests avoid network access and exercise only the new runtime, command,
browser-session, media-candidate, audio, and migration boundaries.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock

from test_vrka import APP, APP_CLASS, standard_options, workspace_temporary_directory


def backend(source="managed", version="test"):
    return APP.YTDLPBackend(source, ("yt-dlp-test",), version, "yt-dlp-test")


class VRKA20RegressionTests(unittest.TestCase):
    def test_release_identity(self):
        self.assertEqual(APP.APP_VERSION, "3.0.0")
        self.assertEqual(APP.APP_BUILD, "011")
        self.assertEqual(APP.APP_DISPLAY_VERSION, "3.0.0")
        self.assertEqual(APP.APP_AUTHOR, "MVRK")

    def test_scrollbar_set_skips_redundant_redraw(self):
        """Identical fractional set() calls must not redraw the scrollbar.

        During window resize the scrollable frames re-emit identical
        fractions on every layout pass; redrawing each time was measured
        as ~34% of total resize CPU.  The patch must keep get() exact and
        redraw only when the position or widget size actually changes.
        """
        draws = []

        class _FakeScrollbar:
            _current_width = 16
            _current_height = 200
            _start_value = 0.0
            _end_value = 1.0

            def _draw(self, *args, **kwargs):
                draws.append((self._start_value, self._end_value))

        APP._patch_scrollbar_redundant_redraw(_FakeScrollbar)
        fake = _FakeScrollbar()
        fake.set(0.1, 0.4)
        fake.set(0.1, 0.4)
        self.assertEqual((fake._start_value, fake._end_value), (0.1, 0.4))
        self.assertEqual(len(draws), 1)
        fake._current_height = 180  # widget resized -> redraw required
        fake.set(0.1, 0.4)
        self.assertEqual(len(draws), 2)
        fake.set(0.25, 0.5)  # new position -> redraw required
        self.assertEqual(len(draws), 3)
        self.assertEqual(draws[-1], (0.25, 0.5))

    def test_canvas_pump_coalescing_defers_redundant_drains(self):
        """Per-draw update_idletasks pumps must coalesce to one per burst.

        CTk ends nearly every widget _draw with canvas.update_idletasks();
        during a resize burst that force-drained the whole pending geometry
        queue dozens of times per step (~10% of resize CPU).  The patch
        must defer duplicates to after_idle and still drain once.
        """
        drained = []
        scheduled = []

        class _FakeCanvas:
            def after_idle(self, callback):
                scheduled.append(callback)

            def update_idletasks(self):
                drained.append("pump")

        APP._patch_canvas_pump_coalescing(_FakeCanvas)
        canvas = _FakeCanvas()
        canvas.update_idletasks()
        canvas.update_idletasks()
        canvas.update_idletasks()
        self.assertEqual(drained, [])  # nothing ran synchronously
        self.assertEqual(len(scheduled), 1)  # exactly one deferred drain
        scheduled[0]()  # the natural idle point drains once
        self.assertEqual(drained, ["pump"])
        canvas.update_idletasks()  # next burst schedules again
        self.assertEqual(len(scheduled), 2)

    def test_mp3_bitrate_matrix_and_audio_wording(self):
        self.assertEqual(
            tuple(APP.MP3_BITRATE_MAP),
            ("320 kbps", "256 kbps", "192 kbps", "128 kbps"),
        )
        self.assertIn("Compressed audio", APP.AUDIO_FORMAT_DESCRIPTIONS["MP3 (Compressed)"])
        self.assertIn("very large file sizes", APP.AUDIO_FORMAT_DESCRIPTIONS["WAV (Uncompressed)"])
        self.assertIn(
            "does not restore lost quality",
            APP.AUDIO_FORMAT_DESCRIPTIONS["FLAC (Lossless container)"],
        )
        with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()), \
             mock.patch.object(APP, "get_bundled_ffmpeg_dir", return_value=None):
            for label, expected in APP.MP3_BITRATE_MAP.items():
                task = APP.DownloadTask(
                    "audio",
                    "https://example.test/audio",
                    "audio",
                    standard_options(
                        audio_format="MP3 (Compressed)",
                        mp3_bitrate=label,
                        embed_thumbnail=False,
                        embed_metadata=False,
                    ),
                )
                _selected, command = APP.build_standard_ytdlp_command(task, "C:/out")
                self.assertIn("--audio-format", command)
                self.assertIn("mp3", command)
                quality_index = command.index("--audio-quality")
                self.assertEqual(command[quality_index + 1], expected)

    def test_playlist_range_trim_and_archive_reach_central_builder(self):
        task = APP.DownloadTask(
            "playlist",
            "https://example.test/list",
            "audio",
            standard_options(
                audio_format="MP3 (Compressed)",
                is_playlist=True,
                playlist_start="2",
                playlist_end="4",
                use_archive=True,
            ),
        )
        with workspace_temporary_directory() as tmpdir, \
             mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()), \
             mock.patch.object(APP, "get_bundled_ffmpeg_dir", return_value=None):
            _selected, command = APP.build_standard_ytdlp_command(
                task, tmpdir, download_section="*3-8"
            )
            self.assertEqual(command[command.index("--playlist-start") + 1], "2")
            self.assertEqual(command[command.index("--playlist-end") + 1], "4")
            self.assertIn("--download-sections", command)
            archive = command[command.index("--download-archive") + 1]
            self.assertTrue(archive.endswith(APP.VRKA_ARCHIVE_FILENAME))
            self.assertNotIn("seal_archive.txt", archive.lower())

    def test_browser_cookie_modes_and_profiles(self):
        cases = (
            ("none", {}, ()),
            (
                "browser",
                {"cookie_browser": "Brave", "cookie_profile": "Profile 2"},
                ("--cookies-from-browser", "brave:Profile 2"),
            ),
            (
                "file",
                {"cookie_file": "C:/private/cookies.txt"},
                ("--cookies", "C:/private/cookies.txt"),
            ),
            (
                "session",
                {
                    "session_cookie_file": "C:/private/session.txt",
                    "session_user_agent": "VRKA-Agent",
                    "session_referer": "https://example.test/page",
                    "session_origin": "https://example.test",
                },
                ("--cookies", "C:/private/session.txt"),
            ),
        )
        with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()), \
             mock.patch.object(APP, "get_bundled_ffmpeg_dir", return_value=None):
            for mode, overrides, expected in cases:
                task = APP.DownloadTask(
                    mode,
                    "https://example.test/media",
                    "video",
                    standard_options(cookie_mode=mode, **overrides),
                )
                _selected, command = APP.build_standard_ytdlp_command(task, "C:/out")
                if expected:
                    option, value = expected
                    self.assertEqual(command[command.index(option) + 1], value)
                else:
                    self.assertNotIn("--cookies", command)
                    self.assertNotIn("--cookies-from-browser", command)

    def test_cookie_settings_migration(self):
        migrated, changed = APP.migrate_cookie_settings(
            {"cookie_mode": "From Browser", "cookie_browser": "firefox"}
        )
        self.assertTrue(changed)
        self.assertEqual(migrated["cookie_mode"], "Selected Browser")
        self.assertEqual(migrated["cookie_browser"], "Firefox")
        self.assertEqual(migrated["cookie_profile"], "")
        session, changed = APP.migrate_cookie_settings(
            {"cookie_mode": "Verified Session", "cookie_browser": "Chrome"}
        )
        self.assertTrue(changed)
        self.assertEqual(session["cookie_mode"], "Disabled")

    def test_cookie_and_authentication_failures_are_classified_without_secrets(self):
        for message in (
            "ERROR: could not copy Chrome cookie database: database is locked",
            "ERROR: failed to decrypt browser cookies",
            "ERROR: sign in to confirm you are not a bot",
        ):
            self.assertEqual(APP.classify_download_error(message), "cookies")
        self.assertEqual(APP.classify_download_error("HTTP Error 403: Forbidden"), "http")
        self.assertEqual(APP.classify_download_error("Unsupported URL"), "unsupported")
        rendered = APP.sanitize_command_for_log(
            [
                "yt-dlp",
                "--cookies",
                "C:/private/cookies.txt",
                "--password=hidden",
                "https://example.test",
            ]
        )
        self.assertNotIn("private", rendered)
        self.assertNotIn("hidden", rendered)

    def test_direct_failure_fallback_eligibility_classification(self):
        """Generic recovery classification for the fast-failure fallback path:
        page-accessible categories are eligible; a bare Unsupported URL, DRM
        and impersonation-mechanism errors stay terminal; an Unsupported URL
        that followed a browser-relevant first error (the observed Cloudflare
        403 -> impersonation retry -> Unsupported URL chain) is eligible."""
        for category in ("cloudflare", "cookies", "expired", "http"):
            exc = APP.YTDLPCommandError("boom", category=category)
            self.assertTrue(
                APP.direct_failure_is_browser_recoverable(exc),
                f"{category} should be browser-recoverable",
            )
        for category in ("drm", "impersonation"):
            exc = APP.YTDLPCommandError("boom", category=category)
            self.assertFalse(
                APP.direct_failure_is_browser_recoverable(exc),
                f"{category} should be terminal",
            )
        bare = APP.YTDLPCommandError("Unsupported URL", category="unsupported")
        self.assertFalse(APP.direct_failure_is_browser_recoverable(bare))
        # The session-variable site case: the generic extractor actually
        # fetched/parsed the (JS-driven) page before reporting Unsupported
        # URL - that is browser-recoverable even without a prior 403.
        fetched = APP.YTDLPCommandError(
            "Unsupported URL", category="unsupported",
            output=(
                "WARNING: [generic] Falling back on generic information extractor\n"
                "ERROR: Unsupported URL: https://jav.guru/1035117/..."
            ),
        )
        self.assertTrue(APP.direct_failure_is_browser_recoverable(fetched))
        fetched2 = APP.YTDLPCommandError(
            "Unsupported URL", category="unsupported",
            output=(
                "[generic] Downloading webpage\n[generic] Extracting information\n"
                "ERROR: Unsupported URL: https://example.test/js-only-page/"
            ),
        )
        self.assertTrue(APP.direct_failure_is_browser_recoverable(fetched2))
        invalid = APP.YTDLPCommandError(
            "Unsupported URL", category="unsupported",
            output="ERROR: [generic] not a valid URL: garbage",
        )
        self.assertFalse(APP.direct_failure_is_browser_recoverable(invalid))
        unknown = APP.YTDLPCommandError("opaque failure", category="unknown")
        self.assertFalse(APP.direct_failure_is_browser_recoverable(unknown))
        chained = APP.YTDLPCommandError(
            "Unsupported URL", category="unsupported",
            prior_categories=("http",),
        )
        self.assertTrue(APP.direct_failure_is_browser_recoverable(chained))
        chained_cloudflare = APP.YTDLPCommandError(
            "Unsupported URL", category="unsupported",
            prior_categories=("cloudflare",),
        )
        self.assertTrue(APP.direct_failure_is_browser_recoverable(chained_cloudflare))
        self.assertFalse(
            APP.direct_failure_is_browser_recoverable(
                APP.YTDLPCommandError(
                    "drm", category="drm", prior_categories=("http",),
                )
            )
        )

    def test_transfer_failure_after_resolution_is_not_browser_recoverable(self):
        """Regression A: a direct run that already resolved the requested media
        and began a real transfer (before_dl / Destination markers) must NOT
        route to Browser Fallback when the media CDN later rejects it (e.g. a
        transient HTTP 403 on YouTube).  Only genuine page/extraction failures
        are fallback-eligible."""
        # YouTube-style: extraction succeeded (before_dl fired), the media CDN
        # then returned HTTP 403 -> stays on the direct path (terminal with
        # Retry, never an automatic protected browser).
        youtube_transfer_403 = APP.YTDLPCommandError(
            "HTTP Error 403: Forbidden", category="http",
            output=(
                "[youtube] bv56jWJg6Lw: Downloading webpage\n"
                "[youtube] bv56jWJg6Lw: Downloading android player API JSON\n"
                "__VRKA_TITLE__Some Video Title\n"
                "[download] Destination: Some Video Title.mp4\n"
                "ERROR: unable to download video data: HTTP Error 403: Forbidden"
            ),
        )
        self.assertFalse(
            APP.direct_failure_is_browser_recoverable(youtube_transfer_403)
        )
        # Same via the Destination-only marker (ffmpeg/HLS paths).
        hls_transfer_403 = APP.YTDLPCommandError(
            "HTTP Error 403: Forbidden", category="http",
            output=(
                "[generic] Downloading m3u8 manifest\n"
                "[download] Destination: episode.m3u8\n"
                "ERROR: unable to download video data: HTTP Error 403: Forbidden"
            ),
        )
        self.assertFalse(APP.direct_failure_is_browser_recoverable(hls_transfer_403))
        # JAV.GURU-style: Cloudflare 403 at EXTRACTION time (no before_dl / no
        # Destination) remains browser-recoverable.
        extraction_403 = APP.YTDLPCommandError(
            "Unable to download webpage: HTTP Error 403: Forbidden",
            category="http",
            output=(
                "WARNING: [generic] Falling back on generic information extractor\n"
                "ERROR: Unable to download webpage: HTTP Error 403: Forbidden"
            ),
        )
        self.assertTrue(APP.direct_failure_is_browser_recoverable(extraction_403))

    def test_media_candidate_filtering_and_labels(self):
        candidates = APP.filter_media_candidates(
            [
                "https://cdn.example/video/master.m3u8?token=opaque",
                "https://cdn.example/video/master.m3u8?token=opaque",
                "https://ads.example/ads/tracker.mp4",
                "https://cdn.example/poster.jpg",
                "https://cdn.example/audio.m4a",
                "javascript:void(0)",
            ]
        )
        # Nothing is rejected by a VRKA filter layer (uBOL owns that); the
        # ad-host mp4 remains a ranked Video candidate.
        self.assertEqual(len(candidates), 3)
        self.assertIn("HLS", APP.media_candidate_label(1, candidates[0]))
        self.assertIn("Video", APP.media_candidate_label(2, candidates[1]))
        self.assertIn("Audio", APP.media_candidate_label(3, candidates[2]))

    def test_hls_segment_shaped_mp4s_collapse_into_their_manifest(self):
        manifest = (
            "https://media.example/hls/254304457/254304457_240p.m3u8"
            "?psch=v2&pkey=opaque"
        )
        ranked = APP.rank_media_candidates(
            [
                {"url": manifest, "source": "response",
                 "content_type": "application/vnd.apple.mpegurl"},
                {"url": (
                    "https://media.example/hls/254304457/"
                    "254304457_240p_h264_164_abcdefgh_1786715515.mp4"
                 ), "source": "response", "content_type": "video/mp4",
                 "content_length": 156641},
                {"url": (
                    "https://media.example/hls/254304457/"
                    "254304457_240p_h264_165_ijklmnop_1786715517.mp4"
                 ), "source": "response", "content_type": "video/mp4",
                 "content_length": 144832},
                {"url": "https://media.example/hls/254304457/standalone.mp4",
                 "source": "response", "content_type": "video/mp4"},
            ]
        )
        segments = [c for c in ranked if c.get("segment_parent_url")]
        self.assertEqual(len(segments), 2)
        self.assertTrue(all(c["segment_parent_url"] == manifest for c in segments))
        self.assertEqual(ranked[0]["url"].split("?")[0], manifest.split("?")[0])
        self.assertEqual(ranked[0]["kind"], "HLS")
        self.assertEqual(segments[0]["kind"], "Segment")
        self.assertTrue(segments[0]["score"] < ranked[0]["score"])
        self.assertEqual(
            [c["url"] for c in ranked if not c.get("segment_parent_url")],
            [manifest, "https://media.example/hls/254304457/standalone.mp4"],
        )

    def test_codec_less_sequence_segment_collapses_into_manifest_or_is_dropped(self):
        # Real production defect: an HLS segment with no codec marker was
        # validated as a standalone video and transferred.
        segment = (
            "https://media-hls.growcdnssedge.com/b-hls-06/259842905/"
            "259842905_182_aXlQBs8rLfOQKLeT_1786781931.mp4"
        )
        manifest = (
            "https://media-hls.growcdnssedge.com/b-hls-06/259842905/"
            "259842905_240p.m3u8"
        )
        # Without an observed manifest the segment must never become a
        # standalone candidate.
        alone = APP.rank_media_candidates(
            [{"url": segment, "source": "response",
              "content_type": "video/mp4", "content_length": 156641}]
        )
        self.assertEqual(alone, [])
        # With its manifest observed, it collapses under the parent and ranks
        # below every standalone candidate.
        ranked = APP.rank_media_candidates(
            [
                {"url": manifest, "source": "response",
                 "content_type": "application/vnd.apple.mpegurl"},
                {"url": segment, "source": "response",
                 "content_type": "video/mp4", "content_length": 156641},
                {"url": (
                    "https://media-hls.growcdnssedge.com/b-hls-06/259842905/"
                    "259842905_240p_h264_init_hLE2qqmbhXIgFFQ3.mp4"
                 ), "source": "response", "content_type": "video/mp4"},
                {"url": "https://cdn.example.com/movie_2024_1080p.mp4",
                 "source": "response", "content_type": "video/mp4"},
            ]
        )
        segment_items = [c for c in ranked if c.get("segment_parent_url")]
        self.assertEqual(len(segment_items), 2)
        self.assertTrue(all(c["segment_parent_url"] == manifest for c in segment_items))
        self.assertTrue(all(c["kind"] == "Segment" for c in segment_items))
        self.assertEqual(ranked[0]["url"], manifest)
        self.assertIn("movie_2024_1080p.mp4", ranked[1]["url"])

    def test_numeric_name_files_are_not_segments(self):
        # Ordinary filenames containing years/resolutions/suffixes must never
        # be classified as HLS segments in either classification layer.
        for url in (
            "https://cdn.example.com/movie_2024_1080p.mp4",
            "https://cdn.example.com/party_2015_trailer.mp4",
            "https://cdn.example.com/video_123.mp4",
            "https://cdn.example.com/clip_2.mp4",
        ):
            self.assertFalse(APP._segment_shaped(url), url)
        from vrka_core.candidates import is_segment
        for url in (
            "https://cdn.example.com/movie_2024_1080p.mp4",
            "https://cdn.example.com/party_2015_trailer.mp4",
            "https://cdn.example.com/video_123.mp4",
        ):
            self.assertFalse(is_segment(url), url)
        # Genuine segment shapes are still recognized in both layers.
        for url in (
            "https://cdn.example.com/name_2_ab12cd_9876543210.mp4",
            "https://media-hls.growcdnssedge.com/b-hls-06/171550991/"
            "171550991_240p_h264_100_y84KLSLGP8cIpmND_1786726372.mp4",
            "https://media-hls.growcdnssedge.com/b-hls-06/171550991/"
            "171550991_240p_h264_init_4Vz42kAnoxkqDNNk.mp4",
        ):
            self.assertTrue(APP._segment_shaped(url), url)
            self.assertTrue(is_segment(url), url)

    def test_widget_cams_re_rendered_after_interaction_stay_marked(self):
        # Real defect: after selecting a server tab, the sidebar cams
        # re-render with NEW numeric stream ids while a large player iframe is
        # present.  Previously collection stopped (cluster_now required "no
        # large player"), so the new cams escaped as user_started and ended
        # the interaction wait early - 225808613_240p was validated before
        # Play.  The widget signature (2+ small videos) must keep collection
        # active, while the requested player's stream (no numeric id) stays
        # user_started.
        first_seen_seq = {}
        widget_cluster = {"seen": True, "first_seq": 1}
        widget_ids = set()
        c1 = [{"url": (
            "https://media-hls.growcdnssedge.com/b-hls-06/225371326/"
            "225371326_240p.m3u8")}]
        widget_ids = APP.mark_widget_candidates(
            c1, first_seen_seq, widget_cluster, widget_ids, 1, True, True)
        self.assertIs(c1[0]["user_started"], False)
        # After the server click: new cams, large player present
        # (cluster_now=False), small videos still visible.
        c2 = [
            {"url": (
                "https://media-hls.growcdnssedge.com/b-hls-11/150627772/"
                "150627772_240p.m3u8")},
            {"url": (
                "https://media-hls.growcdnssedge.com/b-hls-11/225808613/"
                "225808613_240p.m3u8?psch=v2&pkey=NTK9aqcLmNFMWrpQ")},
        ]
        widget_ids = APP.mark_widget_candidates(
            c2, first_seen_seq, widget_cluster, widget_ids, 2, False, True)
        for item in c2:
            self.assertIs(item["user_started"], False, item["url"])
        self.assertEqual(widget_ids, {"225371326", "150627772", "225808613"})
        # The requested episode master has no numeric stream id: stays
        # user_started even while the cams are still visible.
        c3 = [{"url": "https://javclan.com/stream/okZDaRbB06zoatA/master.m3u8"}]
        APP.mark_widget_candidates(
            c3, first_seen_seq, widget_cluster, widget_ids, 3, False, True)
        self.assertIsNot(c3[0].get("user_started"), False)

    def test_requested_master_not_marked_as_widget_and_impersonation_propagates(self):
        # Real MISSAV defect: the requested episode's generic master manifest
        # (surrit.com/.../playlist.m3u8) was observed in the FIRST snapshot
        # alongside the sidebar cams and the blanket ``first<=first_seq`` rule
        # marked it as a widget, so the cams (which escaped marking) outranked
        # it.  A master manifest must never be widget-marked; numeric-ID cam
        # URLs must still be marked.
        from vrka_downloader import mark_widget_candidates
        first_seen_seq = {}
        widget_cluster = {"seen": True, "first_seq": 1}
        widget_ids = set()
        candidates = [
            {"url": (
                "https://surrit.com/ab8ab156-8717-4094-bb2b-79d4e6ed396d/"
                "playlist.m3u8")},
            {"url": (
                "https://edge-hls.growcdnssedge.com/hls/200459374/master/"
                "200459374_240p.m3u8")},
        ]
        mark_widget_candidates(candidates, first_seen_seq, widget_cluster,
                               widget_ids, 1, True, True)
        self.assertIsNot(candidates[0].get("user_started"), False,
                         "requested master must not be a widget")
        self.assertIs(candidates[1]["user_started"], False,
                      "numeric-ID cam must be a widget")
        # The validation probe and transfer command reproduce the Chrome
        # request-impersonation context the direct path discovered, so a
        # Cloudflare-protected requested-media CDN validates instead of 403.
        class Task:
            mode = "video"
            url = "https://surrit.com/x/playlist.m3u8"
            options = {"cookie_mode": "none", "impersonation": "Automatic",
                       "_needs_impersonation": "chrome"}
        _backend, probe = APP.build_candidate_probe_command(
            Task(), {"url": Task.url})
        self.assertIn("--impersonate", probe)
        self.assertIn("--extractor-args", probe)
        transfer = APP._standard_ytdlp_arguments(Task(), "out")
        self.assertIn("--impersonate", transfer)
        self.assertIn("--extractor-args", transfer)
        # Without the discovery flag, Automatic impersonation stays untouched.
        class PlainTask:
            mode = "video"
            url = "https://surrit.com/x/playlist.m3u8"
            options = {"cookie_mode": "none", "impersonation": "Automatic"}
        _backend, probe = APP.build_candidate_probe_command(
            PlainTask(), {"url": PlainTask.url})
        self.assertNotIn("--impersonate", probe)

    def test_master_manifest_preferred_over_variant_and_widget(self):
        # A generic master/rendition-selector manifest must be preferred over
        # (a) a specific variant playlist observed first and (b) a low-quality
        # numeric widget stream, in both classification layers, so the normal
        # downloader can pick the best available quality.
        master = "https://javclan.com/stream/okZDaRbB06zoatA/master.m3u8"
        variant = "https://javclan.com/stream/okZDaRbB06zoatA/index-f1-v1-a1.m3u8"
        widget = "https://media-hls.growcdnssedge.com/x/225371326_240p.m3u8"
        records = [
            {"url": variant, "content_type": "application/vnd.apple.mpegurl"},
            {"url": widget, "content_type": "application/vnd.apple.mpegurl"},
            {"url": master, "content_type": "application/vnd.apple.mpegurl"},
        ]
        ranked = APP.rank_media_candidates(records)
        urls = [c["url"] for c in ranked]
        self.assertEqual(urls[0], master)
        self.assertLess(urls.index(variant), urls.index(widget))
        from vrka_core.candidates import (
            CandidateKind,
            CandidateRanker,
            MediaCandidate,
            canonical_media_identity,
        )
        now = 1000.0
        candidates = [
            MediaCandidate(
                candidate_id=canonical_media_identity(master, CandidateKind.HLS),
                canonical_identity=canonical_media_identity(master, CandidateKind.HLS),
                current_url=master, kind=CandidateKind.HLS,
                first_seen=now, request_count=2),
            MediaCandidate(
                candidate_id=canonical_media_identity(variant, CandidateKind.HLS),
                canonical_identity=canonical_media_identity(variant, CandidateKind.HLS),
                current_url=variant, kind=CandidateKind.HLS,
                first_seen=now, request_count=2),
            MediaCandidate(
                candidate_id=canonical_media_identity(widget, CandidateKind.HLS),
                canonical_identity=canonical_media_identity(widget, CandidateKind.HLS),
                current_url=widget, kind=CandidateKind.HLS,
                first_seen=now, request_count=2),
        ]
        for item in candidates:
            item.segment_count = 2
        decision = CandidateRanker().decide(candidates, now=now)
        ranked_ids = [r.candidate_id for r in decision.ranked]
        master_id = canonical_media_identity(master, CandidateKind.HLS)
        variant_id = canonical_media_identity(variant, CandidateKind.HLS)
        widget_id = canonical_media_identity(widget, CandidateKind.HLS)
        self.assertEqual(ranked_ids[0], master_id)
        self.assertLess(ranked_ids.index(variant_id), ranked_ids.index(widget_id))
        # The automatic fallback advances through the ranked list on
        # ambiguity, so the master is the effective selection target.
        self.assertTrue(
            any("master manifest" in reason for reason in
                next(r for r in decision.ranked if r.candidate_id == master_id).reasons),
            decision.ranked,
        )
        self.assertGreater(
            next(r.score for r in decision.ranked if r.candidate_id == master_id),
            next(r.score for r in decision.ranked if r.candidate_id == variant_id),
        )

    def test_browser_request_ranking_keeps_media_context_without_vrka_ad_rejection(self):
        manifest = "https://media.example/video/master.m3u8?signature=opaque"
        ranked = APP.rank_media_candidates(
            [
                {
                    "url": manifest,
                    "source": "request",
                    "headers": {
                        "Origin": "https://player.example",
                        "User-Agent": "VRKA-Test",
                        "X-Video-Token": "sensitive-token",
                        "Cookie": "must-not-be-copied",
                    },
                },
                {
                    "url": manifest,
                    "source": "response",
                    "status": 200,
                    "content_type": "application/vnd.apple.mpegurl",
                },
                {
                    "url": "https://cdn.havenclick.com/ad/preroll.mp4",
                    "content_type": "video/mp4",
                },
                {
                    "url": "https://ads.example/ads/tracker.mp4",
                    "content_type": "video/mp4",
                },
            ]
        )
        # VRKA no longer rejects ad-host media: uBOL is the content filter and
        # the candidate pipeline must keep every real media request intact
        # (a playable mp4 from an ad host is legitimate media until proven
        # otherwise; the requested manifest simply outranks it).
        self.assertEqual(len(ranked), 3)
        self.assertEqual(ranked[0]["url"], manifest)
        self.assertEqual(ranked[0]["kind"], "HLS")
        self.assertEqual(ranked[0]["headers"]["X-Video-Token"], "sensitive-token")
        self.assertNotIn("Cookie", ranked[0]["headers"])
        kinds = {item["kind"] for item in ranked[1:]}
        self.assertEqual(kinds, {"Video"})

    def test_candidate_specific_headers_are_handed_off_but_redacted_from_logs(self):
        candidate = {
            "url": "https://media.example/master.m3u8",
            "kind": "HLS",
            "headers": {
                "Origin": "https://player.example",
                "X-Video-Token": "sensitive-token",
            },
        }
        task = APP.DownloadTask(
            "candidate-headers",
            "https://example.test/page",
            "video",
            standard_options(cookie_mode="session"),
        )
        with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()):
            _selected, command = APP.build_candidate_probe_command(task, candidate)
        self.assertIn("Origin:https://player.example", command)
        self.assertIn("X-Video-Token:sensitive-token", command)
        rendered = APP.sanitize_command_for_log(command)
        self.assertNotIn("sensitive-token", rendered)
        self.assertIn("X-Video-Token:<redacted>", rendered)
    def test_candidate_probe_preserves_required_session_headers(self):
        task = APP.DownloadTask(
            "candidate",
            "https://example.test/page",
            "video",
            standard_options(
                cookie_mode="session",
                session_cookie_file="C:/private/session.txt",
                session_user_agent="Agent/1",
                session_referer="https://example.test/page",
                session_origin="https://example.test",
            ),
        )
        with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()):
            _selected, command = APP.build_candidate_probe_command(
                task, "https://cdn.example/master.m3u8"
            )
        self.assertIn("--simulate", command)
        self.assertEqual(
            command[command.index("--cookies") + 1], "C:/private/session.txt"
        )
        self.assertEqual(command[command.index("--user-agent") + 1], "Agent/1")
        self.assertEqual(
            command[command.index("--referer") + 1],
            "https://example.test/page",
        )
        self.assertIn("Origin:https://example.test", command)

    def test_candidate_probe_detects_drm_and_accepts_valid_media(self):
        worker = object.__new__(APP_CLASS)
        task = APP.DownloadTask(
            "candidate", "https://example.test/page", "video", standard_options()
        )
        def probe_process(output, code):
            class ProbeProcess:
                def __init__(self, command, **_kwargs):
                    self.command = command
                    self.stdout = io.StringIO(output)
                    self.returncode = code
                    self.pid = 4242

                def poll(self):
                    return self.returncode

                def wait(self):
                    return self.returncode

                def terminate(self):
                    self.returncode = 1

            return ProbeProcess

        with mock.patch.object(
            APP,
            "build_candidate_probe_command",
            return_value=(backend(), ["yt-dlp"]),
        ), mock.patch.object(
            APP.subprocess, "Popen", probe_process(
                "__VRKA_CANDIDATE__hls-720|1280x720|mp4|2500|aac|avc1|720\n", 0,
            )
        ):
            self.assertTrue(
                APP_CLASS._validate_media_candidate(
                    worker,
                    task,
                    "https://cdn.example/master.m3u8",
                    threading.Event(),
                )
            )
        with mock.patch.object(
            APP,
            "build_candidate_probe_command",
            return_value=(backend(), ["yt-dlp"]),
        ), mock.patch.object(
            APP.subprocess, "Popen", probe_process(
                "ERROR: This video is DRM protected\n", 1,
            )
        ):
            with self.assertRaises(APP.YTDLPCommandError) as raised:
                APP_CLASS._validate_media_candidate(
                    worker,
                    task,
                    "https://cdn.example/manifest.mpd",
                    threading.Event(),
                )
        self.assertEqual(raised.exception.category, "drm")

    def test_session_guard_blocks_all_new_windows_without_ad_classification(self):
        # The native layer is a security firewall, not an adblocker: every
        # page-created window is marked handled (never handed to the user's
        # browser), and what is ad traffic is uBOL's decision.  No VRKA host
        # lists, popup policies, resource filters, or DOM cosmetics remain.
        source = Path(APP.__file__).read_text(encoding="utf-8")
        guard = source[source.index("def install_webview2_session_guard"):]
        self.assertIn("args.set_Handled(True)", guard)
        self.assertIn("NewWindowRequested -= browser.on_new_window_request", source)
        self.assertIn("browser_view.Invoke(Action(install_on_ui_thread))", source)
        for removed in (
            "popup_navigation_policy",
            "should_block_top_level_navigation",
            "is_hostile_resource_request",
            "is_unsolicited_popup_host",
            "UNSOLICITED_POPUP_HOST_SUFFIXES",
            "OBVIOUS_TRACKER_HOST_SUFFIXES",
            "AddWebResourceRequestedFilter",
            "CreateWebResourceResponse",
            "resource_blocked",
            "mayzaent",
            "ruddy-pass",
            "pemsrv",
            "__vrkaNuisanceHidden",
            "smallCornerVideos",
            "hasFrame",
            "attributeFilter: ['src']",
            "containsVideo(n) && !insideVideo(n)",
        ):
            self.assertNotIn(removed, source, removed)
        # The live regression instrumentation remains (evidence, not blocking).
        for marker in ("navigation_log", "blocked_popup_urls",
                       "contained_popup_urls", "blocked_navigation_urls",
                       "player_state", "interactive_elements", "dom_overlays",
                       "autoplay_widget_page", "first_seen_seq", "view_size"):
            self.assertIn(marker, source)
        # Autoplay-widget-cluster lineage is unchanged (media identity, not
        # adblocking): widget cams stay marked and cannot become the episode.
        self.assertIn('item["user_started"] = False', source)
        self.assertIn('"autoplay_widget_page"', source)

    def test_ubol_installed_before_first_target_navigation(self):
        # The protected browser must behave like Chrome + uBOL: the window
        # opens blank, uBOL is installed into the profile first, and only then
        # is the requested page navigated to (no post-install reload needed).
        source = Path(APP.__file__).read_text(encoding="utf-8")
        helper = source[source.index("def run_browser_verification_helper"):]
        self.assertIn('url="about:blank"', helper)
        self.assertNotIn("url=start_url", helper)
        self.assertIn("ubol_ready.wait(timeout=60)", helper)
        self.assertIn("AddBrowserExtensionAsync", helper)
        self.assertIn("window.load_url(start_url)", helper)
        self.assertLess(
            helper.index("AddBrowserExtensionAsync"),
            helper.index("window.load_url(start_url)"),
        )
        # The requested page is navigated to from the guarded session thread
        # after uBOL readiness (never at window creation), so no post-install
        # reload is needed.
        gate = helper[helper.index("def install_session_guard_when_ready"):]
        self.assertIn("ubol_ready.wait(timeout=60)", gate)
        self.assertIn("navigate_to_requested_page()", gate)
        self.assertLess(
            gate.index("ubol_ready.wait(timeout=60)"),
            gate.index("navigate_to_requested_page()"),
        )
        # The guard keeps the uBOL install evidence in the payload.
        self.assertIn("ubol_extension", helper)
        self.assertIn("ubol_error", helper)
    def test_stale_verified_candidate_offers_fresh_browser_session(self):
        stale = standard_options(
            cookie_mode="session",
            session_media_candidates=[{"url": "https://media.example/master.m3u8"}],
        )
        self.assertTrue(APP.should_offer_browser_verification(stale, "expired"))
        self.assertTrue(APP.should_offer_browser_verification(stale, "http"))
        self.assertFalse(
            APP.should_offer_browser_verification(
                standard_options(cookie_mode="session", session_media_candidates=[]),
                "expired",
            )
        )
        self.assertTrue(
            APP.should_offer_browser_verification(
                standard_options(cookie_mode="none"), "unsupported"
            )
        )

    def test_generic_manifest_title_gets_safe_collision_resistant_fallback(self):
        candidate = {
            "url": "https://media.example/video/master.m3u8?token=opaque",
            "probe_title": "master",
        }
        self.assertTrue(APP.candidate_needs_fallback_title(candidate))
        title = APP.browser_fallback_title(
            "Example: Episode / One", "https://example.test/watch", "20260727-120000"
        )
        self.assertEqual(title, "Example Episode One - 20260727-120000")
        task = APP.DownloadTask(
            "generic-title",
            "https://example.test/watch",
            "video",
            standard_options(
                resolved_media_url=candidate["url"],
                resolved_media_title=title,
            ),
        )
        with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()):
            _selected, command = APP.build_standard_ytdlp_command(task, "C:/output")
        template = command[command.index("-o") + 1]
        self.assertIn(title, template)
        self.assertNotIn("%(title)s", template)

    def test_managed_runtime_selection_and_corrupt_fallback(self):
        with workspace_temporary_directory() as tmpdir:
            runtime = Path(tmpdir)
            active = runtime / ("yt-dlp.exe" if APP.os.name == "nt" else "yt-dlp")
            active.write_bytes(b"MZ" + b"x" * 20)
            with mock.patch.object(APP, "RUNTIME_DIR", runtime), mock.patch.object(
                APP,
                "validate_ytdlp_binary",
                return_value=(True, "2026.07.26", ""),
            ):
                selected = APP.resolve_ytdlp_backend()
                self.assertEqual(selected.source, "managed")
                self.assertEqual(selected.version, "2026.07.26")
            with mock.patch.object(APP, "RUNTIME_DIR", runtime), mock.patch.object(
                APP,
                "validate_ytdlp_binary",
                return_value=(False, "", "corrupt"),
            ):
                selected = APP.resolve_ytdlp_backend()
                self.assertEqual(selected.source, "bundled")

    def test_updater_uses_official_stable_and_nightly_sources(self):
        self.assertEqual(
            APP._github_release_api("Stable"),
            "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
        )
        self.assertEqual(
            APP._github_release_api("Nightly"),
            "https://api.github.com/repos/yt-dlp/yt-dlp-nightly-builds/releases/latest",
        )

    def test_atomic_updater_activation_and_previous_runtime(self):
        with workspace_temporary_directory() as tmpdir:
            runtime = Path(tmpdir)
            paths = {
                "active": runtime / "yt-dlp.exe",
                "previous": runtime / "yt-dlp.previous.exe",
                "download": runtime / ".yt-dlp.download.exe",
            }
            old_payload = b"old-runtime"
            new_payload = b"MZ" + b"n" * 1_100_000
            paths["active"].write_bytes(old_payload)
            digest = hashlib.sha256(new_payload).hexdigest()
            release = {
                "channel": "Stable",
                "version": "2026.07.26",
                "binary_name": "yt-dlp.exe",
                "binary_url": "https://official.example/yt-dlp.exe",
                "checksum_url": "https://official.example/SHA2-256SUMS",
                "release_url": "https://official.example/release",
            }

            def download(_url, destination):
                Path(destination).write_bytes(new_payload)
                return len(new_payload)

            with mock.patch.object(APP, "RUNTIME_DIR", runtime), \
                 mock.patch.object(APP, "_runtime_paths", return_value=paths), \
                 mock.patch.object(APP, "fetch_ytdlp_release", return_value=release), \
                 mock.patch.object(
                     APP,
                     "_read_url",
                     return_value=(f"{digest}  yt-dlp.exe\n".encode(), {}),
                 ), \
                 mock.patch.object(APP, "_download_binary", side_effect=download), \
                 mock.patch.object(
                     APP,
                     "validate_ytdlp_binary",
                     return_value=(True, "2026.07.26", ""),
                 ), \
                 mock.patch.object(APP, "_save_runtime_state"):
                result = APP.install_ytdlp_update("Stable")
            self.assertEqual(result["status"], "installed")
            self.assertEqual(paths["active"].read_bytes(), new_payload)
            self.assertEqual(paths["previous"].read_bytes(), old_payload)

    def test_archive_migration_merges_without_deleting_legacy(self):
        with workspace_temporary_directory() as tmpdir:
            folder = Path(tmpdir)
            legacy = folder / "seal_archive.txt"
            legacy.write_text(
                "youtube old-one\nyoutube duplicate\n", encoding="utf-8"
            )
            target = folder / APP.VRKA_ARCHIVE_FILENAME
            target.write_text(
                "youtube duplicate\nyoutube vrka-only\n", encoding="utf-8"
            )
            migrated, sources = APP.migrate_download_archive(folder)
            records = migrated.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                records,
                ["youtube duplicate", "youtube vrka-only", "youtube old-one"],
            )
            self.assertIn("seal_archive.txt", sources)
            self.assertTrue(legacy.exists())
            self.assertTrue(
                (folder / "seal_archive.txt.vrka-migration-backup").exists()
            )

    def test_startup_check_is_opt_in_and_bounded_to_once_per_day(self):
        with mock.patch.object(APP, "_read_runtime_state", return_value={"last_check": 1_000}):
            self.assertFalse(APP.should_check_ytdlp_on_startup({}, now=100_000))
            self.assertFalse(
                APP.should_check_ytdlp_on_startup(
                    {"ytdlp_check_on_startup": True}, now=1_100
                )
            )
            self.assertTrue(
                APP.should_check_ytdlp_on_startup(
                    {"ytdlp_check_on_startup": True},
                    now=1_000 + APP.YTDLP_STARTUP_CHECK_SECONDS,
                )
            )

    def test_browser_is_on_demand_and_generic_retry_is_contextual(self):
        source = Path(APP.__file__).read_text(encoding="utf-8")
        before_helper = source[: source.index("def run_browser_verification_helper")]
        self.assertNotIn("import webview", before_helper)
        self.assertIn("import webview", source)
        self.assertIn('"__vrka_browser__"', source)
        self.assertIn('"generic:impersonate"', source)
        self.assertNotIn("webview.start(", before_helper)

    def test_browser_close_capture_is_deferred_off_the_ui_thread(self):
        source = Path(APP.__file__).read_text(encoding="utf-8")
        self.assertIn("window.events.closing += handle_closing", source)
        self.assertIn("threading.Thread(", source)
        self.assertIn("target=capture_and_close", source)
        self.assertIn("daemon=True", source)
        self.assertIn("allow_close.set()", source)
        self.assertNotIn("window.events.closing += capture_session", source)

    def test_custom_command_uses_selected_runtime_and_blocks_path_overrides(self):
        task = APP.DownloadTask(
            "custom",
            "https://example.test/media",
            "custom",
            standard_options(
                custom_command="--remote-components ejs:github --format-sort res"
            ),
        )
        with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()), \
             mock.patch.object(APP, "get_bundled_ffmpeg_dir", return_value="C:/bundled"):
            selected, command = APP.build_custom_ytdlp_command(task, "C:/out")
        self.assertEqual(selected.source, "managed")
        self.assertEqual(command.count("--remote-components"), 1)
        self.assertEqual(command.count("--ffmpeg-location"), 1)
        self.assertEqual(command[-1], task.url)

        task.options["custom_command"] = "--ffmpeg-location C:/untrusted"
        with mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()), \
             mock.patch.object(APP, "get_bundled_ffmpeg_dir", return_value="C:/bundled"):
            with self.assertRaisesRegex(ValueError, "not allowed"):
                APP.build_custom_ytdlp_command(task, "C:/out")

    def test_session_cookie_file_is_redacted_and_cleanup_is_explicit(self):
        source = Path(APP.__file__).read_text(encoding="utf-8")
        self.assertIn('f"task-{task.id}.cookies.txt"', source)
        self.assertIn("cookie_path.unlink()", source)
        rendered = APP.sanitize_command_for_log(
            [
                "yt-dlp",
                "--cookies",
                "C:/secret/task.cookies.txt",
                "https://example.test/media",
            ]
        )
        self.assertNotIn("task.cookies.txt", rendered)


class _FakeHandoffContext:
    """Minimal TaskExecutionContext stand-in for handoff-resume tests."""

    def __init__(self):
        self.cancel_event = threading.Event()
        self.logs = []

    def check_cancelled(self):
        pass

    def log(self, message):
        self.logs.append(str(message))

    def own_process(self, process):
        return lambda: None


def _handoff_bundle(task_id, with_cookies=True):
    from vrka_core.candidates import CandidateKind, HandoffBundle

    cookies = ()
    if with_cookies:
        cookies = (
            {
                "domain": ".example.test", "include_subdomains": True,
                "path": "/", "secure": True, "expires": 4102444800,
                "name": "cf_clearance", "value": "SECRET-CLEARANCE-VALUE",
            },
        )
    return HandoffBundle(
        task_id=task_id,
        candidate_id="mc_handoff_test",
        media_url="https://cdn.example.test/live/master.m3u8",
        media_kind=CandidateKind.HLS,
        user_agent="Mozilla/5.0 VRKA-TestAgent",
        referer="https://example.test/watch/1",
        origin="https://example.test",
        cookies=cookies,
        headers={"Accept": "*/*", "X-Video-Token": "SENSITIVE-TOKEN-VALUE"},
        expected_content_types=("application/vnd.apple.mpegurl",),
    )


class BrowserHandoffContextTests(unittest.TestCase):
    """The validation probe must exercise the SAME effective browser context
    (cookies, User-Agent, Referer, Origin) that the committed transfer will
    use.  A weaker synthetic probe rejected candidates whose real transfer
    would have succeeded."""

    def _worker(self):
        return object.__new__(APP_CLASS)

    def _popen_factory(self, recorder, task, output, code):
        class _ProbeProcess:
            def __init__(self, command, **_kwargs):
                self.command = list(command)
                self.stdout = io.StringIO(output)
                self.returncode = code
                self.pid = 4321
                recorder["commands"].append(self.command)
                cookie_file = task.options.get("session_cookie_file")
                recorder["cookie_file"] = cookie_file
                recorder["cookie_existed_at_probe"] = (
                    Path(cookie_file).is_file() if cookie_file else False
                )
                if cookie_file and Path(cookie_file).is_file():
                    recorder["cookie_at_probe"] = Path(cookie_file).read_text(
                        encoding="utf-8"
                    )

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                self.returncode = self.returncode or 1

        return _ProbeProcess

    def test_validated_probe_receives_full_browser_context_before_transfer(self):
        with workspace_temporary_directory() as tmp:
            session_dir = Path(tmp) / "session"
            worker = self._worker()
            task = APP.DownloadTask(
                "handoff-ok", "https://example.test/watch/1", "video",
                standard_options(),
            )
            bundle = _handoff_bundle(task.id)
            context = _FakeHandoffContext()
            recorder = {"commands": []}

            def fake_run_standard_task(t, _out, _cancel):
                recorder["transfer_options"] = dict(t.options)
                recorder["cookie_at_transfer"] = Path(
                    t.options["session_cookie_file"]
                ).read_text(encoding="utf-8")
                t._handoff_transfer_started.set()
                t._handoff_transfer_flow.set()

            with mock.patch.object(APP, "BROWSER_SESSION_DIR", session_dir), \
                 mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()), \
                 mock.patch.object(
                     APP.subprocess, "Popen",
                     self._popen_factory(recorder, task,
                                         "__VRKA_CANDIDATE__hls|1920x1080|m3u8|4500|aac|avc1|1080\n", 0),
                 ), \
                 mock.patch.object(worker, "_run_standard_task",
                                   side_effect=fake_run_standard_task):
                ok = worker._resume_protected_browser_transfer(
                    task, str(Path(tmp) / "out"), bundle, context,
                )
                self.assertTrue(ok)

                # The probe ran with the complete browser context.
                self.assertEqual(len(recorder["commands"]), 1)
                command = recorder["commands"][0]
                self.assertIn("--simulate", command)
                self.assertIn("--cookies", command)
                cookie_arg = command[command.index("--cookies") + 1]
                self.assertEqual(command[command.index("--user-agent") + 1],
                                 "Mozilla/5.0 VRKA-TestAgent")
                self.assertEqual(command[command.index("--referer") + 1],
                                 "https://example.test/watch/1")
                self.assertIn("Origin:https://example.test", command)
                self.assertIn("X-Video-Token:SENSITIVE-TOKEN-VALUE", command)

                # The session cookie file existed BEFORE the probe executed.
                self.assertTrue(recorder["cookie_existed_at_probe"])
                self.assertEqual(Path(cookie_arg), Path(recorder["cookie_file"]))
                self.assertIn("cf_clearance", recorder["cookie_at_probe"])

                # The committed transfer uses the exact same context.
                transfer_options = recorder["transfer_options"]
                self.assertEqual(transfer_options["resolved_media_url"], bundle.media_url)
                self.assertEqual(transfer_options["cookie_mode"], "session")
                self.assertEqual(transfer_options["session_cookie_file"], cookie_arg)
                self.assertEqual(transfer_options["session_user_agent"],
                                 "Mozilla/5.0 VRKA-TestAgent")
                self.assertEqual(transfer_options["session_referer"],
                                 "https://example.test/watch/1")
                self.assertEqual(transfer_options["session_origin"],
                                 "https://example.test")
                self.assertEqual(
                    transfer_options["resolved_media_headers"]["User-Agent"],
                    "Mozilla/5.0 VRKA-TestAgent",
                )
                self.assertEqual(recorder["cookie_at_probe"],
                                 recorder["cookie_at_transfer"])

                # No credentials/tokens leak into diagnostic logs.
                joined_logs = "\n".join(context.logs)
                self.assertNotIn("SECRET-CLEARANCE-VALUE", joined_logs)
                self.assertNotIn("SENSITIVE-TOKEN-VALUE", joined_logs)

                # Success-path cleanup removes the session cookie file.
                APP.cleanup_task_session_cookie(task)
                self.assertFalse(Path(cookie_arg).exists())

    def test_failed_validation_still_received_context_and_cleans_up_on_task_end(self):
        with workspace_temporary_directory() as tmp:
            session_dir = Path(tmp) / "session"
            worker = self._worker()
            task = APP.DownloadTask(
                "handoff-fail", "https://example.test/watch/2", "video",
                standard_options(),
            )
            bundle = _handoff_bundle(task.id)
            context = _FakeHandoffContext()
            recorder = {"commands": []}

            with mock.patch.object(APP, "BROWSER_SESSION_DIR", session_dir), \
                 mock.patch.object(APP, "resolve_ytdlp_backend", return_value=backend()), \
                 mock.patch.object(
                     APP.subprocess, "Popen",
                     self._popen_factory(recorder, task,
                                         "ERROR: HTTP Error 403: Forbidden\n", 1),
                 ):
                ok = worker._resume_protected_browser_transfer(
                    task, str(Path(tmp) / "out"), bundle, context,
                )
                self.assertFalse(ok)

                # Even the failing probe saw the full browser context.
                command = recorder["commands"][0]
                self.assertIn("--cookies", command)
                self.assertTrue(recorder["cookie_existed_at_probe"])
                self.assertEqual(command[command.index("--user-agent") + 1],
                                 "Mozilla/5.0 VRKA-TestAgent")
                self.assertEqual(command[command.index("--referer") + 1],
                                 "https://example.test/watch/1")
                self.assertIn("Origin:https://example.test", command)

                # The file survives across candidates of the same task (the
                # next candidate reuses it); the task-level cleanup then
                # removes it.
                cookie_file = Path(recorder["cookie_file"])
                self.assertTrue(cookie_file.exists())
                APP.cleanup_task_session_cookie(task)
                self.assertFalse(cookie_file.exists())

                joined_logs = "\n".join(context.logs)
                self.assertNotIn("SECRET-CLEARANCE-VALUE", joined_logs)

    def test_cleanup_helper_never_touches_foreign_paths(self):
        with workspace_temporary_directory() as tmp:
            foreign = Path(tmp) / "elsewhere.cookies.txt"
            foreign.write_text("# x\n", encoding="utf-8")
            task = APP.DownloadTask(
                "handoff-foreign", "https://example.test/watch/3", "video",
                standard_options(session_cookie_file=str(foreign)),
            )
            APP.cleanup_task_session_cookie(task)
            self.assertTrue(foreign.exists())
            task.options["session_cookie_file"] = ""
            APP.cleanup_task_session_cookie(task)
            self.assertTrue(foreign.exists())

    def test_validation_failure_log_carries_kind_host_category_only(self):
        with workspace_temporary_directory() as tmp:
            session_dir = Path(tmp) / "session"
            worker = self._worker()
            task = APP.DownloadTask(
                "diag", "https://example.test/page", "video", standard_options()
            )
            bundle = _handoff_bundle(task.id)
            context = _FakeHandoffContext()

            def fail_popen(command, **_kwargs):
                cmd = list(command)

                class _P:
                    def __init__(self):
                        self.command = cmd
                        self.stdout = io.StringIO(
                            "ERROR: HTTP Error 403: Forbidden\n")
                        self.returncode = 1
                        self.pid = 7

                    def poll(self):
                        return 1

                    def wait(self, timeout=None):
                        return 1

                    def terminate(self):
                        pass

                return _P()

            with mock.patch.object(APP, "BROWSER_SESSION_DIR", session_dir), \
                 mock.patch.object(APP, "resolve_ytdlp_backend",
                                   return_value=backend()), \
                 mock.patch.object(APP.subprocess, "Popen", fail_popen):
                worker._resume_protected_browser_transfer(
                    task, str(Path(tmp) / "out"), bundle, context,
                )
            joined = "\n".join(context.logs)
            # Redacted diagnostics present: kind | host | category.
            self.assertIn("hls", joined)
            self.assertIn("cdn.example.test", joined)
            self.assertIn("http", joined)
            # Secrets absent: no signed URL, no query, no cookie values.
            self.assertNotIn("master.m3u8", joined)
            self.assertNotIn("SECRET-CLEARANCE-VALUE", joined)


class PlayerStateEnrichmentTests(unittest.TestCase):
    """Player-affinity evidence: candidates must carry REAL playback state,
    observation counts, and timing offsets instead of blanket assumptions."""

    def test_playing_element_confers_state_dimensions_and_duration(self):
        players = [{
            "src": "https://media.example/live/master.m3u8",
            "paused": False, "readyState": 4,
            "duration": 9575.56, "rect": {"x": 0, "y": 0, "w": 737, "h": 415},
        }]
        observations = [
            {"url": "https://media.example/live/master.m3u8",
             "first_seen_ts": 1000.5},
            {"url": "https://media.example/live/master.m3u8",
             "first_seen_ts": 1000.2},
        ]
        candidates = [{"url": "https://media.example/live/master.m3u8"}]
        APP.enrich_candidates_with_player_state(
            candidates, players, observations, session_start=1000.0)
        item = candidates[0]
        self.assertIs(item["playing"], True)
        self.assertEqual(item["duration_seconds"], 9575.56)
        self.assertEqual(item["width"], 737)
        self.assertEqual(item["height"], 415)
        self.assertEqual(item["request_count"], 2)
        self.assertEqual(item["observed_offset"], 0.2)

    def test_idle_matching_element_is_not_assumed_playing(self):
        players = [{
            "src": "https://cam.example/idle.m3u8",
            "paused": True, "readyState": 0, "duration": -1,
            "rect": {"x": 0, "y": 0, "w": 160, "h": 90},
        }]
        candidates = [{"url": "https://cam.example/idle.m3u8"}]
        APP.enrich_candidates_with_player_state(candidates, players, [], 0.0)
        self.assertIs(candidates[0]["playing"], False)

    def test_unknown_cross_origin_candidate_left_untouched(self):
        candidates = [{"url": "https://cdn.example/master.m3u8"}]
        APP.enrich_candidates_with_player_state(candidates, [], [], 0.0)
        self.assertNotIn("playing", candidates[0])
        self.assertNotIn("duration_seconds", candidates[0])

    def test_observed_offset_is_clamped_to_bounded_window(self):
        observations = [{"url": "https://a.example/x.mp4",
                         "first_seen_ts": 10_000.0}]
        candidates = [{"url": "https://a.example/x.mp4"}]
        APP.enrich_candidates_with_player_state(
            candidates, [], observations, session_start=0.0)
        self.assertEqual(candidates[0]["observed_offset"], 600.0)

    def test_unclassified_media_diagnostics_present_in_capture_payload(self):
        source = Path(APP.__file__).read_text(encoding="utf-8")
        self.assertIn('"unclassified_media_count"', source)
        self.assertIn("unclassified_media_hosts", source)


class ObserverRetentionTests(unittest.TestCase):
    """Long interactive sessions must not evict active media lineage.

    Regression for the real JAV.GURU failure: a human plays the episode for
    minutes; hundreds of unique segment URLs push the observer past its
    512-entry bound; the old FIFO evicted the master.m3u8 fetched once at
    playback start, orphaning every segment and silently producing zero
    candidates."""

    MASTER = ("https://cv9fqnu812v.cdn-centaurus.com/hls2/01/14735/"
              "36qrh8kws4th_,l,n,h,.urlset/master.m3u8?t=sig&e=x")
    VARIANT = ("https://cv9fqnu812v.cdn-centaurus.com/hls2/01/14735/"
               "36qrh8kws4th_,l,n,h,.urlset/index-v1-a1.m3u8?t=sig&e=x")

    def _session_urls(self):
        urls = []
        for i in range(60):
            urls.append(f"https://jav.guru/wp-content/a{i}.js")
        for i in range(40):
            if i % 20 == 0:
                urls.append("https://media-hls.growcdnssedge.com/x/"
                            "197969896_240p.m3u8")
            else:
                urls.append(f"https://media-hls.growcdnssedge.com/x/"
                            f"197969896_{i}_tok_t.mp4")
        urls.append(self.MASTER)
        urls.append(self.VARIANT)
        for i in range(600):
            urls.append(f"https://cv9fqnu812v.cdn-centaurus.com/hls2/01/"
                        f"14735/36qrh8kws4th_,l,n,h,.urlset/seg{i}.ts?rn={i}")
        return urls

    def _merge_like_helper(self, urls, limit=None):
        """Faithful replication of the helper's merge path (insertion order,
        retention-cached fields, real eviction function)."""
        store = {}
        limit = limit or APP.BROWSER_OBSERVATION_LIMIT
        for url in urls:
            record = {"url": url}
            if url.endswith(".m3u8"):
                record["content_type"] = "application/vnd.apple.mpegurl"
            elif url.endswith(".ts"):
                record["content_type"] = "video/mp2t"
            elif url.endswith(".mp4"):
                record["content_type"] = "video/mp4"
            existing = store.pop(url, {"url": url})
            for k, v in record.items():
                existing[k] = v
            (existing["_mscore"], existing["_mkind"]) = \
                APP._media_observation_score(existing)
            store[url] = existing
            APP.evict_observations_beyond_limit(store, limit)
        return store

    def test_long_human_session_retains_master_and_produces_candidate(self):
        store = self._merge_like_helper(self._session_urls())
        self.assertLessEqual(len(store), APP.BROWSER_OBSERVATION_LIMIT)
        self.assertIn(self.MASTER, store)
        observations = [{k: v for k, v in item.items()
                         if k not in ("_mscore", "_mkind")}
                        for item in store.values()]
        candidates = APP.rank_media_candidates(observations)
        self.assertGreater(len(candidates), 0)
        top_host = candidates[0]["url"].split("/")[2]
        self.assertEqual(top_host, "cv9fqnu812v.cdn-centaurus.com")
        kinds = [c.get("kind") for c in candidates[:4]]
        self.assertIn("HLS", kinds)

    def test_eviction_order_prefers_junk_then_segments_then_manifests(self):
        store = {"https://jav.guru/x.js": {"url": "https://jav.guru/x.js"}}
        segment = {"url": "https://cdn.example/s/seg1.ts",
                   "content_type": "video/mp2t"}
        manifest = {"url": "https://cdn.example/m/master.m3u8",
                    "content_type": "application/vnd.apple.mpegurl"}
        direct = {"url": "https://cdn.example/v/movie.mp4",
                  "content_type": "video/mp4"}
        for rec in (segment, direct, manifest):
            rec["_mscore"], rec["_mkind"] = APP._media_observation_score(rec)
        store[segment["url"]] = segment
        store[direct["url"]] = direct
        store[manifest["url"]] = manifest
        # Push beyond a tight bound: junk goes first; media classes survive.
        APP.evict_observations_beyond_limit(store, 3)
        self.assertNotIn("https://jav.guru/x.js", store)
        self.assertIn(segment["url"], store)
        self.assertIn(direct["url"], store)
        self.assertIn(manifest["url"], store)
        # Tighter pressure sacrifices the segment before direct/manifest.
        APP.evict_observations_beyond_limit(store, 2)
        self.assertNotIn(segment["url"], store)
        self.assertIn(direct["url"], store)
        self.assertIn(manifest["url"], store)

    def test_orphan_segments_still_rejected_after_retention(self):
        store = self._merge_like_helper(
            [f"https://cdn.example/hls/seg{i}.ts?n={i}" for i in range(40)])
        observations = [{k: v for k, v in item.items()
                         if k not in ("_mscore", "_mkind")}
                        for item in store.values()]
        self.assertEqual(APP.rank_media_candidates(observations), [])

    def test_store_remains_bounded_under_pure_flood(self):
        store = self._merge_like_helper(
            [f"https://cdn.example/flood/{i}.bin" for i in range(1500)])
        self.assertLessEqual(len(store), APP.BROWSER_OBSERVATION_LIMIT)


class RepresentationGateTests(unittest.TestCase):
    """Handoff candidates must be self-contained for their task mode.

    Regression for the real HentaiHaven failures: video-only variant
    manifests produced silent files, and rendition manifests ignored the
    user's requested resolution.  The master (audio+video) must be the
    candidate that passes validation."""

    @staticmethod
    def _probe(acodec="aac", vcodec="avc1", height="1080"):
        return {"probe_acodec": acodec, "probe_vcodec": vcodec,
                "probe_height": height}

    def test_video_task_requires_audio_and_video(self):
        ok, reason = APP.candidate_satisfies_task_mode(self._probe(), "video")
        self.assertTrue(ok)
        ok, reason = APP.candidate_satisfies_task_mode(
            self._probe(acodec="none"), "video")
        self.assertFalse(ok)
        self.assertEqual(reason, "no-audio")
        ok, reason = APP.candidate_satisfies_task_mode(
            self._probe(vcodec="none"), "video")
        self.assertFalse(ok)
        self.assertEqual(reason, "no-video")

    def test_audio_only_candidate_rejected_for_video_task(self):
        ok, reason = APP.candidate_satisfies_task_mode(
            self._probe(acodec="mp4a", vcodec="none"), "video")
        self.assertFalse(ok)
        self.assertEqual(reason, "no-video")

    def test_audio_task_unaffected_by_video_codec(self):
        ok, _ = APP.candidate_satisfies_task_mode(
            self._probe(vcodec="none"), "audio")
        self.assertTrue(ok)

    def test_missing_probe_fields_are_treated_as_absent_codecs(self):
        ok, reason = APP.candidate_satisfies_task_mode({}, "video")
        self.assertFalse(ok)
        self.assertEqual(reason, "no-audio")

    def test_height_parse_helper(self):
        self.assertEqual(APP.candidate_probe_height(self._probe()), 1080)
        self.assertEqual(APP.candidate_probe_height(
            self._probe(height="720")), 720)
        self.assertEqual(APP.candidate_probe_height(
            self._probe(height="None")), 0)
        self.assertEqual(APP.candidate_probe_height({}), 0)

    def test_validation_gate_rejects_video_only_variant(self):
        worker = object.__new__(APP_CLASS)
        task = APP.DownloadTask("hh", "https://example.test/page", "video",
                                standard_options())

        class ProbeProcess:
            def __init__(self):
                self.stdout = io.StringIO(
                    "__VRKA_CANDIDATE__hls|1280x720|mp4|2000|"
                    "none|avc1|720\n")
                self.returncode = 0
                self.pid = 9

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                pass

        captured = {}
        real_build = APP.build_candidate_probe_command

        def fake_build(t, c):
            _b, cmd = real_build(t, c)
            captured["cmd"] = cmd
            return backend(), ["yt-dlp"]

        with mock.patch.object(APP, "resolve_ytdlp_backend",
                               return_value=backend()), \
             mock.patch.object(APP, "build_candidate_probe_command",
                               fake_build), \
             mock.patch.object(APP.subprocess, "Popen",
                               lambda *a, **k: ProbeProcess()):
            ok = APP_CLASS._validate_media_candidate(
                worker, task,
                {"url": "https://cdn.example/v.m3u8", "headers": {},
                 "content_type": ""},
                threading.Event(), None)
        self.assertFalse(ok)
        self.assertEqual(getattr(task, "_last_probe_category"), "no-audio")
        # The probe command now requests codec/height fields.
        marker = [c for c in captured["cmd"]
                  if isinstance(c, str) and c.startswith("__VRKA_CANDIDATE__%")]
        self.assertTrue(marker and "acodec" in marker[0] and
                        "vcodec" in marker[0] and "height" in marker[0])


class BrowserObservationCreditTests(unittest.TestCase):
    """A live HTTP-200 browser fetch of the exact media URL outweighs a
    context-bound replay failure (Cloudflare/expired/transient HTTP)."""

    def _bundle(self, status):
        from types import SimpleNamespace
        return SimpleNamespace(observed_status=status)

    def test_cloudflare_failure_overridden_by_browser_200(self):
        self.assertTrue(APP.probe_failure_overridden_by_browser_observation(
            self._bundle(200), "cloudflare"))

    def test_expired_and_http_categories_overridden(self):
        for category in ("expired", "http"):
            self.assertTrue(
                APP.probe_failure_overridden_by_browser_observation(
                    self._bundle(200), category))

    def test_other_categories_not_overridden(self):
        for category in ("drm", "unsupported", "timeout", ""):
            self.assertFalse(
                APP.probe_failure_overridden_by_browser_observation(
                    self._bundle(200), category))

    def test_without_browser_200_no_override(self):
        for status in (0, 403, 503):
            self.assertFalse(
                APP.probe_failure_overridden_by_browser_observation(
                    self._bundle(status), "cloudflare"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
