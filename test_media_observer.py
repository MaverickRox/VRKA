"""Focused MediaObserverAdapter gate (VRKA 3.0 Phase 9).

Covers: artifact verification, prepare/install/status/version/health,
observation normalization from REAL Phase 6 captured shapes, malformed-input
rejection, dedupe, bounds, subscription lifecycle, idempotent shutdown, and
ingestion into the existing CandidateStore model.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))

from vrka_core import media_observer as mo  # noqa: E402
from vrka_core.media_observer import (  # noqa: E402
    OBSERVER_VERSION,
    MediaObserverAdapter,
    artifact_zip_path,
    prepare_extension_dir,
    verify_artifact,
)
from vrka_core.candidates import CandidateKind  # noqa: E402

# REAL observations captured by puemos during the Phase 6 real-site matrix
# (see lab/media_observer/reports/PHASE6-MATRIX.md).
REAL_HENTAIHAVEN_MASTER = {
    "id": "https://octopusmanifest.org/aa660925-64da-4309-a7d8-8bf90d29d417/playlist_vp9.m3u8",
    "uri": "https://octopusmanifest.org/aa660925-64da-4309-a7d8-8bf90d29d417/playlist_vp9.m3u8",
    "initiator": "https://hentaihaven.xxx/watch/kare-no-shiranai-himitsu-o-irete-the-animation/episode-1/",
    "pageTitle": "Kare no Shiranai Himitsu o Irete. The Animation Episode 1 - Hentai Haven",
    "createdAt": 1787553000000,
}
REAL_ANIKOTO_VARIANT = {
    "id": "https://cdn.watching.onl/anime/908e9281295d180348ec77afe6be6b01/20e1a2db46685c0bd690e0b0ba8bac1d/index-f1-v1-a1.m3u8",
    "uri": "https://cdn.watching.onl/anime/908e9281295d180348ec77afe6be6b01/20e1a2db46685c0bd690e0b0ba8bac1d/index-f1-v1-a1.m3u8",
    "initiator": "https://anikoto.cz/watch/sakamoto-days-sfdxz/ep-1",
    "pageTitle": "Watch Free! Sakamoto Days Episode 1 Online in HD - Anikoto",
    "createdAt": 1787599000000,
}
REAL_JAVGURU_VARIANT = {
    "id": "https://javclan.com/stream/VnvwpkpAOjbOeWawaURetA/kjhhiuahiuhgihdf/1787599438/73677491/index-f1-v1-a1.m3u8",
    "uri": "https://javclan.com/stream/VnvwpkpAOjbOeWawaURetA/kjhhiuahiuhgihdf/1787599438/73677491/index-f1-v1-a1.m3u8",
    "initiator": "https://jav.guru/1035117/snos-334-seto-kanna/",
    "pageTitle": "[SNOS-334] episode page",
    "createdAt": 1787599438000,
}


def _state_with(*entries):
    playlists = {}
    for i, e in enumerate(entries):
        key = e.get("id", "entry-%d" % i) if isinstance(e, dict) else "entry-%d" % i
        playlists[key] = e
    return {"local": {"state": {"playlists": {"playlists": playlists}}}}


class ArtifactTests(unittest.TestCase):
    def test_pinned_artifact_present_and_verified(self):
        zip_path = artifact_zip_path()
        if not zip_path.is_file():
            self.skipTest("pinned observer archive not present in this tree")
        self.assertTrue(verify_artifact(zip_path))

    def test_prepare_extension_dir_is_content_keyed_and_idempotent(self):
        zip_path = artifact_zip_path()
        if not zip_path.is_file():
            self.skipTest("pinned observer archive not present in this tree")
        with tempfile.TemporaryDirectory() as tmp:
            first = prepare_extension_dir(zip_path, tmp)
            self.assertTrue(first)
            self.assertTrue((Path(first) / "manifest.json").is_file())
            second = prepare_extension_dir(zip_path, tmp)
            self.assertEqual(first, second)

    def test_prepare_rejects_tampered_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake.zip"
            fake.write_bytes(b"not a zip")
            self.assertIsNone(prepare_extension_dir(fake, tmp))


class AdapterLifecycleTests(unittest.TestCase):
    def make_adapter(self):
        return MediaObserverAdapter(runtime_dir=self.tmp.name + "/ext")

    def setUp(self):
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = self._tmp_ctx
        self.adapter = self.make_adapter()

    def tearDown(self):
        self.adapter.close()
        self._tmp_ctx.cleanup()

    def test_version_constant(self):
        self.assertEqual(OBSERVER_VERSION, self.adapter.version())

    def test_status_fields(self):
        st = self.adapter.status()
        for key in ("project", "version", "license", "artifact_present",
                    "artifact_verified", "dir_present", "closed"):
            self.assertIn(key, st)
        self.assertEqual(st["version"], OBSERVER_VERSION)
        self.assertFalse(st["closed"])

    def test_install_without_runtime_dir_fails_cleanly(self):
        adapter = MediaObserverAdapter()
        info = adapter.install()
        self.assertFalse(info["installed"])
        self.assertTrue(info["error"])
        adapter.close()

    def test_install_health_uninstall_roundtrip(self):
        zip_path = artifact_zip_path()
        if not zip_path.is_file():
            self.skipTest("pinned observer archive not present in this tree")
        info = self.adapter.install()
        self.assertTrue(info["installed"], info.get("error"))
        self.assertTrue(Path(info["dir"]).is_dir())
        health = self.adapter.health()
        self.assertTrue(health["ok"], health)
        self.assertTrue(health["checks"]["artifact_sha256"])
        self.assertTrue(health["checks"]["mv3_service_worker"])
        removed = self.adapter.uninstall()
        self.assertTrue(removed["uninstalled"])
        self.assertEqual(self.adapter.prepared_dirs(), [])
        # Idempotent repeat.
        self.assertEqual(self.adapter.uninstall()["uninstalled"], False)


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MediaObserverAdapter()

    def tearDown(self):
        self.adapter.close()

    def test_real_phase6_observations_normalize(self):
        state = _state_with(REAL_HENTAIHAVEN_MASTER, REAL_ANIKOTO_VARIANT, REAL_JAVGURU_VARIANT)
        obs = self.adapter.enumerate_observations(state)
        self.assertEqual(len(obs), 3)
        urls = {o["url"] for o in obs}
        self.assertIn(REAL_HENTAIHAVEN_MASTER["uri"], urls)
        self.assertIn(REAL_ANIKOTO_VARIANT["uri"], urls)
        self.assertIn(REAL_JAVGURU_VARIANT["uri"], urls)
        for o in obs:
            self.assertEqual(o["content_type"], "application/vnd.apple.mpegurl")
            self.assertTrue(o["user_started"])
            self.assertEqual(o["source"], "media_observer")
            self.assertTrue(o["timestamp"] > 1_700_000_000)

    def test_malformed_entries_rejected(self):
        bad = [
            None,
            "string",
            42,
            {},
            {"url": ""},
            {"url": "javascript:alert(1)"},
            {"url": "file:///etc/passwd"},
            {"url": "//relative/m3u8"},
            {"url": "http://" + "x" * 3000 + ".m3u8"},
            {"url": "https://cdn.example.com/video.mp4"},  # non-m3u8 unexpected
            {"uri": ["list"]},
        ]
        for entry in bad:
            self.assertEqual(self.adapter.enumerate_observations(_state_with(entry)), [],
                             "entry should be rejected: %r" % (entry,))

    def test_duplicates_collapse(self):
        state = _state_with(REAL_ANIKOTO_VARIANT, dict(REAL_ANIKOTO_VARIANT))
        obs = self.adapter.enumerate_observations(state)
        self.assertEqual(len(obs), 1)

    def test_output_bounded(self):
        entries = []
        for i in range(80):
            entries.append({"id": "https://cdn.example.com/%03d/master.m3u8" % i,
                            "createdAt": 1787553000000})
        obs = self.adapter.enumerate_observations(_state_with(*entries))
        self.assertLessEqual(len(obs), 64)

    def test_non_dump_shape_returns_empty(self):
        for junk in (None, [], "x", 7, {"playlists": {"playlists": {}}}):
            self.assertEqual(self.adapter.enumerate_observations(junk), [])


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MediaObserverAdapter()

    def tearDown(self):
        self.adapter.close()

    def test_subscribe_receive_and_close(self):
        received = []
        registered = self.adapter.subscribe_to_observations(lambda obs: received.append(obs))
        self.assertTrue(registered)
        state = _state_with(REAL_HENTAIHAVEN_MASTER)
        obs = self.adapter.enumerate_observations(state)
        delivered = self.adapter.emit(obs)
        self.assertEqual(delivered, 1)
        self.assertEqual(received[0], obs)
        self.adapter.close()
        self.assertEqual(self.adapter.emit(obs), 0)

    def test_close_is_idempotent(self):
        self.adapter.subscribe_to_observations(lambda *a: None)
        self.adapter.close()
        self.adapter.close()
        self.assertTrue(self.adapter.status()["closed"])

    def test_subscribe_rejects_after_close_and_non_callables(self):
        self.adapter.close()
        self.assertFalse(self.adapter.subscribe_to_observations(lambda obs: None))
        other = MediaObserverAdapter()
        try:
            self.assertFalse(other.subscribe_to_observations("not-callable"))
        finally:
            other.close()

    def test_subscriber_exceptions_do_not_break_emit(self):
        def boom(obs):
            raise RuntimeError("subscriber fault")

        received = []
        self.adapter.subscribe_to_observations(boom)
        self.adapter.subscribe_to_observations(lambda obs: received.append(obs))
        n = self.adapter.emit([{"url": "https://a/x.m3u8"}])
        self.assertEqual(n, 1)
        self.assertEqual(received, [[{"url": "https://a/x.m3u8"}]])


class CandidateModelIngestionTests(unittest.TestCase):
    """Adapter output must feed the EXISTING candidate model unchanged."""

    def test_normalized_observation_ingests_into_candidate_store(self):
        from vrka_core.candidates import CandidateStore

        adapter = MediaObserverAdapter()
        try:
            state = _state_with(REAL_HENTAIHAVEN_MASTER, REAL_ANIKOTO_VARIANT, REAL_JAVGURU_VARIANT)
            store = CandidateStore()
            ingested_ids = []
            for o in adapter.enumerate_observations(state):
                candidate = store.observe(
                    url=o["url"],
                    content_type=o["content_type"],
                    timestamp=o["timestamp"],
                    user_started=o["user_started"],
                )
                if candidate is not None:
                    ingested_ids.append(candidate.candidate_id)
            self.assertEqual(len(ingested_ids), 3)
            values = list(store.values())
            kinds = {v.kind for v in values}
            self.assertTrue(any(k == CandidateKind.HLS for k in kinds), kinds)
            # Duplicate re-ingestion stays one identity per canonical URL.
            again = store.observe(url=REAL_ANIKOTO_VARIANT["uri"],
                                  content_type="application/vnd.apple.mpegurl",
                                  user_started=True)
            self.assertEqual(again.candidate_id,
                             next(v.candidate_id for v in values
                                  if v.current_url == REAL_ANIKOTO_VARIANT["uri"]))
        finally:
            adapter.close()


class ProductionWiringPinTest(unittest.TestCase):
    """The protected browser prepares and installs the observer fail-open."""

    def test_helper_prepares_observer_next_to_ubol(self):
        source = (APP_ROOT / "vrka_downloader.py").read_text(encoding="utf-8")
        self.assertIn("_prepare_media_observer()", source)
        self.assertIn('popup_stats["observer"]', source)
        self.assertIn("AddBrowserExtensionAsync(observer_info[\"dir\"])", source)
        ubol_idx = source.index("_prepare_ubol_extension_dir()")
        obs_idx = source.index("observer_info = _prepare_media_observer()")
        self.assertGreater(obs_idx, ubol_idx)


class UpdaterTests(unittest.TestCase):
    """Official-source updater: checksum, atomic replace, rollback safety."""

    def _zip_bytes(self, version):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({
                "manifest_version": 3, "version": version,
                "background": {"service_worker": "background.js"},
            }))
            zf.writestr("background.js", "// worker")
        return buf.getvalue()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp()) / "puemos-hls-downloader"
        self.root.mkdir(parents=True)
        # Seed the pinned artifact copy (the real one, hash-valid).
        src = artifact_zip_path()
        if src.is_file():
            shutil.copyfile(src, self.root / src.name)
        self.artifact = self.root / src.name
        self.pre_hash = (
            hashlib.sha256(self.artifact.read_bytes()).hexdigest()
            if self.artifact.exists() else None)

    def tmp(self):
        return Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_check_network_error_is_soft(self):
        info = mo.check_for_update(fetch_json=lambda url: (_ for _ in ()).throw(IOError("down")))
        self.assertIn("error", info)

    def test_check_already_current(self):
        info = mo.check_for_update(fetch_json=lambda url: {
            "tag_name": "v5.5.0",
            "assets": [{"name": mo.OBSERVER_ASSET_NAME,
                        "browser_download_url": "https://example/x.zip",
                        "digest": "sha256:" + "a" * 64}]})
        self.assertFalse(info["update_available"])
        self.assertEqual(info["available_version"], "5.5.0")

    def test_apply_update_happy_path_replaces_atomically(self):
        new_blob = self._zip_bytes("5.6.0")
        digest = hashlib.sha256(new_blob).hexdigest()
        calls = {}
        def fetch_json(url):
            calls["json"] = url
            return {"tag_name": "v5.6.0",
                    "assets": [{"name": mo.OBSERVER_ASSET_NAME,
                                "browser_download_url": "https://official/x.zip",
                                "digest": "sha256:" + digest}]}
        def fetch_bytes(url):
            calls["bytes"] = url
            return new_blob
        result = mo.apply_update(artifacts_root=self.root,
                                 fetch_json=fetch_json, fetch_bytes=fetch_bytes)
        self.assertTrue(result["updated"], result)
        self.assertEqual(result["installed_version"], "5.6.0")
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), digest)
        self.assertEqual(calls["json"], mo.UPSTREAM_RELEASE_API)
        self.assertEqual(calls["bytes"], "https://official/x.zip")
        self.assertNotIn(".updating", {p.name for p in self.root.iterdir()})

    def test_apply_update_checksum_mismatch_rolls_back_to_previous(self):
        def fetch_json(url):
            return {"tag_name": "v9.9.9",
                    "assets": [{"name": mo.OBSERVER_ASSET_NAME,
                                "browser_download_url": "https://official/x.zip",
                                "digest": "sha256:" + "b" * 64}]}
        result = mo.apply_update(artifacts_root=self.root, fetch_json=fetch_json,
                                 fetch_bytes=lambda url: b"tampered bytes")
        self.assertFalse(result["updated"])
        self.assertIn("checksum mismatch", result.get("error", ""))
        if self.pre_hash:
            self.assertEqual(
                hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.pre_hash)

    def test_apply_update_malformed_archive_leaves_previous_usable(self):
        digest = hashlib.sha256(b"JUNKJUNK").hexdigest()
        result = mo.apply_update(artifacts_root=self.root,
                                 fetch_json=lambda url: {
                                     "tag_name": "v9.9.9",
                                     "assets": [{"name": mo.OBSERVER_ASSET_NAME,
                                                 "browser_download_url": "u",
                                                 "digest": "sha256:" + digest}]},
                                 fetch_bytes=lambda url: b"JUNKJUNK")
        self.assertFalse(result["updated"])
        if self.pre_hash:
            self.assertEqual(
                hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.pre_hash)
        adapter = MediaObserverAdapter(artifacts_root=self.root,
                                       runtime_dir=self.tmp() / "ext")
        try:
            self.assertTrue(adapter.install()["installed"])
        finally:
            adapter.close()

    def test_update_never_touches_vrka_state_outside_artifact(self):
        before = sorted(p.name for p in self.tmp().iterdir())
        mo.apply_update(artifacts_root=self.root,
                        fetch_json=lambda url: {"error": "offline"},
                        fetch_bytes=lambda url: b"")
        self.assertEqual(sorted(p.name for p in self.tmp().iterdir()), before)


class ProductionPackagingPins(unittest.TestCase):
    """The observer must ride inside the frozen bundle and installer."""

    def test_spec_bundles_observer_artifact(self):
        spec = (APP_ROOT / "VRKA-Windows.spec").read_text(encoding="utf-8")
        self.assertIn("third_party/media_observer/puemos-hls-downloader", spec)
        self.assertIn(mo.OBSERVER_ARTIFACT_FILENAME, spec)

    def test_helper_resolves_bundled_observer_and_reports_payload_field(self):
        source = (APP_ROOT / "vrka_downloader.py").read_text(encoding="utf-8")
        self.assertIn("_bundled_observer_zip()", source)
        self.assertIn("resource_path(", source)
        self.assertIn('"observer_extension"', source)

    def test_settings_card_present(self):
        source = (APP_ROOT / "vrka_downloader.py").read_text(encoding="utf-8")
        self.assertIn('"Media Observer"', source)
        self.assertIn("start_observer_check", source)
        self.assertIn("start_observer_update", source)
        self.assertIn("check_for_update", source)
        self.assertIn("apply_update", source)


if __name__ == "__main__":
    unittest.main()
