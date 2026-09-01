"""MediaObserverAdapter - read-only bridge from the pinned third-party media
observer extension (puemos/hls-downloader v5.5.0 MV3-Chromium) into VRKA's
existing observation/candidate model.

Boundary contract:

- This module owns ALL observer-specific knowledge: artifact location,
  provenance, unpacked-directory preparation, persisted-state parsing,
  observation normalization.
- It NEVER talks to the network, never launches processes, never touches the
  browser UI thread, and never uses debug channels.  Actual profile
  installation is performed by the protected-browser helper through the
  official WebView2 ``Profile.AddBrowserExtensionAsync`` API using the
  directory this adapter prepares (the exact mechanism proven in the V3
  Phase 4 lab).
- Ranking, validation, context-faithful handoff, and downloading remain
  entirely with the existing VRKA pipeline.  Observations enter it through
  ``CandidateStore.observe`` supplied by callers.
"""

import hashlib
import io
import json
import os
import shutil
import zipfile
from pathlib import Path

# Pinned upstream provenance (see third_party/media_observer PROVENANCE file).
OBSERVER_PROJECT = "puemos/hls-downloader"
OBSERVER_DIRNAME = "puemos-hls-downloader"
OBSERVER_VERSION = "5.5.0"
OBSERVER_ARTIFACT_FILENAME = "extension-mv3-chrome-v5.5.0.zip"
OBSERVER_SHA256 = (
    "39dc660989c8a219fd0f85e203e2a268486d40a18f743b43dde8a71c1f680a52"
)
OBSERVER_COMMIT = "408b43f7c0f73ea7efd4153199f3935e38e657eb"
OBSERVER_LICENSE = "MIT"
HLS_CONTENT_TYPE = "application/vnd.apple.mpegurl"

_MAX_URL_LENGTH = 2048
_MAX_OBSERVATIONS = 64


def default_artifacts_root():
    """Locate the pinned artifact directory from this repository layout."""
    base = Path(__file__).resolve().parent.parent / "third_party" / "media_observer" / OBSERVER_DIRNAME
    return base


def artifact_zip_path(artifacts_root=None):
    root = Path(artifacts_root) if artifacts_root else default_artifacts_root()
    return root / OBSERVER_ARTIFACT_FILENAME


def sha256_of(path):
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def verify_artifact(zip_path):
    """True when the archive matches the pinned upstream digest."""
    try:
        return sha256_of(zip_path) == OBSERVER_SHA256
    except OSError:
        return False


def prepare_extension_dir(zip_path, runtime_dir):
    """Extract the archive to a stable, content-keyed, UNCHANGED unpacked
    directory suitable for WebView2's AddBrowserExtensionAsync (the folder
    must persist; changing its content removes the extension)."""
    try:
        zip_path = Path(zip_path)
        if not zip_path.is_file() or not verify_artifact(zip_path):
            return None
        with open(zip_path, "rb") as fh:
            key = hashlib.sha1(fh.read()).hexdigest()[:10]
        dest = Path(runtime_dir) / ("%s-%s-%s" % (OBSERVER_DIRNAME, OBSERVER_VERSION, key))
        marker = dest / "manifest.json"
        if marker.is_file():
            return str(dest)
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(str(dest))
        return str(dest) if marker.is_file() else None
    except Exception:
        return None


class MediaObserverAdapter:
    """Install/status/version/health/enumerate/subscribe facade."""

    def __init__(self, artifacts_root=None, runtime_dir=None):
        self._artifacts_root = Path(artifacts_root) if artifacts_root else default_artifacts_root()
        self._runtime_dir = Path(runtime_dir) if runtime_dir else None
        self._subscribers = []
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    def install(self):
        """Verify and unpack the pinned artifact.  Returns a status dict;
        profile registration itself is done by the caller via the official
        WebView2 API using the returned directory."""
        info = {
            "project": OBSERVER_PROJECT,
            "version": self.version(),
            "installed": False,
            "dir": "",
            "error": "",
        }
        zip_path = artifact_zip_path(self._artifacts_root)
        if not zip_path.is_file():
            info["error"] = "pinned observer artifact not found"
            return info
        if not verify_artifact(zip_path):
            info["error"] = "observer artifact sha256 mismatch"
            return info
        if self._runtime_dir is None:
            info["error"] = "observer runtime directory not configured"
            return info
        dest = prepare_extension_dir(zip_path, self._runtime_dir)
        if not dest:
            info["error"] = "observer extraction failed"
            return info
        info["installed"] = True
        info["dir"] = dest
        return info

    def uninstall(self):
        """Remove the prepared directory.  Idempotent; never raises."""
        removed = False
        if self._runtime_dir is not None:
            try:
                for child in Path(self._runtime_dir).glob("%s-%s-*" % (
                        OBSERVER_DIRNAME, OBSERVER_VERSION)):
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                        removed = True
            except OSError:
                pass
        return {"uninstalled": removed}

    def status(self):
        zip_path = artifact_zip_path(self._artifacts_root)
        return {
            "project": OBSERVER_PROJECT,
            "version": self.version(),
            "license": OBSERVER_LICENSE,
            "commit": OBSERVER_COMMIT,
            "artifact_present": zip_path.is_file(),
            "artifact_verified": verify_artifact(zip_path),
            "dir_present": bool(self.prepared_dirs()),
            "closed": self._closed,
        }

    def version(self):
        return OBSERVER_VERSION

    def prepared_dirs(self):
        if self._runtime_dir is None:
            return []
        return [str(p) for p in Path(self._runtime_dir).glob(
            "%s-%s-*" % (OBSERVER_DIRNAME, OBSERVER_VERSION))]

    def health(self):
        zip_path = artifact_zip_path(self._artifacts_root)
        checks = {
            "artifact_present": zip_path.is_file(),
            "artifact_sha256": verify_artifact(zip_path),
            "manifest_valid": False,
            "version_matches": False,
            "mv3_service_worker": False,
        }
        dirs = self.prepared_dirs()
        if dirs:
            try:
                manifest = json.loads((Path(dirs[0]) / "manifest.json").read_text(encoding="utf-8"))
                checks["manifest_valid"] = isinstance(manifest, dict)
                checks["version_matches"] = str(manifest.get("version", "")) == OBSERVER_VERSION
                worker = ((manifest.get("background") or {}).get("service_worker") or "")
                checks["mv3_service_worker"] = bool(worker) and manifest.get("manifest_version") == 3
            except (OSError, ValueError):
                pass
        checks["subscribers_active"] = len(self._subscribers)
        checks["not_closed"] = not self._closed
        return {"ok": all(v is True for k, v in checks.items()
                          if k not in ("subscribers_active",)), "checks": checks}

    # -- observation pipeline ----------------------------------------------

    def enumerate_observations(self, state):
        """Normalize a raw observer storage snapshot ({'local':
        {'state': {...}}}, the shape the capture path produces) into
        observation dicts shaped for ``CandidateStore.observe``.
        Malformed entries are rejected; duplicates collapse; output is
        bounded."""
        if not isinstance(state, dict):
            return []
        local = state.get("local")
        st = local.get("state") if isinstance(local, dict) else None
        playlists = ((st or {}).get("playlists") or {}).get("playlists")
        if not isinstance(playlists, dict):
            return []
        seen_urls = set()
        out = []
        for pid, entry in playlists.items():
            normalized = self._normalize_entry(pid, entry)
            if normalized is None or normalized["url"] in seen_urls:
                continue
            seen_urls.add(normalized["url"])
            out.append(normalized)
            if len(out) >= _MAX_OBSERVATIONS:
                break
        return out

    def _normalize_entry(self, pid, entry):
        if not isinstance(entry, dict):
            return None
        url = entry.get("url") or entry.get("uri") or pid
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return None
        if len(url) > _MAX_URL_LENGTH or ".m3u8" not in url.lower():
            return None
        initiator = entry.get("initiator")
        title = entry.get("pageTitle")
        created = entry.get("createdAt")
        timestamp = None
        if isinstance(created, (int, float)) and created > 0:
            timestamp = created / 1000.0
        return {
            "url": url,
            "content_type": HLS_CONTENT_TYPE,
            "timestamp": timestamp,
            "user_started": True,
            "source": "media_observer",
            "observer_version": OBSERVER_VERSION,
            "page_url": initiator if isinstance(initiator, str) else "",
            "title": title if isinstance(title, str) else "",
            "is_master": "master" in url.lower(),
        }

    def subscribe_to_observations(self, callback):
        """Register a callback receiving the normalized observations list on
        emit().  Bounded; returns True when registered."""
        if self._closed or not callable(callback) or len(self._subscribers) >= 8:
            return False
        self._subscribers.append(callback)
        return True

    def emit(self, observations):
        """Fan normalized observations out to subscribers.  Safe after close."""
        if self._closed or not isinstance(observations, list):
            return 0
        delivered = 0
        for cb in tuple(self._subscribers):
            try:
                cb(observations)
                delivered += 1
            except Exception:
                continue
        return delivered

    def close(self):
        """Idempotent shutdown."""
        self._closed = True
        self._subscribers.clear()


# -- updater (approved design: official source, HTTPS, checksum, atomic,
#    rollback-by-validation; no mirrors; never touches VRKA itself) ------

UPSTREAM_RELEASE_API = "https://api.github.com/repos/puemos/hls-downloader/releases/latest"
OBSERVER_ASSET_NAME = "extension-mv3-chrome.zip"
_USER_AGENT = "VRKA-media-observer-updater"


def _version_tuple(value):
    parts = []
    for piece in str(value).lstrip("v").split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def _default_fetch_json(url):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _default_fetch_bytes(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def check_for_update(fetch_json=None):
    """Look up the official upstream latest release.  Never raises; an
    unreachable network is a soft error, never a state change."""
    fetch_json = fetch_json or _default_fetch_json
    try:
        release = fetch_json(UPSTREAM_RELEASE_API)
    except Exception as exc:
        return {"error": "release lookup failed: %s" % exc}
    tag = str(release.get("tag_name") or "")
    asset = next((a for a in (release.get("assets") or [])
                  if a.get("name") == OBSERVER_ASSET_NAME), None)
    if not asset:
        return {"error": "official Chromium MV3 asset missing from latest release"}
    digest = str(asset.get("digest") or "").split(":")[-1].lower()
    available = tag.lstrip("v")
    return {
        "current_version": OBSERVER_VERSION,
        "available_version": available,
        "update_available": _version_tuple(available) > _version_tuple(OBSERVER_VERSION),
        "asset_url": asset.get("browser_download_url") or "",
        "sha256": digest,
        "source": UPSTREAM_RELEASE_API,
    }


def apply_update(artifacts_root=None, fetch_json=None, fetch_bytes=None):
    """Download the official artifact, verify checksum + manifest structure,
    then atomically replace the pinned archive.  Any failure leaves the
    previous artifact and its prepared directory untouched and usable."""
    info = check_for_update(fetch_json)
    if info.get("error"):
        return dict(info, updated=False)
    if not info["update_available"]:
        return dict(info, updated=False, message="already current")
    url, digest = info.get("asset_url"), info.get("sha256")
    if not url or not digest:
        return {"updated": False, "error": "release metadata incomplete"}
    fetch_bytes = fetch_bytes or _default_fetch_bytes
    try:
        blob = fetch_bytes(url)
    except Exception as exc:
        return {"updated": False, "error": "download failed: %s" % exc}
    actual = hashlib.sha256(blob).hexdigest()
    if actual != digest:
        return {"updated": False, "error": "checksum mismatch",
                "expected_sha256": digest, "actual_sha256": actual}
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            new_version = str(manifest.get("version") or "")
            worker = ((manifest.get("background") or {}).get("service_worker") or "")
            if manifest.get("manifest_version") != 3 or not worker:
                return {"updated": False, "error": "invalid extension manifest"}
            if _version_tuple(new_version) != _version_tuple(info["available_version"]):
                return {"updated": False, "error": "manifest version does not match release"}
    except Exception as exc:
        return {"updated": False, "error": "archive validation failed: %s" % exc}
    target = artifact_zip_path(artifacts_root)
    tmp = target.with_name(target.name + ".updating")
    tmp.write_bytes(blob)
    os.replace(tmp, target)
    return {"updated": True, "previous_version": OBSERVER_VERSION,
            "installed_version": new_version, "artifact": str(target),
            "sha256": actual}
