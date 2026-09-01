import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from vrka_core import (
    ActivityPhase,
    AutomaticFallbackExecutor,
    BrowserFallbackError,
    DirectPathEligibleForFallback,
    DownloadState,
    ExternalReplayRejected,
    JsonFileBrowserEpisode,
    ProcessInactivity,
    ProtectedBrowserFallback,
    TaskScheduler,
    TaskSpec,
    TaskStore,
)


class FakeEpisode:
    def __init__(self, payload, order):
        self.payload = payload
        self.order = order
        self.committed = False
        self.closed = False

    def capture(self, _cancel_event, since_seq=0):
        self.order.append("capture")
        return self.payload

    def request_capture(self):
        self.order.append("request_capture")

    def commit(self):
        self.order.append("commit")
        self.committed = True

    def close(self):
        self.order.append("close")
        self.closed = True


class SequencedFakeEpisode:
    """Emits capture payloads in order; mirrors a live browser that snapshots
    repeatedly while staying open (each snapshot advances capture_seq)."""

    def __init__(self, payloads, order):
        self.payloads = list(payloads)
        self.order = order
        self.committed = False
        self.closed = False

    def capture(self, _cancel_event, since_seq=0):
        self.order.append("capture")
        return self.payloads.pop(0)

    def request_capture(self):
        self.order.append("request_capture")

    def commit(self):
        self.order.append("commit")
        self.committed = True

    def close(self):
        self.order.append("close")
        self.closed = True


class BrowserFallbackTests(unittest.TestCase):
    def _payload(self):
        return {
            "ok": True,
            "blocked_popup_count": 4,
            "blocked_navigation_count": 2,
            "rejected_junk_count": 8,
            "dropped_request_count": 1,
            "user_agent": "secret-agent",
            "referer": "https://private.example.test/watch",
            "cookies": [{"name": "session", "value": "secret-cookie"}],
            "media_candidates": [
                {
                    "url": "https://ads.example.test/preroll.mp4?token=ad-token",
                    "content_type": "video/mp4", "duration_seconds": 8,
                    "width": 320, "height": 180, "popup_context": True,
                    "nuisance_score": 9, "request_count": 3, "player_id": "popup",
                },
                {
                    "url": "https://cdn.example.test/main.m3u8?token=media-token",
                    "content_type": "application/vnd.apple.mpegurl",
                    "duration_seconds": 1800, "width": 1920, "height": 1080,
                    "primary_player": True, "user_started": True, "playing": True, "player_id": "main",
                    "headers": {"Referer": "https://private.example.test/watch", "Authorization": "secret"},
                    "request_count": 4,
                },
                {
                    "url": "https://cdn.example.test/main-segment-1.ts",
                    "content_type": "video/mp2t",
                    "segment_parent_url": "https://cdn.example.test/main.m3u8?token=media-token",
                    "player_id": "main", "content_length": 400000,
                },
                {
                    "url": "https://cdn.example.test/main-segment-2.ts",
                    "content_type": "video/mp2t",
                    "segment_parent_url": "https://cdn.example.test/main.m3u8?token=media-token",
                    "player_id": "main", "content_length": 400000,
                },                {
                    "url": "https://cdn.example.test/alternate.mp4?token=alternate-token",
                    "content_type": "video/mp4", "duration_seconds": 1700,
                    "width": 1280, "height": 720, "primary_player": True,
                    "user_started": True, "player_id": "alternate", "request_count": 4,
                },
            ],
        }

    def test_first_capture_is_requested_without_waiting_for_dom_visible_playback(self):
        """Regression (V3 production fallback): the helper's playable watcher
        cannot see cross-origin iframe players, so the first snapshot must be
        requested up front instead of waiting for a payload that would never
        arrive (production died with "closed before validated handoff")."""
        task_id = "00000000-0000-4000-8000-000000000310"
        order = []
        # Snapshot 1: page still loading, zero candidates.  Snapshot 2 (after
        # the paced re-capture): media flowed from an invisible provider
        # iframe into the network sniffer.
        payloads = [
            {"ok": True, "capture_seq": 1, "media_candidates": [], "cookies": []},
            {
                "ok": True, "capture_seq": 2, "cookies": [],
                "media_candidates": [{
                    "url": "https://provider.example.test/index-f1-v1-a1.m3u8",
                    "content_type": "application/vnd.apple.mpegurl",
                    "duration_seconds": 1400, "width": 1920, "height": 1080,
                    "primary_player": True, "user_started": True,
                    "player_id": "embed", "request_count": 3,
                }],
            },
        ]
        episode = SequencedFakeEpisode(payloads, order)

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        def resume(bundle, _context):
            order.append("resume")
            return True

        controller = ProtectedBrowserFallback(
            lambda *_: episode, resume, clock=lambda: 100,
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/10", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(5))
                self.assertEqual(scheduler.get(task_id).state,
                                 DownloadState.COMPLETED,
                                 scheduler.get(task_id).error)
                self.assertEqual(
                    order[:2], ["request_capture", "capture"],
                    "the observation window must open before the first wait",
                )
                self.assertIn("resume", order)
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_same_task_handoff_ranks_primary_media_and_commits_after_transfer_start(self):
        task_id = "00000000-0000-4000-8000-000000000301"
        order = []
        episode = FakeEpisode(self._payload(), order)
        bundles = []

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        def resume(bundle, _context):
            order.append("resume")
            self.assertFalse(episode.committed)
            bundles.append(bundle)
            return True

        controller = ProtectedBrowserFallback(lambda *_: episode, resume, clock=lambda: 100)
        executor = AutomaticFallbackExecutor(direct, controller)
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(TaskStore(Path(directory) / "tasks.json"), executor)
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/1", "video",
                    {"browser_fallback_enabled": True, "quality": "1080p"}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(scheduler.get(task_id).state, DownloadState.COMPLETED, scheduler.get(task_id).error)
                self.assertEqual(len(bundles), 1)
                self.assertEqual(bundles[0].task_id, task_id)
                self.assertEqual(bundles[0].safe_summary()["media_host"], "cdn.example.test")
                self.assertTrue(bundles[0].media_url.startswith("https://cdn.example.test/main.m3u8"))
                self.assertEqual(order, ["request_capture", "capture", "resume", "commit", "close"])
                states = [event.data.get("state") for event in scheduler.events.snapshot()
                          if event.kind == "task_state"]
                self.assertIn(DownloadState.BROWSER_WAITING_FOR_MEDIA.value, states)
                self.assertIn(DownloadState.HANDOFF_VALIDATING.value, states)
                self.assertIn(DownloadState.DOWNLOADER_RESUMED.value, states)
                self.assertEqual([event.task_id for event in scheduler.events.snapshot()
                                  if event.kind == "task_added"], [task_id])
                text = repr(scheduler.events.snapshot())
                self.assertNotIn("secret-cookie", text)
                self.assertNotIn("media-token", text)
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_fast_direct_failure_routes_to_browser_fallback_same_task(self):
        """A fast direct-path failure in a browser-recoverable category (e.g.
        Cloudflare 403 then an extractor-level failure) continues on the SAME
        task through the automatic protected-browser fallback instead of
        becoming terminal ERROR with a manual Retry."""
        task_id = "00000000-0000-4000-8000-000000000305"
        order = []
        episode = FakeEpisode(self._payload(), order)
        bundles = []

        def direct(_record, _context):
            raise DirectPathEligibleForFallback(
                "Direct extraction failed (cloudflare); Browser Fallback eligible",
                category="cloudflare",
            )

        def resume(bundle, _context):
            order.append("resume")
            self.assertFalse(episode.committed)
            bundles.append(bundle)
            return True

        controller = ProtectedBrowserFallback(lambda *_: episode, resume, clock=lambda: 100)
        executor = AutomaticFallbackExecutor(direct, controller)
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(TaskStore(Path(directory) / "tasks.json"), executor)
            try:
                scheduler.submit(TaskSpec.create(
                    "https://jav.guru.example/watch/1", "video",
                    {"browser_fallback_enabled": True, "quality": "1080p"},
                    task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(scheduler.get(task_id).state, DownloadState.COMPLETED,
                                 scheduler.get(task_id).error)
                self.assertEqual(len(bundles), 1)
                self.assertEqual(bundles[0].task_id, task_id)
                self.assertEqual(order, ["request_capture", "capture", "resume", "commit", "close"])
                states = [event.data.get("state") for event in scheduler.events.snapshot()
                          if event.kind == "task_state"]
                self.assertIn(DownloadState.DIRECT_FAILED_ELIGIBLE_FOR_FALLBACK.value, states)
                self.assertIn(DownloadState.BROWSER_STARTING.value, states)
                self.assertIn(DownloadState.BROWSER_WAITING_FOR_MEDIA.value, states)
                self.assertEqual([event.task_id for event in scheduler.events.snapshot()
                                  if event.kind == "task_added"], [task_id])
                log_messages = [event.message for event in scheduler.events.snapshot()
                                if event.kind == "log"]
                self.assertTrue(
                    any("cloudflare" in message for message in log_messages),
                    log_messages,
                )
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_fast_direct_failure_stays_one_failed_task_when_fallback_disabled(self):
        task_id = "00000000-0000-4000-8000-000000000306"
        browser_calls = []

        def direct(_record, _context):
            raise DirectPathEligibleForFallback(
                "Direct extraction failed (http); Browser Fallback eligible",
                category="http",
            )

        executor = AutomaticFallbackExecutor(
            direct, lambda *_: browser_calls.append("unexpected"),
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(TaskStore(Path(directory) / "tasks.json"), executor)
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/disabled", "video",
                    {"browser_fallback_enabled": False}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_state(task_id, DownloadState.FAILED, 2))
                self.assertEqual(browser_calls, [])
                self.assertIn("http", scheduler.get(task_id).error)
                self.assertEqual([event.task_id for event in scheduler.events.snapshot()
                                  if event.kind == "task_added"], [task_id])
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_failed_handoff_recovers_with_another_candidate_without_requeue(self):
        task_id = "00000000-0000-4000-8000-000000000302"
        order = []
        episode = FakeEpisode(self._payload(), order)
        attempted = []

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        def resume(bundle, _context):
            attempted.append(bundle.candidate_id)
            order.append("resume")
            return len(attempted) == 2

        controller = ProtectedBrowserFallback(lambda *_: episode, resume, clock=lambda: 100)
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/2", "video", {}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(scheduler.get(task_id).state, DownloadState.COMPLETED, scheduler.get(task_id).error)
                self.assertEqual(len(attempted), 2)
                self.assertNotEqual(attempted[0], attempted[1])
                recovering = [event for event in scheduler.events.snapshot()
                               if event.data.get("state") == DownloadState.FALLBACK_RECOVERING.value]
                self.assertEqual(len(recovering), 1)
                self.assertEqual([event.task_id for event in scheduler.events.snapshot()
                                  if event.kind == "task_added"], [task_id])
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_ambiguous_automatic_fallback_walks_ranked_candidates_without_selector(self):
        """A metadata-starved payload (many similar candidates) stays ambiguous;
        without an interactive selector the automatic path must advance through
        the stabilized ranked list instead of failing on BrowserSelectionRequired.
        Mirrors the live hostile-page capture shape (equal-scoring HLS and
        segment-style mp4 candidates with no width/height/segment metadata)."""
        task_id = "00000000-0000-4000-8000-000000000303"
        order = []
        payload = {
            "ok": True,
            "blocked_popup_count": 0,
            "blocked_navigation_count": 0,
            "rejected_junk_count": 0,
            "dropped_request_count": 0,
            "user_agent": "secret-agent",
            "referer": "https://example.test/watch",
            "cookies": [],
            "media_candidates": [
                {"url": "https://cdn.example.test/b-hls-01/100/100_240p_h264_1_aaa_1786713000.mp4",
                 "content_type": "video/mp4"},
                {"url": "https://cdn.example.test/b-hls-02/200/200_240p_h264_1_bbb_1786713001.mp4",
                 "content_type": "video/mp4"},
                {"url": "https://edge.example.test/hls/100/master/100_240p.m3u8",
                 "content_type": "application/vnd.apple.mpegurl"},
                {"url": "https://edge.example.test/hls/200/master/200_240p.m3u8",
                 "content_type": "application/vnd.apple.mpegurl"},
            ],
        }

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        attempted = []

        def resume(bundle, _context):
            order.append("resume")
            attempted.append(bundle.media_url)
            return len(attempted) >= 2  # first ranked candidate fails validation

        controller = ProtectedBrowserFallback(
            lambda *_: FakeEpisode(payload, order), resume, clock=lambda: 100,
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/3", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(
                    scheduler.get(task_id).state, DownloadState.COMPLETED,
                    scheduler.get(task_id).error,
                )
                self.assertEqual(len(attempted), 2)
                self.assertNotEqual(attempted[0], attempted[1])
                self.assertEqual(
                    [event.task_id for event in scheduler.events.snapshot()
                     if event.kind == "task_added"],
                    [task_id],
                )
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_empty_first_capture_requests_fresh_snapshot_from_open_browser(self):
        """When the first live snapshot contains no usable candidates (e.g. only
        an early ad/interstitial), the automatic path must request another
        snapshot from the still-open browser instead of failing or closing."""
        task_id = "00000000-0000-4000-8000-000000000304"
        order = []
        empty = {"ok": True, "capture_seq": 1, "media_candidates": [], "cookies": []}
        good = dict(self._payload())
        good["capture_seq"] = 2
        episode = SequencedFakeEpisode([empty, good], order)

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        def resume(bundle, _context):
            order.append("resume")
            return True

        controller = ProtectedBrowserFallback(lambda *_: episode, resume, clock=lambda: 100)
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/4", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(scheduler.get(task_id).state, DownloadState.COMPLETED,
                                 scheduler.get(task_id).error)
                self.assertEqual(order, ["request_capture", "capture", "request_capture", "capture",
                                         "resume", "commit", "close"])
                self.assertEqual([event.task_id for event in scheduler.events.snapshot()
                                  if event.kind == "task_added"], [task_id])
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_empty_snapshot_before_widget_cluster_waits_then_finds_episode(self):
        """The live JAV.GURU page renders its sidebar live-cams 15-30 s after
        load.  A first snapshot taken before ANY media exists must not be
        treated as terminal ("No playable media observed"); the fallback must
        keep the browser open and re-capture until the widget cluster appears
        and then the requested episode media after the interaction."""
        task_id = "00000000-0000-4000-8000-000000000307"
        order = []
        empty = {"ok": True, "capture_seq": 1, "media_candidates": [], "cookies": []}
        widgets = self._widget_cluster_payload(2, with_episode=False)
        widgets3 = self._widget_cluster_payload(3, with_episode=False)
        episode_payload = self._widget_cluster_payload(4, with_episode=True)
        episode = SequencedFakeEpisode([empty, widgets, widgets3, episode_payload], order)

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        def resume(bundle, _context):
            order.append("resume")
            return True

        controller = ProtectedBrowserFallback(
            lambda *_: episode, resume, clock=lambda: 100,
            interaction_wait_seconds=8.0,
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/7", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(12))
                self.assertEqual(scheduler.get(task_id).state, DownloadState.COMPLETED,
                                 scheduler.get(task_id).error)
                # Empty snapshot -> fresh capture (widgets) -> fresh capture
                # (widgets) -> fresh capture (episode after interaction).
                self.assertEqual(order, ["request_capture", "capture", "request_capture", "capture",
                                         "request_capture", "capture",
                                         "request_capture", "capture",
                                         "resume", "commit", "close"])
                self.assertNotIn("No playable media was observed",
                                 scheduler.get(task_id).error or "")
                self.assertEqual([event.task_id for event in scheduler.events.snapshot()
                                  if event.kind == "task_added"], [task_id])
            finally:
                self.assertTrue(scheduler.shutdown())

    def _widget_cluster_payload(self, capture_seq, with_episode=False):
        """A page whose visible DOM media is a cluster of small autoplay
        widgets (sidebar live-cams); the requested player is a nested frame
        whose stream appears only after the user interacts."""
        candidates = [
            {
                "url": f"https://cdn.example.test/cam{capture_seq}.m3u8?tok={capture_seq}",
                "content_type": "application/vnd.apple.mpegurl",
                "user_started": False, "request_count": 3, "first_seen_seq": 1,
            },
            {
                "url": f"https://cdn.example.test/cam{capture_seq}b.m3u8?tok={capture_seq}",
                "content_type": "application/vnd.apple.mpegurl",
                "user_started": False, "request_count": 3, "first_seen_seq": 1,
            },
        ]
        if with_episode:
            candidates.append({
                "url": "https://cdn.example.test/episode.m3u8?tok=episode",
                "content_type": "application/vnd.apple.mpegurl",
                "user_started": True, "request_count": 4, "first_seen_seq": 2,
                "duration_seconds": 9575, "width": 1920, "height": 1080,
                "status": 200,
            })
            candidates.append({
                "url": "https://cdn.example.test/episode-seg-1.ts",
                "content_type": "video/mp2t",
                "segment_parent_url": "https://cdn.example.test/episode.m3u8?tok=episode",
                "first_seen_seq": 2, "content_length": 500000,
            })
            candidates.append({
                "url": "https://cdn.example.test/episode-seg-2.ts",
                "content_type": "video/mp2t",
                "segment_parent_url": "https://cdn.example.test/episode.m3u8?tok=episode",
                "first_seen_seq": 2, "content_length": 500000,
            })
        return {
            "ok": True,
            "capture_seq": capture_seq,
            "autoplay_widget_page": True,
            "media_candidates": candidates,
            "cookies": [],
            "blocked_popup_count": 0,
            "blocked_navigation_count": 0,
            "rejected_junk_count": 0,
            "dropped_request_count": 0,
            "user_agent": "agent",
            "referer": "https://example.test/watch",
        }

    def test_autoplay_widget_cluster_waits_for_requested_media_after_interaction(self):
        """When the page autoplays a cluster of small widget videos and the
        first capture sees only those widgets, the automatic path must keep
        the browser open and re-capture (bounded) until the requested media
        appears after the user interaction - then pick the requested media
        over the widgets on the SAME task."""
        task_id = "00000000-0000-4000-8000-000000000305"
        order = []
        first = self._widget_cluster_payload(1, with_episode=False)
        second = self._widget_cluster_payload(2, with_episode=True)
        episode = SequencedFakeEpisode([first, second], order)

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        def resume(bundle, _context):
            order.append("resume")
            return True

        controller = ProtectedBrowserFallback(lambda *_: episode, resume, clock=lambda: 100)
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/5", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(scheduler.get(task_id).state, DownloadState.COMPLETED,
                                 scheduler.get(task_id).error)
                # The widget-only capture triggered a fresh live snapshot from
                # the still-open browser instead of committing to a sidebar cam.
                self.assertEqual(order, ["request_capture", "capture", "request_capture", "capture",
                                         "resume", "commit", "close"])
                states = [event.data.get("state") for event in scheduler.events.snapshot()
                          if event.kind == "task_state"]
                self.assertIn(DownloadState.BROWSER_WAITING_FOR_MEDIA.value, states)
                self.assertEqual([event.task_id for event in scheduler.events.snapshot()
                                  if event.kind == "task_added"], [task_id])
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_autoplay_widget_cluster_without_requested_media_commits_after_bounded_wait(self):
        """If no requested media ever appears, the interaction wait is bounded:
        the fallback commits to the best available candidate instead of
        waiting forever or failing the whole task."""
        task_id = "00000000-0000-4000-8000-000000000306"
        order = []
        payloads = [self._widget_cluster_payload(seq) for seq in range(1, 80)]
        episode = SequencedFakeEpisode(payloads, order)

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        def resume(bundle, _context):
            order.append("resume")
            return True

        controller = ProtectedBrowserFallback(
            lambda *_: episode, resume, clock=lambda: 100,
            interaction_wait_seconds=6.0,
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/6", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id,
                ))
                # Paced re-captures (0.1 s/round at interaction_wait_seconds=6)
                # make the 68-round cap take ~7 s of real time.
                self.assertTrue(scheduler.wait_for_idle(15))
                self.assertEqual(scheduler.get(task_id).state, DownloadState.COMPLETED,
                                 scheduler.get(task_id).error)
                # Bounded: the deadline governs (45 s in production), and the
                # round cap max(16, 6*10+8)=68 is a pure infinite-loop guard.
                # The first request opens the observation window before any
                # DOM-visible playback; the other 68 are paced re-captures.
                re_captures = order.count("request_capture")
                self.assertEqual(re_captures, 69)
                self.assertIn("resume", order)
                self.assertTrue(episode.committed)
                self.assertEqual([event.task_id for event in scheduler.events.snapshot()
                                  if event.kind == "task_added"], [task_id])
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_widget_shaped_cams_alone_never_commit_before_requested_media(self):
        """Regression B (packaged): when the DOM cluster heuristic misses the
        sidebar live-cams (autoplay_widget_page=False - the cams can render
        inside a nested frame), the observed candidates are still generically
        widget-shaped (numeric stream id + rendition suffix).  The fallback
        must NOT commit one of those cams as the requested episode: it keeps
        waiting (bounded) while only widget-shaped media exists, and selects
        the requested episode master the moment it appears."""
        cam_url = (
            "https://media-hls.example/hls/263361383/263361383_240p.m3u8?tok=cam"
        )
        cam2_url = (
            "https://media-hls.example/hls/225808613/225808613_240p.m3u8?tok=cam2"
        )
        episode_url = (
            "https://javclan.example/stream/okZDaRbB06zoatA/master.m3u8?tok=ep"
        )

        def cam_payload(seq):
            return {
                "ok": True, "capture_seq": seq,
                "autoplay_widget_page": False,
                "media_candidates": [
                    {
                        "url": cam_url, "content_type": "application/vnd.apple.mpegurl",
                        "user_started": True, "request_count": 3, "first_seen_seq": 1,
                    },
                    {
                        "url": cam2_url, "content_type": "application/vnd.apple.mpegurl",
                        "user_started": True, "request_count": 3, "first_seen_seq": 1,
                    },
                ],
                "cookies": [], "blocked_popup_count": 0,
                "blocked_navigation_count": 0, "rejected_junk_count": 0,
                "dropped_request_count": 0, "user_agent": "agent",
                "referer": "https://example.test/watch",
            }

        task_id_a = "00000000-0000-4000-8000-000000000308"
        order = []
        # Case A: only widget-shaped cams forever -> bounded wait, then a clean
        # interaction-required failure.  NEVER a cam commit as the episode.
        payloads = [cam_payload(seq) for seq in range(1, 80)]
        episode = SequencedFakeEpisode(payloads, order)

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        def resume(bundle, _context):
            order.append("resume")
            return True

        controller = ProtectedBrowserFallback(
            lambda *_: episode, resume, clock=lambda: 100,
            interaction_wait_seconds=6.0,
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/8", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id_a,
                ))
                self.assertTrue(scheduler.wait_for_idle(15))
                # The wait held (bounded): many re-captures, then a clean
                # failure - the sidebar cam is NEVER committed as the episode.
                self.assertGreaterEqual(order.count("request_capture"), 10)
                self.assertNotIn("resume", order)
                self.assertFalse(episode.committed)
                self.assertEqual(scheduler.get(task_id_a).state, DownloadState.FAILED,
                                 scheduler.get(task_id_a).error)
                self.assertIn("sidebar/live-widget", scheduler.get(task_id_a).error)
            finally:
                self.assertTrue(scheduler.shutdown())

        # Case B: cams first, then the requested episode master appears after
        # the user interaction -> the episode is selected, not a cam.
        task_id_b = "00000000-0000-4000-8000-000000000309"
        order_b = []
        cam_only = cam_payload(1)
        episode_payload = dict(cam_payload(2))
        episode_payload["media_candidates"] = episode_payload["media_candidates"] + [
            {
                "url": episode_url, "content_type": "application/vnd.apple.mpegurl",
                "user_started": True, "request_count": 4, "first_seen_seq": 2,
                "duration_seconds": 9575, "width": 1920, "height": 1080,
                "status": 200,
            },
        ]
        episode_b = SequencedFakeEpisode([cam_only, episode_payload], order_b)
        bundles = []

        def resume_b(bundle, _context):
            order_b.append("resume")
            bundles.append(bundle)
            return True

        controller_b = ProtectedBrowserFallback(
            lambda *_: episode_b, resume_b, clock=lambda: 100,
            interaction_wait_seconds=30.0,
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller_b),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/9", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id_b,
                ))
                self.assertTrue(scheduler.wait_for_idle(12))
                self.assertEqual(scheduler.get(task_id_b).state, DownloadState.COMPLETED,
                                 scheduler.get(task_id_b).error)
                self.assertEqual(len(bundles), 1)
                self.assertTrue(bundles[0].media_url.startswith(
                    "https://javclan.example/stream/okZDaRbB06zoatA/master.m3u8"
                ), bundles[0].media_url)
                self.assertNotIn("263361383", bundles[0].media_url)
                # Browser-observation credit: the payload record's response
                # status/content-type must reach the handoff bundle so a
                # context-bound replay failure can be overridden downstream.
                self.assertEqual(bundles[0].observed_status, 200)
                self.assertEqual(bundles[0].observed_content_type,
                                 "application/vnd.apple.mpegurl")
                self.assertEqual(order_b, ["request_capture", "capture", "request_capture", "capture",
                                           "resume", "commit", "close"])
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_metadata_starved_payload_without_player_ids_ranks_manifest_over_fragment(self):
        """A capture payload without per-player attribution must not fabricate
        a shared player lineage (which would mark all but the last-observed
        candidate as replaced) - the coherent HLS manifest must win over a
        standalone init fragment, matching the live hostile-page shape."""
        task_id = "00000000-0000-4000-8000-000000000305"
        order = []
        payload = {
            "ok": True,
            "blocked_popup_count": 0,
            "blocked_navigation_count": 0,
            "rejected_junk_count": 0,
            "dropped_request_count": 0,
            "user_agent": "secret-agent",
            "referer": "https://example.test/watch",
            "cookies": [],
            "media_candidates": [
                # Deliberately NOT numeric-stream-id shaped: the numeric-id +
                # rendition-suffix shape is the generic sidebar live-widget
                # signature that keeps the interaction wait open (Regression B),
                # so the manifest-over-fragment ranking exercised here uses
                # plain HLS paths.
                {"url": "https://edge.example/hls/stream-a/master/stream-a_240p.m3u8",
                 "content_type": "application/vnd.apple.mpegurl"},
                {"url": "https://media.example/b-hls-05/stream-a/stream-a_240p.m3u8?psch=v2&pkey=opaque",
                 "content_type": "application/vnd.apple.mpegurl"},
                {"url": "https://media.example/b-hls-31/stream-b/stream-b_240p.m3u8?psch=v2&pkey=opaque",
                 "content_type": "application/vnd.apple.mpegurl"},
                {"url": "https://media.example/b-hls-05/stream-a/stream-a_240p_h264_init_Wewh5IFwPHzqWBCy.mp4",
                 "content_type": "video/mp4", "content_length": 1238},
                {"url": "https://media.example/b-hls-05/stream-a/stream-a_240p_h264_233_wxu_1786716171.mp4",
                 "content_type": "video/mp4", "content_length": 150669,
                 "segment_parent_url": "https://media.example/b-hls-05/stream-a/stream-a_240p.m3u8?psch=v2&pkey=opaque"},
                {"url": "https://media.example/b-hls-05/stream-a/stream-a_240p_h264_234_euy_1786716173.mp4",
                 "content_type": "video/mp4", "content_length": 143995,
                 "segment_parent_url": "https://media.example/b-hls-05/stream-a/stream-a_240p.m3u8?psch=v2&pkey=opaque"},
            ],
        }

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        bundles = []

        def resume(bundle, _context):
            order.append("resume")
            bundles.append(bundle)
            return True

        controller = ProtectedBrowserFallback(
            lambda *_: FakeEpisode(payload, order), resume, clock=lambda: 100,
        )
        with TemporaryDirectory() as directory:
            scheduler = TaskScheduler(
                TaskStore(Path(directory) / "tasks.json"),
                AutomaticFallbackExecutor(direct, controller),
            )
            try:
                scheduler.submit(TaskSpec.create(
                    "https://example.test/watch/5", "video",
                    {"browser_fallback_enabled": True}, task_id=task_id,
                ))
                self.assertTrue(scheduler.wait_for_idle(2))
                self.assertEqual(scheduler.get(task_id).state, DownloadState.COMPLETED,
                                 scheduler.get(task_id).error)
                # The coherent HLS manifest with segment evidence wins, never the
                # 1.2 KiB init fragment.
                self.assertEqual(len(bundles), 1)
                self.assertTrue(
                    bundles[0].media_url.split("?")[0].endswith(".m3u8"),
                    bundles[0].media_url,
                )
                self.assertNotIn("h264_init", bundles[0].media_url)
                self.assertEqual(
                    [event.task_id for event in scheduler.events.snapshot()
                     if event.kind == "task_added"],
                    [task_id],
                )
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_manual_close_before_handoff_is_refused(self):
        """A capture produced by a premature window close (manual_closed) must
        not hand off; the browser closes only after validated transfer start."""
        with TemporaryDirectory() as directory:
            result_path = Path(directory) / "capture.json"
            result_path.write_text(
                json.dumps({"ok": True, "manual_closed": True,
                            "media_candidates": [], "cookies": []}),
                encoding="utf-8",
            )
            episode = JsonFileBrowserEpisode(
                SimpleNamespace(poll=lambda: 0), result_path,
            )
            with self.assertRaisesRegex(BrowserFallbackError, "before validated handoff"):
                episode.capture(threading.Event())

    def test_capture_accepts_newer_snapshot_while_browser_is_open(self):
        """The app waits for a newer capture_seq while the browser is still
        open, proving capture is not a window-close side effect."""
        with TemporaryDirectory() as directory:
            result_path = Path(directory) / "capture.json"
            result_path.write_text(
                json.dumps({"ok": True, "capture_seq": 2,
                            "media_candidates": [], "cookies": []}),
                encoding="utf-8",
            )
            process = SimpleNamespace(poll=lambda: None)  # helper still running
            payload = JsonFileBrowserEpisode(process, result_path).capture(
                threading.Event(), since_seq=1,
            )
            self.assertEqual(payload["capture_seq"], 2)

    def test_capture_file_size_is_bounded_before_parsing(self):
        with TemporaryDirectory() as directory:
            result_path = Path(directory) / "capture.json"
            result_path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
            episode = JsonFileBrowserEpisode(
                SimpleNamespace(poll=lambda: 0), result_path,
            )
            with self.assertRaises(BrowserFallbackError):
                episode.capture(threading.Event())

            valid_path = Path(directory) / "valid.json"
            valid_path.write_text(
                '{"ok": true, "media_candidates": [], "cookies": []}',
                encoding="utf-8",
            )
            payload = JsonFileBrowserEpisode(
                SimpleNamespace(poll=lambda: 0), valid_path,
            ).capture(threading.Event())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["media_candidates"], [])

    def _single_media_payload(self, with_status):
        """One strong episode candidate; 'status' models the protected
        browser's confirmed HTTP 200 for the media resource itself."""
        record = {
            "url": "https://provider.example/stream/episode/master.m3u8?tok=1",
            "content_type": "application/vnd.apple.mpegurl",
            "user_started": True, "request_count": 4,
            "duration_seconds": 9575, "width": 1920, "height": 1080,
        }
        if with_status:
            record["status"] = 200
        return {
            "ok": True, "capture_seq": 1,
            "blocked_popup_count": 0, "blocked_navigation_count": 0,
            "rejected_junk_count": 0, "dropped_request_count": 0,
            "user_agent": "agent", "referer": "https://example.test/watch",
            "cookies": [], "media_candidates": [record],
        }

    def _run_exhausted_handoff(self, task_id, payload, resume):
        order = []
        episode = FakeEpisode(payload, order)

        def direct(_record, _context):
            raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                    eligible_for_fallback=True)

        controller = ProtectedBrowserFallback(
            lambda *_: episode, resume, clock=lambda: 100,
            interaction_wait_seconds=5.0,
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
                self.assertEqual(record.state, DownloadState.FAILED, record.error)
                return record.error, episode, order
            finally:
                self.assertTrue(scheduler.shutdown())

    def test_browser_observed_media_external_replay_rejected(self):
        """A candidate the protected browser fetched with HTTP 200 whose
        external transfer the server refuses (context-bound category) is
        browser-accessible but externally non-transferable: the task ends
        with that honest classification, and the candidate itself still
        records the true FAILED_HANDOFF lifecycle."""
        task_id = "00000000-0000-4000-8000-000000000311"
        attempts = []

        def resume(_bundle, _context):
            attempts.append(1)
            raise ExternalReplayRejected(
                "The protected browser fetched this media, but the media "
                "server rejected the independent transfer replay.")

        error, episode, order = self._run_exhausted_handoff(
            task_id, self._single_media_payload(with_status=True), resume)

        self.assertIn("browser-accessible but externally non-transferable", error)
        self.assertNotIn("No stable media candidate remains for handoff", error)
        self.assertGreaterEqual(len(attempts), 1)
        self.assertFalse(episode.committed)
        self.assertIn("close", order)

    def test_external_replay_rejection_without_browser_credit_keeps_classic_path(self):
        """Without confirmed browser-fetch credit (observed_status != 200)
        the same transfer failure must NOT produce the browser-accessible
        classification: the classic no-stable-candidate message stays."""
        task_id = "00000000-0000-4000-8000-000000000312"

        def resume(_bundle, _context):
            raise ExternalReplayRejected(
                "The protected browser fetched this media, but the media "
                "server rejected the independent transfer replay.")

        error, _episode, _order = self._run_exhausted_handoff(
            task_id, self._single_media_payload(with_status=False), resume)

        self.assertIn("No stable media candidate remains for handoff", error)
        self.assertNotIn("browser-accessible", error)


if __name__ == "__main__":
    unittest.main()
