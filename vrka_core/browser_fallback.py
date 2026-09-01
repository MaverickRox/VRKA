"""UI-neutral protected-browser capture, candidate ranking, and atomic handoff."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .candidates import (
    CandidateLifecycle,
    CandidateRanker,
    CandidateStore,
    DownloadState,
    HandoffBundle,
)
from .ownership import terminate_process_tree


MAX_CAPTURE_BYTES = 8 * 1024 * 1024

_NUMERIC_STREAM_ID_RE = re.compile(r"\b\d{5,}\b")
_WIDGET_RENDITION_SUFFIX_RE = re.compile(r"_(\d{3,4})p(?:\.m3u8)?$", re.IGNORECASE)


def looks_like_live_widget_url(url):
    """True when a candidate URL has the generic sidebar live-widget signature.

    Sidebar/live-cam HLS streams are addressed by a numeric stream id in the
    URL path and a rendition suffix such as ``_240p.m3u8`` (the requested
    episode's master/manifest on the same pages has neither).  This is
    evidence that a candidate belongs to an autoplay widget cluster, not to
    the requested media - independent of the DOM shape heuristics that can
    miss the cluster (e.g. when the cams render inside a nested frame).
    """
    parsed = urllib.parse.urlsplit(str(url or ""))
    if not _NUMERIC_STREAM_ID_RE.search(parsed.path):
        return False
    leaf = parsed.path.rsplit("/", 1)[-1]
    return bool(_WIDGET_RENDITION_SUFFIX_RE.search(leaf))


class BrowserFallbackError(RuntimeError):
    pass


class BrowserFallbackCancelled(BrowserFallbackError):
    pass


class ExternalReplayRejected(BrowserFallbackError):
    """A browser-credited candidate was refused by the media server's edge
    during the external transfer replay itself (context-bound category).

    This is a TRANSFER limitation, not a candidate defect: the protected
    browser fetched the exact resource with HTTP 200, so the candidate is
    browser-accessible; the independent HTTP client simply cannot reproduce
    the browser-authenticated request (transport fingerprint / token
    binding).  Never treat it as validation failure or candidate decay."""
    pass


class BrowserContextCancelled(BrowserFallbackError):
    """The user closed the protected browser (or it exited) during a
    browser-context transfer.  A clean, user-initiated cancellation of the
    transfer - never a media, provider, or candidate failure.  Captured
    material has already been discarded with the episode; the task can be
    retried normally."""
    pass


class BrowserSelectionRequired(BrowserFallbackError):
    def __init__(self, candidates: tuple[Mapping[str, Any], ...], explanation: str):
        self.candidates = candidates
        self.explanation = explanation
        super().__init__("Multiple plausible browser media candidates require selection")


class BrowserEpisode(Protocol):
    def capture(self, cancel_event: threading.Event) -> Mapping[str, Any]: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...


class BrowserLauncher(Protocol):
    def __call__(self, record, context) -> BrowserEpisode: ...


@dataclass
class JsonFileBrowserEpisode:
    """One owned helper process writing a bounded JSON capture file."""

    process: Any
    result_path: Path
    cleanup_paths: tuple[Path, ...] = ()
    timeout_seconds: float = 30 * 60
    release_process: Callable[[], None] | None = None
    tree_terminator: Callable[[Any], None] = terminate_process_tree
    _closed: bool = False

    def capture(self, cancel_event: threading.Event,
                since_seq: int = 0) -> Mapping[str, Any]:
        """Wait for a live capture snapshot newer than ``since_seq``.

        The protected browser writes its capture on demand while staying open
        (a media-playable signal or an explicit capture command), so waiting
        for the file is not a window-close side effect. A payload written by a
        premature manual close is flagged ``manual_closed`` and refused.
        """
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            payload = self._read_capture()
            if payload is not None:
                self._register_capture_cleanup(payload)
                if int(payload.get("capture_seq") or 0) > since_seq:
                    return payload
            if payload is not None and self.process.poll() is not None:
                # The helper exited with a capture on disk; accept the newest
                # snapshot even if the sequence did not advance (legacy
                # payloads, or the helper was closed after writing).
                return payload
            if payload is None and self.process.poll() is not None:
                raise BrowserFallbackError("Protected browser closed before capture")
            if cancel_event.wait(0.1):
                raise BrowserFallbackCancelled("Browser fallback was cancelled")
            if time.monotonic() >= deadline:
                raise BrowserFallbackError("Protected browser timed out")

    def _register_capture_cleanup(self, payload: Mapping[str, Any]) -> None:
        """Track the session capture directory so its spilled media objects
        are deleted with the episode on every outcome (success of the
        external replay, failure, or cancellation) unless the browser-
        context transfer has already consumed them into the output."""
        capture = payload.get("media_capture") or {}
        objects_dir = str(capture.get("objects_dir") or "")
        if objects_dir and Path(objects_dir).is_dir():
            known = self.cleanup_paths
            if not any(str(path) == objects_dir for path in known):
                self.cleanup_paths = tuple(known) + (Path(objects_dir),)

    def _read_capture(self) -> Mapping[str, Any] | None:
        if not self.result_path.is_file():
            return None
        try:
            size = self.result_path.stat().st_size
        except OSError:
            return None
        if size > MAX_CAPTURE_BYTES:
            raise BrowserFallbackError("Protected browser returned an oversized capture")
        try:
            payload = json.loads(self.result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping) or not payload.get("ok"):
            raise BrowserFallbackError(
                str(payload.get("error") if isinstance(payload, Mapping)
                    else "Invalid browser capture")
            )
        if payload.get("manual_closed"):
            raise BrowserFallbackError("Protected browser closed before validated handoff")
        return payload

    def request_capture(self) -> None:
        """Ask the open browser for a fresh live snapshot without closing it."""
        self._send("capture")

    def request_media_capture(self) -> None:
        """Ask the open browser to ensure bounded media body capture is
        active.  Capture is session-wide (attached before the player's
        first fetch so init fragments are observable); this command is a
        no-op once attached."""
        self._send("mediacapture")

    def send_harness_command(self, command: str) -> None:
        """QA-harness-only stdin command (click/clickclass/clickvideo).

        Never used by the product path; exists so verification drivers can
        reproduce genuine user gestures (e.g. starting playback) the same
        way a human would."""
        self._send(command[:32])

    def commit(self) -> None:
        if self._closed:
            return
        self._send("commit")
        self._wait_or_terminate()
        self._cleanup()
        self._closed = True

    def close(self) -> None:
        if self._closed:
            return
        self._send("cancel")
        self._wait_or_terminate()
        self._cleanup()
        self._closed = True

    def _send(self, command: str) -> None:
        try:
            if self.process.poll() is None and self.process.stdin:
                self.process.stdin.write(command + "\n")
                self.process.stdin.flush()
        except (OSError, ValueError):
            pass

    def _wait_or_terminate(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            try:
                self.tree_terminator(self.process)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

    def _cleanup(self) -> None:
        try:
            self.result_path.unlink(missing_ok=True)
        except OSError:
            pass
        for path in self.cleanup_paths:
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        if self.release_process:
            self.release_process()
            self.release_process = None


class SubprocessBrowserLauncher:
    """Creates an owned helper process without depending on any GUI toolkit."""

    def __init__(self, session_dir: str | os.PathLike[str],
                 command_factory: Callable[[object, Path], Sequence[str]], *,
                 timeout_seconds: float = 30 * 60):
        self.session_dir = Path(session_dir)
        self.command_factory = command_factory
        self.timeout_seconds = timeout_seconds

    def __call__(self, record, context) -> JsonFileBrowserEpisode:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        result_path = self.session_dir / f"browser-{record.task_id}-{uuid.uuid4().hex}.json"
        profile_path = result_path.with_suffix(".profile")
        process = subprocess.Popen(
            list(self.command_factory(record, result_path)),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8",
        )
        release = context.own_process(process)
        return JsonFileBrowserEpisode(
            process, result_path, (profile_path,), self.timeout_seconds, release,
        )


class ProtectedBrowserFallback:
    """Captures, stabilizes, validates, and commits one browser handoff per task."""

    def __init__(self, launcher: BrowserLauncher,
                 resume_transfer: Callable[[HandoffBundle, object], bool], *,
                 select_candidate: Callable[[tuple[Mapping[str, Any], ...], str], str | None] | None = None,
                 browser_context_transfer: Callable[[Any, HandoffBundle, object], bool] | None = None,
                 max_handoff_attempts: int = 3,
                 max_capture_rounds: int = 3,
                 clock: Callable[[], float] = time.monotonic,
                 interaction_wait_seconds: float = 45.0):
        if not 1 <= max_handoff_attempts <= 8:
            raise ValueError("Handoff attempts must be between 1 and 8")
        if not 1 <= max_capture_rounds <= 8:
            raise ValueError("Capture rounds must be between 1 and 8")
        self.launcher = launcher
        self.resume_transfer = resume_transfer
        self.select_candidate = select_candidate
        self.browser_context_transfer = browser_context_transfer
        self.max_handoff_attempts = max_handoff_attempts
        self.max_capture_rounds = max_capture_rounds
        self.clock = clock
        self.interaction_wait_seconds = max(5.0, min(120.0, interaction_wait_seconds))

    def __call__(self, record, context) -> None:
        episode = self.launcher(record, context)
        context.on_cancel(episode.close)
        context.on_cleanup(episode.close)
        context.transition(DownloadState.BROWSER_WAITING_FOR_MEDIA,
                           message="Waiting for protected browser media")
        # Open the observation window unconditionally: the helper's playable
        # watcher cannot see cross-origin iframe players, so the first
        # snapshot must never depend on DOM-visible playback.  The bounded
        # interaction wait below owns all further re-capture pacing.
        episode.request_capture()
        since_seq = 0
        payload = episode.capture(context.cancel_event, since_seq=since_seq)
        since_seq = int(payload.get("capture_seq") or 0)
        store, records = self._candidates_from_payload(payload)

        # Bounded observation wait.  Two live-observed page shapes must never
        # end the observation early:
        #
        # 1. EMPTY FIRST SNAPSHOT - the snapshot can be taken before the page
        #    has rendered ANY media (sidebar live-cams on media pages appear
        #    15-30 s after load), so "no candidates yet" is a waiting state,
        #    not a terminal one.
        # 2. AUTOPLAY-WIDGET CLUSTER - the page autoplays a cluster of small
        #    widget videos (sidebar live-cams etc.) with no visible main
        #    player; the first-observed candidates belong to those widgets,
        #    NOT the requested media, whose stream only appears after the user
        #    selects a server and presses Play.
        #
        # Re-capture (bounded by the interaction deadline) while either shape
        # holds, so the requested media can enter the store before ranking.
        # The browser stays open and is never closed to trigger capture.
        interaction_deadline = self.clock() + self.interaction_wait_seconds
        wait_rounds = 0
        # Re-captures can complete in well under a second; the cap exists only
        # as an infinite-loop guard, so it must be generous enough that the
        # bounded interaction deadline (not the round count) ends the wait.
        max_wait_rounds = max(16, int(self.interaction_wait_seconds * 10) + 8)
        while (
            self.clock() < interaction_deadline
            and wait_rounds < max_wait_rounds
            and not payload.get("drm_detected")
            and (
                not store.values()
                or (
                    bool(payload.get("autoplay_widget_page"))
                    and not self._store_has_user_started_candidate(store)
                )
                or (
                    # Even when the DOM cluster heuristic missed the sidebar
                    # widgets (the cams can render inside a nested frame and
                    # the large-player rule flips off), the observed media is
                    # still only live-widget streams (numeric stream id +
                    # rendition suffix - the requested episode's manifest
                    # never matches): keep waiting for the user's interaction
                    # instead of committing a sidebar cam as the episode.
                    # The user_started flag is NOT used here: in the missed-
                    # cluster case the cams were never widget-marked, so they
                    # arrive with the default user_started=True.
                    self._store_only_widget_shaped(store)
                )
            )
        ):
            context.transition(DownloadState.BROWSER_WAITING_FOR_MEDIA,
                               message="Waiting for the requested media in the protected browser")
            # Pace re-captures: the helper executes one capture at a time over
            # a shared stdin pipe, so bursting requests faster than it can
            # drain them fills the pipe and stalls the fallback thread.
            time.sleep(max(0.0, min(1.5, self.interaction_wait_seconds / 60.0)))
            episode.request_capture()
            payload = episode.capture(context.cancel_event, since_seq=since_seq)
            since_seq = int(payload.get("capture_seq") or 0)
            store, records = self._candidates_from_payload(payload)
            wait_rounds += 1
        if payload.get("drm_detected"):
            raise BrowserFallbackError("The media is DRM-protected; VRKA will not bypass DRM")
        self._emit_protection_stats(context, payload)
        if not store.values():
            context.transition(DownloadState.BROWSER_INTERACTION_REQUIRED,
                               message="No playable media was observed")
            raise BrowserFallbackError("No playable media was observed in the protected browser")
        if self._store_only_widget_shaped(store):
            # Regression B: the bounded interaction window expired and the
            # protected browser still exposed ONLY sidebar/live-widget media
            # (numeric stream id + rendition suffix cams).  The requested
            # episode's manifest/master never appeared, so committing one of
            # these cams as the episode would be the wrong-media false
            # positive this guard exists to prevent.  Fail the handoff cleanly
            # with an actionable message instead - never substitute unrelated
            # widget media for the requested episode.
            context.transition(DownloadState.BROWSER_INTERACTION_REQUIRED,
                               message=("Only sidebar/live-widget media was observed; "
                                        "select the server and press Play in the "
                                        "protected browser to continue"))
            raise BrowserFallbackError(
                "The requested media did not appear in the protected browser: "
                "only sidebar/live-widget streams were observed. Select the "
                "server and press Play in the browser window, then start the "
                "task again."
            )

        context.transition(DownloadState.BROWSER_STABILIZING_CANDIDATES,
                           message="Stabilizing protected-browser candidates")
        failed_ids: set[str] = set()
        browser_fetch_credit_seen = False
        external_replay_rejection_seen = False
        browser_context_attempted = False
        for attempt in range(self.max_handoff_attempts):
            candidate = self._choose_candidate(store, payload, failed_ids)
            if candidate is None:
                raise self._terminal_handoff_error(
                    browser_fetch_credit_seen, external_replay_rejection_seen,
                    "No stable media candidate remains for handoff")
            candidate.lifecycle = CandidateLifecycle.SELECTED
            context.transition(DownloadState.HANDOFF_PREPARING,
                               message="Preparing secure media handoff")
            bundle = self._bundle(record.task_id, record.spec.url, candidate, payload, records)
            if int(getattr(bundle, "observed_status", 0) or 0) == 200:
                browser_fetch_credit_seen = True
            context.transition(DownloadState.HANDOFF_VALIDATING,
                               message="Validating downloader transfer start")
            try:
                started = bool(self.resume_transfer(bundle, context))
            except BrowserContextCancelled:
                # User-initiated: the protected browser is gone, so no
                # further candidate can be attempted.  Terminate the task
                # with the clean cancellation message.
                store.mark_handoff(candidate.candidate_id, False)
                raise
            except ExternalReplayRejected:
                started = False
                external_replay_rejection_seen = True
                # Generic browser-context transfer: the protected browser has
                # the media, independent replay is refused.  Attempted at
                # most ONCE per task, only here, never before rejection.
                if (self.browser_context_transfer is not None
                        and not browser_context_attempted
                        and hasattr(episode, "request_media_capture")):
                    browser_context_attempted = True
                    context.transition(
                        DownloadState.BROWSER_CONTEXT_TRANSFER,
                        message="Transferring media through the protected browser")
                    try:
                        if self.browser_context_transfer(episode, bundle, context):
                            store.mark_handoff(candidate.candidate_id, True)
                            try:
                                episode.commit()
                            except Exception:
                                pass
                            context.transition(
                                DownloadState.DOWNLOADER_RESUMED,
                                message="Browser-context transfer completed")
                            context.log(
                                "Browser-context transfer completed the task "
                                "with media captured from the protected browser.")
                            return
                    except Exception as transfer_exc:
                        context.log(
                            "Browser-context transfer could not reconstruct "
                            f"the media: {transfer_exc}")
                    # Falling through: the loop's normal recovery path tries
                    # the next stabilized candidate.
            except Exception as exc:
                started = False
                failure = str(exc)
            else:
                failure = "transfer did not confirm startup"
            if started:
                store.mark_handoff(candidate.candidate_id, True)
                context.transition(DownloadState.DOWNLOADER_RESUMED,
                                   message="Validated browser handoff resumed")
                try:
                    episode.commit()
                except Exception:
                    # A confirmed transfer is authoritative; close cleanup is best effort.
                    pass
                context.log("Validated browser handoff resumed the existing task.")
                return
            store.mark_handoff(candidate.candidate_id, False)
            failed_ids.add(candidate.candidate_id)
            context.transition(DownloadState.FALLBACK_RECOVERING,
                               message="Trying another stabilized media candidate")
            context.log("Browser handoff candidate failed validation; recovering within this task.")
        raise self._terminal_handoff_error(
            browser_fetch_credit_seen, external_replay_rejection_seen,
            "No browser media candidate could start a validated transfer")

    @staticmethod
    def _terminal_handoff_error(browser_fetch_credit_seen: bool,
                                external_replay_rejection_seen: bool,
                                default_message: str) -> BrowserFallbackError:
        """Terminal classification for an exhausted stabilization loop.

        When a candidate carried confirmed browser-fetch credit (the
        protected browser fetched it with HTTP 200) and its external
        transfer was then rejected by the media server with a context-bound
        category, the honest task-level classification is
        "browser-accessible but externally non-transferable" - NOT an
        unstable/invalid candidate.  Every other exhaustion path keeps the
        classic message."""
        if browser_fetch_credit_seen and external_replay_rejection_seen:
            return BrowserFallbackError(
                "The protected browser fetched this media successfully, but the "
                "media server rejects an independent transfer replay "
                "(browser-accessible but externally non-transferable).")
        return BrowserFallbackError(default_message)

    @staticmethod
    def _store_has_user_started_candidate(store: CandidateStore) -> bool:
        """True when the store contains any candidate that followed a user
        interaction (as opposed to an autoplay widget cluster).  Segment
        children are collapsed into their manifest by the store, so this only
        ever inspects manifest/direct candidates."""
        return any(candidate.user_started for candidate in store.values())

    @staticmethod
    def _store_only_widget_shaped(store: CandidateStore) -> bool:
        """True when every stored candidate carries the generic live-widget URL
        signature (numeric stream id + rendition suffix).  The requested
        episode's manifest/master never matches, so once it appears this
        returns False and the interaction wait ends normally."""
        candidates = tuple(store.values())
        if not candidates:
            return False
        return all(
            looks_like_live_widget_url(candidate.current_url)
            for candidate in candidates
        )

    def _choose_candidate(self, store: CandidateStore, payload: Mapping[str, Any],
                          failed_ids: set[str]):
        candidates = tuple(
            candidate for candidate in store.values()
            if candidate.candidate_id not in failed_ids
            and candidate.lifecycle not in {
                CandidateLifecycle.EXPIRED,
                CandidateLifecycle.REJECTED,
                CandidateLifecycle.FAILED_HANDOFF,
            }
        )
        decision = CandidateRanker().decide(candidates, now=self.clock() + 3.0)
        chosen_id = decision.selected_candidate_id
        if not chosen_id and decision.ambiguous_candidate_ids:
            safe_candidates = tuple(
                candidate.safe_dict() for candidate in candidates
                if candidate.candidate_id in decision.ambiguous_candidate_ids
            )
            chosen_id = str(payload.get("selected_candidate_id") or "")
            if chosen_id not in decision.ambiguous_candidate_ids:
                if self.select_candidate is not None:
                    chosen_id = self.select_candidate(
                        safe_candidates, decision.explanation,
                    ) or ""
                elif decision.ranked:
                    # Automatic same-task fallback has no interactive selector:
                    # advance through the stabilized ranked list so a failed
                    # candidate gives way to the next valid one instead of
                    # failing the whole task on ambiguity.
                    chosen_id = decision.ranked[0].candidate_id
            if chosen_id not in decision.ambiguous_candidate_ids:
                raise BrowserSelectionRequired(safe_candidates, decision.explanation)
        if not chosen_id and decision.ranked:
            chosen_id = decision.ranked[0].candidate_id
        return store.get(chosen_id) if chosen_id else None

    def _candidates_from_payload(self, payload: Mapping[str, Any]) -> tuple[CandidateStore, tuple[Mapping[str, Any], ...]]:
        store = CandidateStore()
        records = tuple(item for item in (payload.get("media_candidates") or ()) if isinstance(item, Mapping))
        observed_at = self.clock()
        for item in records:
            candidate = store.observe(
                url=str(item.get("url") or ""),
                content_type=str(item.get("content_type") or ""),
                timestamp=observed_at + (_nonnegative_float(item.get("observed_offset")) or 0.0),
                player_id=str(item.get("player_id") or ""),
                frame_id=str(item.get("frame_id") or ""),
                primary_player=bool(item.get("primary_player", True)),
                nested_frame=bool(item.get("nested_frame")),
                popup_context=bool(item.get("popup_context")),
                segment_parent_url=str(item.get("segment_parent_url") or ""),
                content_length=_positive_int(item.get("content_length")),
                playing=bool(item.get("playing", True)),
                duration_seconds=_nonnegative_float(item.get("duration_seconds")),
                width=_positive_int(item.get("width")), height=_positive_int(item.get("height")),
                user_started=bool(item.get("user_started", True)),
                nuisance_score=max(0, int(item.get("nuisance_score") or 0)),
                required_header_names=(item.get("headers") or {}).keys(),
            )
            if candidate:
                candidate.request_count = max(candidate.request_count, int(item.get("request_count") or 2))
        return store, records

    @staticmethod
    def _bundle(task_id: str, source_url: str, candidate, payload: Mapping[str, Any],
                records: tuple[Mapping[str, Any], ...]) -> HandoffBundle:
        record = next((item for item in records if str(item.get("url") or "") == candidate.current_url), {})
        headers = {
            str(name): str(value) for name, value in (record.get("headers") or {}).items()
            if str(name).lower() in {"accept", "accept-language", "origin", "referer", "user-agent"}
        }
        raw_cookies = payload.get("cookies") or ()
        cookies = tuple(dict(cookie) for cookie in raw_cookies if isinstance(cookie, Mapping))
        return HandoffBundle(
            task_id=task_id, candidate_id=candidate.candidate_id,
            media_url=candidate.current_url, media_kind=candidate.kind,
            user_agent=str(payload.get("user_agent") or ""),
            referer=str(payload.get("referer") or source_url),
            origin=str(payload.get("origin") or ""),
            cookies=cookies, headers=headers,
            expected_content_types=(candidate.content_type,) if candidate.content_type else (),
            observed_status=int(record.get("status") or 0),
            observed_content_type=str(record.get("content_type") or ""),
            expected_duration_seconds=float(candidate.duration_seconds or 0.0),
        )

    @staticmethod
    def _emit_protection_stats(context, payload: Mapping[str, Any]) -> None:
        context.emit("browser_protection_stats", data={
            "blocked_popups": int(payload.get("blocked_popup_count") or 0),
            "blocked_navigations": int(payload.get("blocked_navigation_count") or 0),
            "rejected_requests": int(payload.get("rejected_junk_count") or 0),
            "dropped_observations": int(payload.get("dropped_request_count") or 0),
        })


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
