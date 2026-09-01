"""UI-neutral candidate and same-job fallback domain for VRKA build010.

This module has no WebView, GUI, filesystem, or subprocess dependency so
the security-sensitive ranking and state rules remain deterministic and easy
to exercise with hostile synthetic fixtures.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import time
import urllib.parse
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping


class CandidateKind(str, Enum):
    DIRECT = "direct"
    HLS = "hls"
    DASH = "dash"
    OTHER = "other"


class CandidateLifecycle(str, Enum):
    OBSERVED = "observed"
    PLAYABLE = "playable"
    PLAYING = "playing"
    STABILIZING = "stabilizing"
    STABLE = "stable"
    REPLACED = "replaced"
    EXPIRED = "expired"
    REJECTED = "rejected"
    SELECTED = "selected"
    HANDED_OFF = "handed_off"
    FAILED_HANDOFF = "failed_handoff"


class DownloadState(str, Enum):
    QUEUED = "queued"
    DIRECT_ATTEMPT = "direct_attempt"
    DIRECT_FAILED_ELIGIBLE_FOR_FALLBACK = "direct_failed_eligible_for_fallback"
    BROWSER_STARTING = "browser_starting"
    BROWSER_WAITING_FOR_MEDIA = "browser_waiting_for_media"
    BROWSER_INTERACTION_REQUIRED = "browser_interaction_required"
    BROWSER_STABILIZING_CANDIDATES = "browser_stabilizing_candidates"
    CANDIDATE_SELECTION_REQUIRED = "candidate_selection_required"
    HANDOFF_PREPARING = "handoff_preparing"
    HANDOFF_VALIDATING = "handoff_validating"
    BROWSER_CONTEXT_TRANSFER = "browser_context_transfer"
    DOWNLOADER_RESUMED = "downloader_resumed"
    DOWNLOAD_RUNNING = "download_running"
    POST_PROCESSING = "post_processing"
    FALLBACK_RECOVERING = "fallback_recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {
    DownloadState.COMPLETED,
    DownloadState.FAILED,
    DownloadState.CANCELLED,
}


_TRANSITIONS: dict[DownloadState, frozenset[DownloadState]] = {
    DownloadState.QUEUED: frozenset({DownloadState.DIRECT_ATTEMPT, DownloadState.CANCELLED}),
    DownloadState.DIRECT_ATTEMPT: frozenset({
        DownloadState.DIRECT_FAILED_ELIGIBLE_FOR_FALLBACK,
        DownloadState.DOWNLOAD_RUNNING,
        DownloadState.COMPLETED,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }),
    DownloadState.DIRECT_FAILED_ELIGIBLE_FOR_FALLBACK: frozenset({
        DownloadState.BROWSER_STARTING,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }),
    DownloadState.BROWSER_STARTING: frozenset({
        DownloadState.BROWSER_WAITING_FOR_MEDIA,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }),
    DownloadState.BROWSER_WAITING_FOR_MEDIA: frozenset({
        DownloadState.BROWSER_INTERACTION_REQUIRED,
        DownloadState.BROWSER_STABILIZING_CANDIDATES,
        DownloadState.HANDOFF_PREPARING,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.CANCELLED,
    }),
    DownloadState.BROWSER_INTERACTION_REQUIRED: frozenset({
        DownloadState.BROWSER_WAITING_FOR_MEDIA,
        DownloadState.BROWSER_STABILIZING_CANDIDATES,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.CANCELLED,
    }),
    DownloadState.BROWSER_STABILIZING_CANDIDATES: frozenset({
        DownloadState.BROWSER_WAITING_FOR_MEDIA,
        DownloadState.CANDIDATE_SELECTION_REQUIRED,
        DownloadState.HANDOFF_PREPARING,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.CANCELLED,
    }),
    DownloadState.CANDIDATE_SELECTION_REQUIRED: frozenset({
        DownloadState.HANDOFF_PREPARING,
        DownloadState.BROWSER_WAITING_FOR_MEDIA,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.CANCELLED,
    }),
    DownloadState.HANDOFF_PREPARING: frozenset({
        DownloadState.HANDOFF_VALIDATING,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.CANCELLED,
    }),
    DownloadState.HANDOFF_VALIDATING: frozenset({
        DownloadState.DOWNLOADER_RESUMED,
        DownloadState.BROWSER_CONTEXT_TRANSFER,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.CANCELLED,
    }),
    DownloadState.BROWSER_CONTEXT_TRANSFER: frozenset({
        DownloadState.DOWNLOADER_RESUMED,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }),
    DownloadState.DOWNLOADER_RESUMED: frozenset({
        DownloadState.DOWNLOAD_RUNNING,
        DownloadState.POST_PROCESSING,
        DownloadState.COMPLETED,
        DownloadState.FALLBACK_RECOVERING,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }),
    DownloadState.DOWNLOAD_RUNNING: frozenset({
        DownloadState.POST_PROCESSING,
        DownloadState.COMPLETED,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }),
    DownloadState.POST_PROCESSING: frozenset({
        DownloadState.COMPLETED,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }),
    DownloadState.FALLBACK_RECOVERING: frozenset({
        DownloadState.BROWSER_STARTING,
        DownloadState.BROWSER_WAITING_FOR_MEDIA,
        DownloadState.BROWSER_STABILIZING_CANDIDATES,
        DownloadState.CANDIDATE_SELECTION_REQUIRED,
        DownloadState.HANDOFF_PREPARING,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }),
    DownloadState.COMPLETED: frozenset(),
    DownloadState.FAILED: frozenset(),
    DownloadState.CANCELLED: frozenset(),
}

# Cancellation and ordinary failure can happen in every live phase. Keeping these
# terminal edges uniform prevents executor exceptions from stranding the FIFO worker.
_TRANSITIONS = {
    state: (
        targets | frozenset({DownloadState.FAILED, DownloadState.CANCELLED})
        if state not in TERMINAL_STATES else targets
    )
    for state, targets in _TRANSITIONS.items()
}


@dataclass
class DownloadStateMachine:
    """Explicit transition guard attached to exactly one logical task ID."""

    task_id: str
    state: DownloadState = DownloadState.QUEUED
    sequence: int = 0
    attempts: int = 0

    def transition(self, target: DownloadState) -> int:
        if target == self.state:
            return self.sequence
        if target not in _TRANSITIONS[self.state]:
            raise ValueError(f"Invalid fallback transition: {self.state.value} -> {target.value}")
        self.state = target
        self.sequence += 1
        if target in (DownloadState.DIRECT_ATTEMPT, DownloadState.HANDOFF_VALIDATING):
            self.attempts += 1
        return self.sequence

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


VOLATILE_QUERY_NAMES = frozenset({
    "auth", "authorization", "expires", "exp", "hdnts", "hmac", "jwt",
    "key", "policy", "signature", "sig", "token", "x-amz-credential",
    "x-amz-date", "x-amz-expires", "x-amz-security-token", "x-amz-signature",
})
SEGMENT_SUFFIXES = (".ts", ".m4s", ".m4a", ".cmfv", ".cmfa", ".aac")
_SEGMENT_PATH_RE = re.compile(r"(?:^|[/_.-])(?:seg(?:ment)?|chunk|frag(?:ment)?|part)[-_]?\d+", re.I)
# Sequence-numbered codec segments served with a media extension (many CDNs
# expose HLS segments as ``name_<codec>_<n>_<token>.mp4`` URLs). These are
# children of a manifest, never standalone transfer candidates.
_SEGMENT_CODEC_RE = re.compile(
    r"(?:^|[_.-])(?:h264|h265|hevc|avc1?|aac|mp4a|mpeg4|vp9|opus|seg(?:ment)?|chunk|frag(?:ment)?|part|piece|slice)[_.-]?\d{1,6}(?:[_.-]|$)",
    re.I,
)
# HLS initialization fragments (``name_<codec>_init_<token>.mp4``) are also
# segments, never standalone transfer candidates.
_SEGMENT_INIT_RE = re.compile(
    r"(?:^|[_.-])(?:h264|h265|hevc|avc1?|aac|mp4a|mpeg4|vp9|opus)[_.-]init[_.-]",
    re.I,
)
# Sequence-numbered media served without a codec marker (``name_<n>_<token>_<epoch>.mp4``
# from HLS/CDN segment pipelines).  The digit run must be delimited by
# separators on BOTH sides AND followed by another ``_``-separated field:
# resolution suffixes (``1080p``), year/version names (``movie_2024_1080p``,
# ``party_2015_trailer``) and leading numeric stream ids (``123456_240p.m3u8``)
# do not match.
_SEGMENT_SEQUENCE_RE = re.compile(r"[_.-]\d{1,8}_[A-Za-z0-9]+_[A-Za-z0-9]", re.I)


def _normalized_host(host: str | None) -> str:
    value = (host or "").strip().lower().rstrip(".")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return value.encode("idna").decode("ascii") if value else ""


def media_kind(url: str, content_type: str = "") -> CandidateKind:
    path = urllib.parse.urlsplit(url).path.lower()
    mime = content_type.lower().split(";", 1)[0].strip()
    if path.endswith(".m3u8") or mime in {
        "application/vnd.apple.mpegurl", "application/x-mpegurl",
    }:
        return CandidateKind.HLS
    if path.endswith(".mpd") or mime == "application/dash+xml":
        return CandidateKind.DASH
    if mime.startswith(("video/", "audio/")) or path.endswith((
        ".mp4", ".webm", ".mov", ".mkv", ".mp3", ".wav", ".flac", ".m4a",
    )):
        return CandidateKind.DIRECT
    return CandidateKind.OTHER


def is_segment(url: str, content_type: str = "") -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    # The sequence-number regex is extension-sensitive (``name_2.mp4`` is a
    # normal file, ``name_2_<token>_<epoch>.mp4`` is a segment), so it is
    # evaluated against the stem without the media extension.
    filename = path.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return (path.endswith(SEGMENT_SUFFIXES)
            or bool(_SEGMENT_PATH_RE.search(path))
            or bool(_SEGMENT_CODEC_RE.search(path))
            or bool(_SEGMENT_INIT_RE.search(path))
            or bool(_SEGMENT_SEQUENCE_RE.search(stem)))


_GENERIC_MASTER_STEMS = frozenset({"master", "playlist", "manifest", "index"})


def is_master_manifest(url: str) -> bool:
    """True when a manifest path is a generic master/rendition-selector name
    (``master.m3u8``, ``playlist.m3u8``, ``manifest.mpd``, plain ``index.m3u8``)
    rather than a specific rendition playlist (``index-f1-v1-a1.m3u8``,
    ``225371326_240p.m3u8``).  Preferring the master lets the normal
    downloader choose the best available quality instead of a fixed rendition."""
    path = urllib.parse.urlsplit(str(url or "")).path
    filename = path.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0].lower() if "." in filename else filename.lower()
    return stem in _GENERIC_MASTER_STEMS


def canonical_media_identity(url: str, kind: CandidateKind | None = None) -> str:
    """Build a stable logical identity without changing the transfer URL.

    Only well-known ephemeral authentication fields are removed.  All other
    query fields remain because they can distinguish genuinely different media.
    """

    parsed = urllib.parse.urlsplit(url)
    chosen_kind = kind or media_kind(url)
    host = _normalized_host(parsed.hostname)
    port = f":{parsed.port}" if parsed.port else ""
    path = re.sub(r"/{2,}", "/", urllib.parse.unquote(parsed.path or "/"))
    stable_query = [
        (name.lower(), value)
        for name, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if name.lower() not in VOLATILE_QUERY_NAMES
    ]
    stable_query.sort()
    normalized = urllib.parse.urlunsplit((
        parsed.scheme.lower(), host + port, path, urllib.parse.urlencode(stable_query), "",
    ))
    digest = hashlib.sha256(f"{chosen_kind.value}|{normalized}".encode("utf-8")).hexdigest()
    return digest[:24]


@dataclass
class CandidateEvidence:
    timestamp: float
    event: str
    safe_detail: str = ""


@dataclass
class MediaCandidate:
    candidate_id: str
    canonical_identity: str
    current_url: str
    kind: CandidateKind
    content_type: str = ""
    response_content_type: str = ""
    player_id: str = ""
    frame_id: str = ""
    primary_player: bool = False
    nested_frame: bool = False
    popup_context: bool = False
    first_seen: float = 0.0
    last_seen: float = 0.0
    request_count: int = 0
    segment_count: int = 0
    bytes_observed: int = 0
    playing: bool = False
    playback_started_at: float | None = None
    sustained_playback_seconds: float = 0.0
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    bitrate: int | None = None
    user_started: bool = False
    user_selected: bool = False
    source_lineage: list[str] = field(default_factory=list)
    replaced_by: str = ""
    repeated_loops: int = 0
    expires_at: float | None = None
    lifecycle: CandidateLifecycle = CandidateLifecycle.OBSERVED
    nuisance_score: int = 0
    required_header_names: frozenset[str] = field(default_factory=frozenset)
    evidence: list[CandidateEvidence] = field(default_factory=list)
    rank_score: float = 0.0
    confidence_explanation: tuple[str, ...] = field(default_factory=tuple)

    def safe_dict(self) -> dict[str, Any]:
        """Return metadata safe for presentation, diagnostics, and History."""

        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind.value,
            "content_type": self.content_type or self.response_content_type,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "playing": self.playing,
            "stable": self.lifecycle == CandidateLifecycle.STABLE,
            "lifecycle": self.lifecycle.value,
            "score": round(self.rank_score, 2),
            "confidence": list(self.confidence_explanation),
            "host": _normalized_host(urllib.parse.urlsplit(self.current_url).hostname),
        }


class CandidateStore:
    """Bounded semantic candidate store with player source lineage."""

    def __init__(self, max_candidates: int = 48, max_evidence_per_candidate: int = 16,
                 max_age_seconds: float = 1800.0):
        if not 4 <= max_candidates <= 256:
            raise ValueError("Candidate bound must be between 4 and 256")
        self.max_candidates = max_candidates
        self.max_evidence = max_evidence_per_candidate
        self.max_age_seconds = max_age_seconds
        self._items: dict[str, MediaCandidate] = {}
        self._player_current: dict[str, str] = {}

    def values(self) -> tuple[MediaCandidate, ...]:
        return tuple(self._items.values())

    def get(self, candidate_id: str) -> MediaCandidate | None:
        return self._items.get(candidate_id)

    def observe(self, *, url: str, content_type: str = "", timestamp: float | None = None,
                player_id: str = "", frame_id: str = "", primary_player: bool = False,
                nested_frame: bool = False, popup_context: bool = False,
                segment_parent_url: str = "", content_length: int | None = None,
                playing: bool | None = None, duration_seconds: float | None = None,
                width: int | None = None, height: int | None = None,
                user_started: bool = False, nuisance_score: int = 0,
                required_header_names: Iterable[str] = ()) -> MediaCandidate | None:
        now = float(timestamp if timestamp is not None else time.monotonic())
        if not str(url).startswith(("http://", "https://")):
            return None
        if is_segment(url, content_type):
            return self._observe_segment(segment_parent_url, player_id, now, content_length)
        kind = media_kind(url, content_type)
        if kind == CandidateKind.OTHER:
            return None
        identity = canonical_media_identity(url, kind)
        candidate = next(
            (item for item in self._items.values() if item.canonical_identity == identity),
            None,
        )
        created = candidate is None
        if candidate is None:
            candidate_id = "mc_" + hashlib.blake2s(
                f"{identity}|{now}|{len(self._items)}".encode("utf-8"), digest_size=8,
            ).hexdigest()
            candidate = MediaCandidate(
                candidate_id=candidate_id,
                canonical_identity=identity,
                current_url=url,
                kind=kind,
                first_seen=now,
                last_seen=now,
            )
            self._items[candidate_id] = candidate
        candidate.current_url = url  # Refresh an expired/signed transfer URL in place.
        candidate.content_type = content_type or candidate.content_type
        candidate.last_seen = now
        candidate.request_count += 1
        candidate.player_id = player_id or candidate.player_id
        candidate.frame_id = frame_id or candidate.frame_id
        candidate.primary_player = primary_player or candidate.primary_player
        candidate.nested_frame = nested_frame
        candidate.popup_context = popup_context
        candidate.duration_seconds = duration_seconds if duration_seconds is not None else candidate.duration_seconds
        candidate.width = width if width is not None else candidate.width
        candidate.height = height if height is not None else candidate.height
        candidate.user_started = user_started or candidate.user_started
        candidate.nuisance_score = max(candidate.nuisance_score, nuisance_score)
        candidate.required_header_names = frozenset(
            set(candidate.required_header_names) | {str(name).lower() for name in required_header_names}
        )
        if content_length and content_length > 0:
            candidate.bytes_observed = max(candidate.bytes_observed, int(content_length))
        if playing is not None:
            if playing and not candidate.playing:
                candidate.playback_started_at = now
            if not playing and candidate.playing and candidate.playback_started_at is not None:
                candidate.sustained_playback_seconds += max(0.0, now - candidate.playback_started_at)
                candidate.playback_started_at = None
            candidate.playing = playing
            candidate.lifecycle = CandidateLifecycle.PLAYING if playing else CandidateLifecycle.PLAYABLE
        elif created:
            candidate.lifecycle = CandidateLifecycle.PLAYABLE
        self._evidence(candidate, "observed", kind.value)
        if player_id:
            previous_id = self._player_current.get(player_id)
            if previous_id and previous_id != candidate.candidate_id:
                previous = self._items.get(previous_id)
                if previous and not previous.user_selected:
                    previous.replaced_by = candidate.candidate_id
                    previous.lifecycle = CandidateLifecycle.REPLACED
                    candidate.source_lineage = (previous.source_lineage + [previous.candidate_id])[-8:]
                    self._evidence(previous, "replaced", candidate.candidate_id)
                    self._evidence(candidate, "source_replacement", previous.candidate_id)
            self._player_current[player_id] = candidate.candidate_id
        self.prune(now)
        return candidate

    def _observe_segment(self, manifest_url: str, player_id: str, now: float,
                         content_length: int | None) -> MediaCandidate | None:
        candidate = None
        if manifest_url:
            identity = canonical_media_identity(manifest_url, media_kind(manifest_url))
            candidate = next(
                (item for item in self._items.values() if item.canonical_identity == identity), None,
            )
        if candidate is None and player_id:
            candidate = self._items.get(self._player_current.get(player_id, ""))
        if candidate and candidate.kind in (CandidateKind.HLS, CandidateKind.DASH):
            candidate.segment_count += 1
            candidate.request_count += 1
            candidate.last_seen = now
            if content_length and content_length > 0:
                candidate.bytes_observed += int(content_length)
            self._evidence(candidate, "segment", "")
        return candidate

    def _evidence(self, candidate: MediaCandidate, event: str, detail: str) -> None:
        candidate.evidence.append(CandidateEvidence(candidate.last_seen, event, detail[:80]))
        if len(candidate.evidence) > self.max_evidence:
            del candidate.evidence[:-self.max_evidence]

    def select(self, candidate_id: str) -> MediaCandidate:
        candidate = self._items[candidate_id]
        candidate.user_selected = True
        candidate.lifecycle = CandidateLifecycle.SELECTED
        self._evidence(candidate, "user_selected", "")
        return candidate

    def mark_handoff(self, candidate_id: str, success: bool) -> None:
        candidate = self._items[candidate_id]
        candidate.lifecycle = (
            CandidateLifecycle.HANDED_OFF if success else CandidateLifecycle.FAILED_HANDOFF
        )
        self._evidence(candidate, "handoff_committed" if success else "handoff_failed", "")

    def prune(self, now: float | None = None) -> None:
        current = float(now if now is not None else time.monotonic())
        stale = [
            item for item in self._items.values()
            if current - item.last_seen > self.max_age_seconds
            and item.lifecycle not in (CandidateLifecycle.SELECTED, CandidateLifecycle.HANDED_OFF)
        ]
        for item in stale:
            self._items.pop(item.candidate_id, None)
        if len(self._items) <= self.max_candidates:
            return
        retention = sorted(
            self._items.values(),
            key=lambda item: (
                item.user_selected,
                item.lifecycle not in (CandidateLifecycle.REPLACED, CandidateLifecycle.EXPIRED,
                                       CandidateLifecycle.REJECTED),
                item.last_seen,
            ),
            reverse=True,
        )[:self.max_candidates]
        kept = {item.candidate_id for item in retention}
        self._items = {key: value for key, value in self._items.items() if key in kept}


@dataclass(frozen=True)
class RankingConfig:
    user_selected: float = 1000.0
    user_started: float = 70.0
    primary_player: float = 48.0
    playing: float = 36.0
    sustained_per_second: float = 2.0
    sustained_cap: float = 55.0
    coherent_manifest: float = 34.0
    manifest_kind: float = 14.0
    master_manifest: float = 40.0
    direct_kind: float = 10.0
    stable_survival: float = 22.0
    source_replacement: float = 30.0
    useful_dimensions: float = 12.0
    complete_context: float = 8.0
    nested_frame: float = -12.0
    popup_context: float = -85.0
    replaced: float = -145.0
    repeated_loop: float = -22.0
    nuisance: float = -5.0
    expired: float = -1000.0
    failed_handoff: float = -240.0
    min_candidate_score: float = 20.0
    unambiguous_margin: float = 28.0
    quick_stability_seconds: float = 2.5
    ambiguous_stability_seconds: float = 5.0
    maximum_stability_seconds: float = 12.0


@dataclass(frozen=True)
class RankedCandidate:
    candidate_id: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RankingDecision:
    selected_candidate_id: str | None
    ambiguous_candidate_ids: tuple[str, ...]
    ranked: tuple[RankedCandidate, ...]
    wait_seconds: float
    explanation: str


class CandidateRanker:
    def __init__(self, config: RankingConfig | None = None):
        self.config = config or RankingConfig()

    def score(self, candidate: MediaCandidate, now: float) -> RankedCandidate:
        cfg = self.config
        score = 0.0
        reasons: list[str] = []

        def add(value: float, explanation: str) -> None:
            nonlocal score
            if value:
                score += value
                reasons.append(("+" if value > 0 else "") + f"{value:g} {explanation}")

        add(cfg.user_selected if candidate.user_selected else 0, "explicit user selection")
        add(cfg.user_started if candidate.user_started else 0, "playback followed user interaction")
        add(cfg.primary_player if candidate.primary_player else 0, "linked to primary player")
        add(cfg.playing if candidate.playing else 0, "currently playing")
        sustained = candidate.sustained_playback_seconds
        if candidate.playing and candidate.playback_started_at is not None:
            sustained += max(0.0, now - candidate.playback_started_at)
        add(min(sustained * cfg.sustained_per_second, cfg.sustained_cap), "sustained playback")
        if candidate.kind in (CandidateKind.HLS, CandidateKind.DASH):
            add(cfg.manifest_kind, f"{candidate.kind.value.upper()} manifest")
            if is_master_manifest(candidate.current_url):
                add(cfg.master_manifest, "master manifest (best-quality selection)")
            if candidate.segment_count >= 2:
                add(cfg.coherent_manifest, "coherent segment activity")
        elif candidate.kind == CandidateKind.DIRECT:
            add(cfg.direct_kind, "direct media response")
        survival = max(0.0, now - candidate.first_seen)
        if survival >= cfg.quick_stability_seconds and candidate.request_count >= 2:
            add(cfg.stable_survival, "survived stabilization window")
        if candidate.source_lineage:
            add(cfg.source_replacement, "replaced an earlier source in the same player")
        if candidate.width and candidate.height and candidate.width >= 320 and candidate.height >= 180:
            add(cfg.useful_dimensions, "usable video dimensions")
        if candidate.required_header_names:
            add(cfg.complete_context, "required request context observed")
        add(cfg.nested_frame if candidate.nested_frame else 0, "nested frame context")
        add(cfg.popup_context if candidate.popup_context else 0, "popup context")
        add(cfg.replaced if candidate.lifecycle == CandidateLifecycle.REPLACED else 0,
            "rapidly replaced source")
        add(cfg.repeated_loop * candidate.repeated_loops, "repeated looping")
        add(cfg.nuisance * candidate.nuisance_score, "nuisance-adjacent request")
        add(cfg.expired if candidate.lifecycle == CandidateLifecycle.EXPIRED else 0, "expired URL")
        add(cfg.failed_handoff if candidate.lifecycle == CandidateLifecycle.FAILED_HANDOFF else 0,
            "previous validation failure")
        return RankedCandidate(candidate.candidate_id, score, tuple(reasons[:10]))

    def decide(self, candidates: Iterable[MediaCandidate], now: float | None = None) -> RankingDecision:
        current = float(now if now is not None else time.monotonic())
        items = list(candidates)
        ranked = sorted(
            (self.score(item, current) for item in items),
            key=lambda item: (-item.score, item.candidate_id),
        )
        by_id = {item.candidate_id: item for item in items}
        for entry in ranked:
            candidate = by_id[entry.candidate_id]
            candidate.rank_score = entry.score
            candidate.confidence_explanation = entry.reasons
        if not ranked or ranked[0].score < self.config.min_candidate_score:
            return RankingDecision(None, tuple(), tuple(ranked), self.config.quick_stability_seconds,
                                   "Waiting for stronger playback evidence.")
        top = ranked[0]
        top_candidate = by_id[top.candidate_id]
        if top_candidate.user_selected:
            return RankingDecision(top.candidate_id, tuple(), tuple(ranked), 0.0,
                                   "Selected explicitly by the user.")
        margin = top.score - ranked[1].score if len(ranked) > 1 else top.score
        age = max(0.0, current - top_candidate.first_seen)
        coherent = (
            top_candidate.kind == CandidateKind.DIRECT and top_candidate.request_count >= 2
        ) or (
            top_candidate.kind in (CandidateKind.HLS, CandidateKind.DASH)
            and top_candidate.segment_count >= 2
        )
        if margin >= self.config.unambiguous_margin and coherent:
            remaining = max(0.0, self.config.quick_stability_seconds - age)
            return RankingDecision(
                top.candidate_id if remaining == 0 else None,
                tuple(), tuple(ranked), remaining,
                "The leading media has coherent activity and a clear confidence margin.",
            )
        plausible = tuple(
            item.candidate_id for item in ranked
            if item.score >= self.config.min_candidate_score
            and top.score - item.score < self.config.unambiguous_margin
        )
        wait_target = min(self.config.ambiguous_stability_seconds, self.config.maximum_stability_seconds)
        remaining = max(0.0, wait_target - age)
        if remaining > 0:
            return RankingDecision(None, plausible, tuple(ranked), remaining,
                                   "Several media candidates are still stabilizing.")
        if len(plausible) > 1:
            return RankingDecision(None, plausible, tuple(ranked), 0.0,
                                   "Multiple plausible videos remain; user selection is required.")
        return RankingDecision(top.candidate_id, tuple(), tuple(ranked), 0.0,
                               "The best available candidate remained stable through the bounded window.")


@dataclass(frozen=True)
class HandoffBundle:
    """Immutable, ephemeral downloader context.

    ``safe_summary`` is the only representation allowed outside the core.  Secret
    fields stay in the backend process and are never included in repr output.
    """

    task_id: str
    candidate_id: str
    media_url: str = field(repr=False)
    media_kind: CandidateKind
    user_agent: str = field(default="", repr=False)
    referer: str = field(default="", repr=False)
    origin: str = field(default="", repr=False)
    cookies: tuple[Mapping[str, Any], ...] = field(default_factory=tuple, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    expected_content_types: tuple[str, ...] = ()
    observed_status: int = 0
    observed_content_type: str = ""
    expected_duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "cookies", tuple(MappingProxyType(dict(row)) for row in self.cookies))

    def safe_summary(self) -> dict[str, Any]:
        parsed = urllib.parse.urlsplit(self.media_url)
        return {
            "task_id": self.task_id,
            "candidate_id": self.candidate_id,
            "media_kind": self.media_kind.value,
            "media_host": _normalized_host(parsed.hostname),
            "has_user_agent": bool(self.user_agent),
            "has_referer": bool(self.referer),
            "has_origin": bool(self.origin),
            "cookie_count": len(self.cookies),
            "header_names": sorted(str(name).lower() for name in self.headers),
        }
