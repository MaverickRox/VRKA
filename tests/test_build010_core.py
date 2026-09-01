import unittest

from vrka_core import (
    CandidateKind,
    CandidateRanker,
    CandidateStore,
    CoreEvent,
    DownloadState,
    EventBus,
    HandoffBundle,
    TaskRecord,
    TaskSpec,
)
from vrka_core.candidates import is_segment


class CoreDomainTests(unittest.TestCase):
    def test_task_spec_is_deeply_immutable_and_drops_runtime_options(self):
        source = {"quality": "1080p", "range": [1, 3], "_staging_dir": "secret"}
        spec = TaskSpec.create(
            "https://example.test/watch/1", "video", source,
            task_id="00000000-0000-4000-8000-000000000010", now=10,
        )
        source["range"].append(9)
        self.assertEqual(spec.options["range"], (1, 3))
        self.assertNotIn("_staging_dir", spec.options)
        with self.assertRaises(TypeError):
            spec.options["quality"] = "720p"

    def test_one_task_identity_survives_fallback_transitions(self):
        spec = TaskSpec.create(
            "https://example.test/watch/2", "video", {},
            task_id="00000000-0000-4000-8000-000000000011",
        )
        task = TaskRecord.pending(spec)
        for state in (
            DownloadState.DIRECT_ATTEMPT,
            DownloadState.DIRECT_FAILED_ELIGIBLE_FOR_FALLBACK,
            DownloadState.BROWSER_STARTING,
            DownloadState.BROWSER_WAITING_FOR_MEDIA,
            DownloadState.HANDOFF_PREPARING,
            DownloadState.HANDOFF_VALIDATING,
            DownloadState.DOWNLOADER_RESUMED,
            DownloadState.DOWNLOAD_RUNNING,
            DownloadState.POST_PROCESSING,
            DownloadState.COMPLETED,
        ):
            task.transition(state)
        self.assertEqual(task.task_id, spec.task_id)
        self.assertTrue(task.consume_terminal_event())
        self.assertFalse(task.consume_terminal_event())

    def test_cancel_request_is_idempotent(self):
        task = TaskRecord.pending(TaskSpec.create(
            "https://example.test/watch/3", "audio", {},
            task_id="00000000-0000-4000-8000-000000000012",
        ))
        self.assertTrue(task.request_cancel())
        self.assertFalse(task.request_cancel())
        task.transition(DownloadState.CANCELLED)
        self.assertFalse(task.request_cancel())

    def test_candidate_ranker_supersedes_early_ad_like_media(self):
        store = CandidateStore(max_candidates=8)
        early = store.observe(
            url="https://ads.example.test/pre-roll.mp4?token=1",
            content_type="video/mp4", timestamp=0, player_id="main",
            duration_seconds=8, width=320, height=180, nuisance_score=8,
            popup_context=True,
        )
        primary = store.observe(
            url="https://cdn.example.test/feature.m3u8?token=2",
            content_type="application/vnd.apple.mpegurl", timestamp=3,
            player_id="main", primary_player=True, user_started=True,
            duration_seconds=1800, width=1920, height=1080, playing=True,
            required_header_names=("referer", "user-agent"),
        )
        store.observe(
            url=primary.current_url, content_type=primary.content_type, timestamp=6,
            player_id="main", primary_player=True, user_started=True, playing=True,
        )
        store.observe(
            url="https://cdn.example.test/segment-1.ts", timestamp=6.2,
            player_id="main", segment_parent_url=primary.current_url,
        )
        store.observe(
            url="https://cdn.example.test/segment-2.ts", timestamp=6.4,
            player_id="main", segment_parent_url=primary.current_url,
        )
        decision = CandidateRanker().decide(store.values(), now=9)
        self.assertEqual(decision.selected_candidate_id, primary.candidate_id)
        self.assertNotEqual(decision.selected_candidate_id, early.candidate_id)

    def test_codec_segment_mp4_urls_collapse_into_their_manifest(self):
        # CDNs expose HLS segments as sequence-numbered codec .mp4 URLs; these
        # must never become standalone transfer candidates.
        self.assertTrue(is_segment(
            "https://media.example/hls/254304457/254304457_240p_h264_164_abcdef_1786715515.mp4",
            "video/mp4",
        ))
        self.assertTrue(is_segment(
            "https://media.example/hls/245475950/245475950_240p_h264_init_Wewh5IFwPHzqWBCy.mp4",
            "video/mp4",
        ))
        self.assertFalse(is_segment(
            "https://media.example/hls/254304457/standalone.mp4", "video/mp4",
        ))
        store = CandidateStore(max_candidates=8)
        primary = store.observe(
            url="https://media.example/hls/254304457/254304457_240p.m3u8",
            content_type="application/vnd.apple.mpegurl", timestamp=1,
            player_id="main", primary_player=True,
        )
        for index, token in enumerate(("aa", "bb", "cc"), 1):
            store.observe(
                url=f"https://media.example/hls/254304457/254304457_240p_h264_16{index}_{token}_1786715515.mp4",
                content_type="video/mp4", timestamp=2 + index,
                player_id="main", segment_parent_url=primary.current_url,
            )
        self.assertEqual(len(store.values()), 1)
        self.assertGreaterEqual(primary.segment_count, 3)

    def test_candidate_storage_and_event_history_are_bounded(self):
        store = CandidateStore(max_candidates=4)
        for index in range(12):
            store.observe(
                url=f"https://cdn{index}.example.test/video-{index}.mp4",
                content_type="video/mp4", timestamp=index,
            )
        self.assertLessEqual(len(store.values()), 4)
        bus = EventBus(max_events=16)
        for index in range(30):
            bus.emit(CoreEvent("log", message=str(index)))
        self.assertEqual(len(bus.snapshot()), 16)
        self.assertEqual(bus.snapshot()[0].message, "14")

    def test_handoff_repr_and_safe_summary_do_not_expose_secrets(self):
        bundle = HandoffBundle(
            task_id="task-1", candidate_id="candidate-1",
            media_url="https://cdn.example.test/video.m3u8?token=secret",
            media_kind=CandidateKind.HLS,
            user_agent="secret-agent", referer="https://private.example.test/",
            cookies=({"name": "session", "value": "secret-cookie"},),
            headers={"Authorization": "Bearer secret"},
        )
        combined = repr(bundle) + repr(bundle.safe_summary())
        self.assertNotIn("secret-cookie", combined)
        self.assertNotIn("Bearer secret", combined)
        self.assertNotIn("token=secret", combined)
        self.assertEqual(bundle.safe_summary()["media_host"], "cdn.example.test")


if __name__ == "__main__":
    unittest.main()
