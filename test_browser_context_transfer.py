"""Browser-context media transfer: assembly, loop wiring, and app transfer.

Generic coverage for the technical class "the protected browser fetched the
media (HTTP 200); independent replay was refused; the transfer completes
through browser-captured bytes".  No site identity, no WebView2 required.
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from vrka_core import (
    AutomaticFallbackExecutor,
    BrowserFallbackError,
    DownloadState,
    ExternalReplayRejected,
    ProcessInactivity,
    ActivityPhase,
    ProtectedBrowserFallback,
    TaskScheduler,
    TaskSpec,
    TaskStore,
    assemble_browser_capture,
    classify_capture_entry,
)


def _write_object(objects_dir: Path, data: bytes) -> str:
    import hashlib
    name = "obj-" + hashlib.sha256(data).hexdigest()[:16]
    (objects_dir / name).write_bytes(data)
    return name


class MediaAssemblyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.objects = Path(self._tmp.name) / "objects"
        self.objects.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _entry(self, url, data, content_type="", status=200, seq=0):
        return {
            "url": url, "status": status, "bytes": len(data),
            "content_type": content_type, "object": _write_object(self.objects, data),
            "seq": seq,
        }

    def test_classification_uses_multiple_signals(self):
        self.assertEqual(classify_capture_entry(
            "https://x.test/hls/v.m3u8?sig=1"), "playlist")
        self.assertEqual(classify_capture_entry(
            "https://x.test/hls/v", "application/vnd.apple.mpegurl"), "playlist")
        self.assertEqual(classify_capture_entry(
            "https://x.test/hls/seg-7.ts"), "segment")
        self.assertEqual(classify_capture_entry(
            "https://x.test/hls/seg_1", "video/mp2t"), "segment")
        # No content type, no media suffix, but generic segment path shape.
        self.assertEqual(classify_capture_entry(
            "https://x.test/b-hls-05/123/123_h264_114_token"), "other")
        self.assertEqual(classify_capture_entry(
            "https://x.test/page", "text/html"), "other")

    def test_playlist_mode_assembles_listed_order(self):
        seg_a = self._entry("https://m.test/h/seg0.ts", b"AAAA", "video/mp2t")
        seg_b = self._entry("https://m.test/h/seg1.ts", b"BBBB", "video/mp2t")
        playlist = self._entry(
            "https://m.test/h/v.m3u8", b"#EXTM3U\nseg0.ts\nseg1.ts\n#EXT-X-ENDLIST\n",
            "application/vnd.apple.mpegurl")
        out = Path(self._tmp.name) / "out.ts"
        report = assemble_browser_capture(
            [seg_b, playlist, seg_a], self.objects, out)
        self.assertTrue(report["assembled"], report)
        self.assertEqual(report["mode"], "playlist")
        self.assertEqual(out.read_bytes(), b"AAAABBBB")

    def test_playlist_mode_follows_redirect_map(self):
        seg_body = b"REDIR"
        seg = self._entry("https://mirror.test/seg5.ts", seg_body, "video/mp2t")
        playlist = self._entry(
            "https://m.test/h/v.m3u8", b"#EXTM3U\n/seg5.ts\n",
            "application/vnd.apple.mpegurl")
        out = Path(self._tmp.name) / "out.ts"
        report = assemble_browser_capture(
            [playlist, seg], self.objects, out,
            {"https://m.test/h/seg5.ts": "https://mirror.test/seg5.ts"})
        self.assertTrue(report["assembled"], report)
        self.assertEqual(out.read_bytes(), seg_body)

    def test_observation_order_fallback_groups_lineages(self):
        # Provider omitted Content-Type and the variant playlist body was
        # unreadable (0 bytes): reconstruction must come from capture order.
        stream_a = [self._entry(f"https://m.test/s-a/seg{i}_1", bytes([65, i]), seq=i)
                    for i in range(3)]
        stream_b = [self._entry(f"https://m.test/s-b/seg{i}_2", bytes([66, i]), seq=i)
                    for i in range(2)]
        empty_playlist = self._entry("https://m.test/s-a/v.m3u8", b"", seq=9)
        out = Path(self._tmp.name) / "out.bin"
        report = assemble_browser_capture(
            [empty_playlist] + stream_b + stream_a, self.objects, out)
        self.assertTrue(report["assembled"], report)
        self.assertEqual(report["mode"], "observation-order")
        # Richest lineage wins, concatenated in observation order.
        self.assertEqual(out.read_bytes(), bytes([65, 0, 65, 1, 65, 2]))

    def test_missing_segments_reported_never_fabricated(self):
        seg = self._entry("https://m.test/h/seg0.ts", b"AAAA", "video/mp2t")
        playlist = self._entry(
            "https://m.test/h/v.m3u8",
            b"#EXTM3U\nseg0.ts\nseg1.ts\nseg2.ts\n", "application/vnd.apple.mpegurl")
        out = Path(self._tmp.name) / "out.ts"
        report = assemble_browser_capture([playlist, seg], self.objects, out)
        self.assertTrue(report["assembled"])
        self.assertEqual(report["segments"], 1)
        self.assertEqual(len(report["missing"]), 2)

    def test_nothing_captured_is_honest(self):
        out = Path(self._tmp.name) / "out.bin"
        report = assemble_browser_capture([], self.objects, out)
        self.assertFalse(report["assembled"])


class _FakeEpisode:
    def __init__(self, payloads, order):
        self.payloads = list(payloads)
        self.order = order
        self.committed = False
        self.commands = []

    def capture(self, _cancel_event, since_seq=0):
        self.order.append("capture")
        return self.payloads.pop(0) if self.payloads else {
            "ok": True, "capture_seq": 99, "media_capture": None, "cookies": [],
            "media_candidates": [], "user_agent": "a", "referer": "https://x.test/w",
        }

    def request_capture(self):
        self.order.append("request_capture")

    def request_media_capture(self):
        self.order.append("request_media_capture")

    def commit(self):
        self.order.append("commit")
        self.committed = True

    def close(self):
        self.order.append("close")


def _media_payload(seq=1, status=200):
    return {
        "ok": True, "capture_seq": seq,
        "blocked_popup_count": 0, "blocked_navigation_count": 0,
        "rejected_junk_count": 0, "dropped_request_count": 0,
        "user_agent": "agent", "referer": "https://example.test/watch",
        "cookies": [],
        "media_candidates": [{
            "url": "https://provider.example/stream/ep/master.m3u8",
            "content_type": "application/vnd.apple.mpegurl",
            "user_started": True, "request_count": 4,
            "duration_seconds": 90, "width": 1280, "height": 720,
            "status": status,
        }],
    }


class BrowserContextLoopTests(unittest.TestCase):
    def _run_task(self, task_id, resume, browser_context_transfer):
        order = []
        episode = _FakeEpisode([_media_payload()], order)

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        controller = ProtectedBrowserFallback(
            lambda *_: episode, resume,
            browser_context_transfer=browser_context_transfer,
            clock=lambda: 100, interaction_wait_seconds=5.0,
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/x", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(10))
                record = scheduler.get(task_id)
                return record, episode, order
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_rejected_replay_completes_through_browser_context_transfer(self):
        task_id = "00000000-0000-4000-8000-000000000320"
        calls = []

        def resume(_bundle, _context):
            raise ExternalReplayRejected(
                "The protected browser fetched this media, but the media "
                "server rejected the independent transfer replay.")

        def browser_context_transfer(episode, bundle, context):
            calls.append((episode, bundle))
            episode.request_media_capture()
            return True

        record, episode, order = self._run_task(task_id, resume, browser_context_transfer)
        self.assertEqual(record.state, DownloadState.COMPLETED, record.error)
        self.assertEqual(len(calls), 1)
        self.assertIn("request_media_capture", order)
        self.assertIn("commit", order)
        self.assertTrue(episode.committed)

    def test_browser_context_transfer_attempted_only_once(self):
        task_id = "00000000-0000-4000-8000-000000000321"
        attempts = []

        def resume(_bundle, _context):
            raise ExternalReplayRejected("rejected")

        def browser_context_transfer(_episode, _bundle, _context):
            attempts.append(1)
            return False  # capture yielded nothing

        record, _episode, _order = self._run_task(task_id, resume, browser_context_transfer)
        self.assertEqual(record.state, DownloadState.FAILED)
        self.assertEqual(len(attempts), 1)
        # The honest classification for this class is preserved.
        self.assertIn("browser-accessible but externally non-transferable",
                      record.error)

    def test_no_rejection_no_browser_context_attempt(self):
        task_id = "00000000-0000-4000-8000-000000000322"

        def resume(_bundle, _context):
            return True  # normal external transfer works

        def browser_context_transfer(_episode, _bundle, _context):
            raise AssertionError("browser-context transfer must not run")

        record, _episode, order = self._run_task(task_id, resume, browser_context_transfer)
        self.assertEqual(record.state, DownloadState.COMPLETED, record.error)
        self.assertNotIn("request_media_capture", order)


class EpisodeProtocolTests(unittest.TestCase):
    def test_request_media_capture_sends_command(self):
        from vrka_core import JsonFileBrowserEpisode
        sent = []
        process = SimpleNamespace(
            poll=lambda: None, stdin=SimpleNamespace(
                write=lambda data: sent.append(data) or len(data),
                flush=lambda: None))
        episode = JsonFileBrowserEpisode(process, Path("unused.json"))
        episode.request_media_capture()
        self.assertEqual(sent, ["mediacapture\n"])


class AppBrowserContextTransferTests(unittest.TestCase):
    def _app_with_capture(self, tmp, probe_summary):
        import vrka_downloader as app_module
        app = object.__new__(app_module.VRKADownloader)
        app._probe_media_summary = mock.Mock(return_value=probe_summary)
        return app, app_module

    def test_transfer_collects_assembles_validates_places_output(self):
        import vrka_downloader as app_module
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            objects = tmp_path / "objects"
            objects.mkdir()
            body = b"MEDIA" * 32
            import hashlib
            name = "obj-" + hashlib.sha256(body).hexdigest()[:16]
            (objects / name).write_bytes(body)
            payload = {
                "ok": True, "capture_seq": 7,
                "media_capture": {
                    "objects": [{
                        "url": "https://provider.example/s/seg0.m4s",
                        "status": 200, "bytes": len(body),
                        "content_type": "", "object": name, "seq": 0,
                    }],
                    "redirects": {}, "total_bytes": len(body),
                    "objects_dir": str(objects),
                },
                "media_candidates": [], "cookies": [],
                "user_agent": "a", "referer": "https://example.test/watch",
            }
            episode = _FakeEpisode([payload], [])
            app, app_module = self._app_with_capture(tmp, {
                "format": {"format_name": "mov,mp4", "duration": "90.0"},
                "streams": [{"codec_type": "video", "codec_name": "h264"},
                            {"codec_type": "audio", "codec_name": "aac"}],
            })
            app.BROWSER_CAPTURE_SETTLE_SECONDS = 0.3
            app.BROWSER_CAPTURE_WAIT_SECONDS = 5.0
            task = app_module.DownloadTask(
                "bctx1", "https://example.test/watch", "video", {})
            task.title = "My Episode"
            task.options["_staging_dir"] = str(tmp_path / "staging")
            Path(task.options["_staging_dir"]).mkdir()
            bundle = SimpleNamespace(
                referer="https://example.test/watch",
                expected_duration_seconds=90.0)
            progress = []
            context = SimpleNamespace(
                cancel_event=threading.Event(),
                check_cancelled=lambda: None,
                log=lambda _m: None,
                progress=lambda value, **kw: progress.append((value, kw)),
            )

            ok = app._run_browser_context_transfer(
                episode, task, str(tmp_path / "out"), bundle, context)

            self.assertTrue(ok)
            self.assertTrue(Path(task.output_path).exists())
            self.assertIn("My Episode", task.output_path)
            self.assertEqual(progress[-1][1].get("output_path"), task.output_path)

    def test_partial_playlist_capture_is_an_honest_failure(self):
        """ffprobe duration on fMP4 reports the DECLARED duration, not the
        captured coverage; the playlist's segment list is the honest
        completeness evidence.  A playlist capture with missing segments
        must fail with coverage numbers, never masquerade as complete."""
        import vrka_downloader as app_module
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            objects = tmp_path / "objects"
            objects.mkdir()
            body = b"P" * 64
            import hashlib
            name = "obj-" + hashlib.sha256(body).hexdigest()[:16]
            (objects / name).write_bytes(body)
            playlist_body = b"#EXTM3U\nseg0.m4s\nseg1.m4s\nseg2.m4s\n"
            playlist_name = "obj-" + hashlib.sha256(playlist_body).hexdigest()[:16]
            (objects / playlist_name).write_bytes(playlist_body)
            payload = {
                "ok": True, "capture_seq": 3,
                "media_capture": {
                    "objects": [
                        {
                            "url": "https://provider.example/s/seg0.m4s",
                            "status": 200, "bytes": len(body),
                            "content_type": "", "object": name, "seq": 0,
                        },
                        {
                            "url": "https://provider.example/s/v.m3u8",
                            "status": 200, "bytes": len(playlist_body),
                            "content_type": "application/vnd.apple.mpegurl",
                            "object": playlist_name, "seq": 1,
                        },
                    ],
                    "redirects": {}, "total_bytes": len(body),
                    "objects_dir": str(objects),
                },
                "media_candidates": [], "cookies": [],
                "user_agent": "a", "referer": "https://example.test/watch",
            }
            episode = _FakeEpisode([payload], [])
            app, app_module = self._app_with_capture(tmp, {
                "format": {"format_name": "mov,mp4", "duration": "2863.0"},
                "streams": [{"codec_type": "video", "codec_name": "h264"}],
            })
            app.BROWSER_CAPTURE_SETTLE_SECONDS = 0.3
            app.BROWSER_CAPTURE_WAIT_SECONDS = 5.0
            task = app_module.DownloadTask(
                "bctx2", "https://example.test/watch", "video", {})
            task.options["_staging_dir"] = str(tmp_path / "staging")
            Path(task.options["_staging_dir"]).mkdir()
            bundle = SimpleNamespace(
                referer="https://example.test/watch",
                expected_duration_seconds=90.0)
            context = SimpleNamespace(
                cancel_event=threading.Event(),
                check_cancelled=lambda: None,
                log=lambda _m: None,
                progress=lambda value, **kw: None,
            )
            with self.assertRaises(RuntimeError) as caught:
                app._run_browser_context_transfer(
                    episode, task, str(tmp_path / "out"), bundle, context)
            self.assertIn("covered only 1 of 3 playlist segments",
                          str(caught.exception))


    def test_browser_close_during_capture_is_a_clean_cancellation(self):
        """The user closing the protected browser mid-capture cancels the
        browser-context download cleanly - never reported as unstable
        media, provider failure, or a missing candidate."""
        import vrka_downloader as app_module
        from vrka_core import BrowserFallbackError
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            episode = SimpleNamespace(
                request_media_capture=lambda: None,
                request_capture=lambda: None,
                capture=lambda *_a, **_k: (_ for _ in ()).throw(
                    BrowserFallbackError(
                        "Protected browser closed before capture")),
            )
            app, app_module = self._app_with_capture(tmp, {})
            task = app_module.DownloadTask(
                "bctx3", "https://example.test/watch", "video", {})
            task.options["_staging_dir"] = str(tmp_path / "staging")
            Path(task.options["_staging_dir"]).mkdir()
            bundle = SimpleNamespace(
                referer="https://example.test/watch",
                expected_duration_seconds=0.0)
            context = SimpleNamespace(
                cancel_event=threading.Event(),
                check_cancelled=lambda: None,
                log=lambda _m: None,
                progress=lambda *a, **k: None,
            )
            with self.assertRaises(app_module.BrowserContextCancelled) as caught:
                app._run_browser_context_transfer(
                    episode, task, str(tmp_path / "out"), bundle, context)
            self.assertIn("Protected browser closed. Browser-context "
                          "download was cancelled.",
                          str(caught.exception))

class SessionEvidenceTests(unittest.TestCase):
    """Evidence semantics: 'provider dry' requires playback-started proof."""

    def _classify(self, payload, state="failed", error="", **kw):
        from vrka_core.browser_capture import classify_session_evidence
        return classify_session_evidence(payload, state, error, **kw)

    def test_page_loaded_without_playback_is_not_provider_dry(self):
        payload = {
            "ok": True, "observed_request_count": 40,
            "media_candidates": [], "player_state": [],
            "media_capture": None,
        }
        evidence = self._classify(payload, error="No playable media was observed")
        self.assertEqual(evidence["session"], "playback_not_initiated")

    def test_player_present_but_never_started_is_not_provider_dry(self):
        payload = {
            "ok": True, "observed_request_count": 55,
            "media_candidates": [{
                "url": "https://p.test/cam.m3u8", "user_started": False,
            }],
            "player_state": [{"rect": {"w": 300, "h": 150}}],
            "media_capture": None,
        }
        evidence = self._classify(payload)
        self.assertEqual(evidence["session"], "playback_not_initiated")

    def test_playback_started_without_media_is_provider_delivery_failure(self):
        payload = {
            "ok": True, "observed_request_count": 80,
            "media_candidates": [{
                "url": "https://p.test/ep/master.m3u8", "user_started": True,
            }],
            "player_state": [{"playing": True}],
            "media_capture": {"objects": [], "total_bytes": 0},
        }
        evidence = self._classify(payload)
        self.assertEqual(evidence["session"], "provider_media_delivery_failed")

    def test_playback_started_and_captured_is_delivering(self):
        payload = {
            "ok": True, "observed_request_count": 120,
            "media_candidates": [{
                "url": "https://p.test/ep/master.m3u8", "user_started": True,
            }],
            "player_state": [{"playing": True}],
            "media_capture": {"objects": [{"bytes": 4096}], "total_bytes": 4096},
        }
        evidence = self._classify(payload, state="browser_context_transfer")
        self.assertEqual(evidence["session"], "media_captured")

    def test_forced_rejection_is_marked_simulated(self):
        payload = {"ok": True, "media_candidates": [], "player_state": []}
        evidence = self._classify(payload, force_rejection=True,
                                  browser_context_attempted=True)
        self.assertTrue(evidence["rejection_simulated"])
        self.assertTrue(evidence["browser_context_attempted"])

    def test_bctx_attempt_without_bytes_is_reported(self):
        payload = {
            "ok": True, "media_candidates": [],
            "player_state": [], "media_capture": {"objects": []},
        }
        evidence = self._classify(payload, browser_context_attempted=True)
        self.assertEqual(evidence["session"], "browser_context_transfer_failed")

    def test_completed_state_wins(self):
        payload = {"ok": True, "media_candidates": [], "player_state": []}
        evidence = self._classify(payload, state="completed")
        self.assertEqual(evidence["session"], "completed")


class CoverageMonitorTests(unittest.TestCase):
    """The collector is a PASSIVE monitor: the user drives playback; VRKA
    never seeks, clicks, or otherwise automates the player."""

    def _payload(self, seq, captured_count, tmp_path, total=3):
        import hashlib
        objects = []
        playlist_body = (b"#EXTM3U\n" + b"".join(
            b"#EXTINF:4.0,\nseg%d.m4s\n" % i for i in range(total))
            + b"#EXT-X-ENDLIST\n")
        pname = "obj-" + hashlib.sha256(playlist_body).hexdigest()[:16]
        (tmp_path / pname).write_bytes(playlist_body)
        objects.append({
            "url": "https://p.test/s/v.m3u8", "status": 200,
            "bytes": len(playlist_body),
            "content_type": "application/vnd.apple.mpegurl",
            "object": pname, "seq": 0})
        for i in range(captured_count):
            body = b"S" * 2048 + bytes([i])
            name = "obj-" + hashlib.sha256(
                body + bytes([i])).hexdigest()[:16]
            (tmp_path / name).write_bytes(body)
            objects.append({
                "url": f"https://p.test/s/seg{i}.m4s", "status": 200,
                "bytes": len(body), "content_type": "video/mp4",
                "object": name, "seq": i + 1})
        return {
            "ok": True, "capture_seq": seq,
            "media_capture": {
                "objects": objects, "redirects": {},
                "total_bytes": sum(o["bytes"] for o in objects),
                "objects_dir": str(tmp_path),
            },
            "media_candidates": [], "cookies": [],
            "user_agent": "a", "referer": "https://example.test/watch",
        }

    def test_complete_coverage_returns_immediately_without_seeking(self):
        import vrka_downloader as app_module
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payloads = [self._payload(1, 3, tmp_path)]
            unknown_calls = []

            class _Episode:
                def request_capture(self):
                    pass

                def capture(self, _cancel, since_seq=0):
                    return payloads.pop(0) if payloads else {
                        "ok": True, "capture_seq": 99, "media_capture": None,
                        "cookies": [], "media_candidates": [],
                        "user_agent": "a", "referer": "r",
                    }

                def __getattr__(self, name):
                    if name == "seek_timeline":
                        unknown_calls.append(name)
                    raise AttributeError(name)

            app = object.__new__(app_module.VRKADownloader)
            app.BROWSER_CAPTURE_WAIT_SECONDS = 4.0
            app.BROWSER_CAPTURE_SETTLE_SECONDS = 0.4
            app.BROWSER_CAPTURE_PLAYTHROUGH_SECONDS = 8.0
            context = SimpleNamespace(
                cancel_event=threading.Event(),
                check_cancelled=lambda: None,
                log=lambda _m: None,
                progress=lambda *a, **k: None,
            )
            best = app._collect_browser_capture(_Episode(), context)
            self.assertEqual(len(model_captured(best)), 3)
            self.assertEqual(unknown_calls, [])

    def test_partial_capture_returns_bounded_without_automation(self):
        """Coverage that never completes must still return (bounded) with
        the richest snapshot - no seeking, no unbounded wait."""
        import vrka_downloader as app_module
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payloads = [self._payload(1, 1, tmp_path)]

            class _Episode:
                def request_capture(self):
                    pass

                def capture(self, _cancel, since_seq=0):
                    return payloads.pop(0) if payloads else {
                        "ok": True, "capture_seq": 99, "media_capture": None,
                        "cookies": [], "media_candidates": [],
                        "user_agent": "a", "referer": "r",
                    }

            app = object.__new__(app_module.VRKADownloader)
            app.BROWSER_CAPTURE_WAIT_SECONDS = 1.0
            app.BROWSER_CAPTURE_SETTLE_SECONDS = 0.3
            app.BROWSER_CAPTURE_PLAYTHROUGH_SECONDS = 2.0
            context = SimpleNamespace(
                cancel_event=threading.Event(),
                check_cancelled=lambda: None,
                log=lambda _m: None,
                progress=lambda *a, **k: None,
            )
            best = app._collect_browser_capture(_Episode(), context)
            self.assertEqual(len(model_captured(best)), 1)


def model_captured(best):
    from vrka_core.coverage import model_from_urls, parse_playlist
    objects_dir = Path(best["objects_dir"])
    for entry in reversed(best.get("objects", [])):
        if ".m3u8" not in entry.get("url", "").lower():
            continue
        playlist_file = objects_dir / entry.get("object", "")
        if playlist_file.is_file():
            text = playlist_file.read_text(errors="replace")
            if "#EXTINF" in text:
                times, urls, _total = parse_playlist(
                    text, entry.get("url", ""))
                model = model_from_urls(times, urls, set())
                captured = {
                    o.get("url", "").split("?")[0]
                    for o in best.get("objects", [])
                    if int(o.get("bytes") or 0) > 0}
                for url in captured:
                    index = urls.index(url) if url in urls else None
                    if index is not None:
                        model.mark_captured(index)
                return model.captured
    return set()
