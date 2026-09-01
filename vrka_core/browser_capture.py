"""Protected-browser media body capture (helper process, Windows only).

Attaches CoreWebView2.WebResourceResponseReceived handlers to the running
protected browser and copies the bodies of media responses to content-keyed
files on disk, producing a bounded manifest for the generic media assembly
module.  This is the production form of the lab-proven primitive
(lab/webctx_transfer): no CDP, no page scripts, no telemetry.

Threading contract (mirrors the lab findings):
- the WebView2 event handler only records metadata and STARTS
  GetContentAsync(); it never blocks;
- a small worker pool consumes the content operations and spills bytes to
  disk, so memory stays bounded regardless of output size;
- capture is cancellable and detachable; shutdown drains workers and closes
  streams.

Only responses the user's own authenticated browser actually fetched during
the controlled session are copied.  Cookie/Authorization header VALUES are
never recorded - request headers are stored as names only.
"""

from __future__ import annotations

import hashlib
import queue
import threading
import time
from pathlib import Path

from .media_assembly import classify

MAX_OBJECTS = 4000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB spill ceiling
WORKER_COUNT = 4


class MediaBodyCapture:
    """Bounded response-body capture for one protected-browser session."""

    def __init__(self, core, objects_dir: Path,
                 max_objects: int = MAX_OBJECTS,
                 max_total_bytes: int = MAX_TOTAL_BYTES,
                 worker_count: int = WORKER_COUNT):
        self.core = core
        self.objects_dir = Path(objects_dir)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.max_objects = max_objects
        self.max_total_bytes = max_total_bytes
        self._active = True
        self._lock = threading.Lock()
        self._work: queue.Queue = queue.Queue()
        self._workers: list[threading.Thread] = []
        self.state: dict = {
            "active": True, "objects": [], "redirects": {},
            "total_bytes": 0, "stopped": None, "errors": [],
        }
        self._handler_count = 0

    # -- lifecycle -------------------------------------------------------
    def attach(self) -> bool:
        try:
            self.core.WebResourceResponseReceived += self._on_response
        except Exception as exc:  # noqa: BLE001
            self.state["errors"].append(f"attach: {type(exc).__name__}")
            return False
        for index in range(self.worker_count()):
            worker = threading.Thread(target=self._worker_loop, daemon=True,
                                      name=f"vrka-capture-{index}")
            worker.start()
            self._workers.append(worker)
        return True

    def worker_count(self):
        return WORKER_COUNT

    def detach(self):
        """Stop accepting new captures; queued bodies are drained."""
        self._active = False
        self.state["active"] = False
        for _ in self._workers:
            self._work.put(None)
        for worker in self._workers:
            worker.join(timeout=5.0)
        self._workers.clear()

    def cleanup(self):
        """Remove spilled objects after the transfer outcome is decided."""
        import shutil
        shutil.rmtree(self.objects_dir, ignore_errors=True)

    # -- event handlers (WebView2 UI thread; never block) -----------------
    def _on_response(self, sender, args):
        if not self._active:
            return
        try:
            request = args.Request
            response = args.Response
            url = str(request.Uri)
            status = int(response.StatusCode)
            headers = {}
            for pair in response.Headers:
                headers[str(pair.Key)] = str(pair.Value)
            content_type = headers.get("Content-Type", "")
            if classify(url, content_type) == "other":
                return
            if status not in (200, 206):
                if status in (301, 302, 303, 307, 308):
                    location = headers.get("Location", "")
                    if location:
                        with self._lock:
                            self.state["redirects"][url.split("?")[0]] = location
                return
            with self._lock:
                if self.state["stopped"]:
                    return
                if (len(self.state["objects"]) >= self.max_objects
                        or self.state["total_bytes"] >= self.max_total_bytes):
                    self.state["stopped"] = "capture-limit"
                    self._active = False
                    return
            # Mandatory lab-proven pattern: start the async body read here,
            # hand the operation to a worker, return immediately.
            content_task = response.GetContentAsync()
            request_header_names = [
                str(pair.Key) for pair in request.Headers]
            self._work.put((url, status, content_type, content_task,
                            request_header_names, time.time()))
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if len(self.state["errors"]) < 20:
                    self.state["errors"].append(f"event: {type(exc).__name__}")

    # -- workers (background threads; all blocking happens here) ----------
    def _worker_loop(self):
        while True:
            item = self._work.get()
            if item is None:
                return
            url, status, content_type, content_task, header_names, started = item
            try:
                from System.IO import MemoryStream  # pythonnet; helper only
                deadline = time.time() + 60
                while not content_task.IsCompleted and time.time() < deadline:
                    time.sleep(0.005)
                if content_task.IsFaulted or not content_task.IsCompleted:
                    return
                stream = content_task.Result
                # Lab-proven read pattern: pythonnet marshals a COPY of a
                # Python bytearray into Stream.Read, so reads into Python
                # buffers silently yield zeros.  CopyTo performs the
                # marshaling inside .NET and returns the real bytes.
                sink = MemoryStream()
                stream.CopyTo(sink)
                data = bytes(sink.ToArray())
                copied = len(data)
                if copied <= 0:
                    return
                digest = hashlib.sha256(
                    f"{url}|{started}".encode()).hexdigest()[:20]
                target = self.objects_dir / f"obj-{digest}"
                with open(target, "wb") as handle:
                    handle.write(data)
                with self._lock:
                    self.state["total_bytes"] += copied
                    self.state["objects"].append({
                        "url": url,
                        "status": status,
                        "content_type": content_type,
                        "bytes": copied,
                        "object": target.name,
                        "request_header_names": header_names,
                        "seq": len(self.state["objects"]),
                        "ts": started,
                    })
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    if len(self.state["errors"]) < 20:
                        self.state["errors"].append(
                            f"worker: {type(exc).__name__}")

    # -- reporting --------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            objects = list(self.state["objects"])
            return {
                "active": self.state["active"],
                "objects": objects,
                "redirects": dict(self.state["redirects"]),
                "total_bytes": self.state["total_bytes"],
                "stopped": self.state["stopped"],
                "errors": list(self.state["errors"]),
                "objects_dir": str(self.objects_dir),
            }


def classify_session_evidence(payload: dict | None, state: str, error: str,
                              force_rejection: bool = False,
                              browser_context_attempted: bool = False) -> dict:
    """Evidence-based session classification for protected-browser runs.

    "Provider dry" is claimed ONLY when there is evidence that playback
    actually started (user-started media candidates or a playing player)
    and the provider still delivered no usable media.  A page that loaded
    but whose player was never initiated is reported as
    ``playback_not_initiated`` - never as provider failure.
    """
    payload = payload if isinstance(payload, dict) else {}
    candidates = payload.get("media_candidates") or []
    user_started = [c for c in candidates if c.get("user_started")]
    players = payload.get("player_state") or []
    capture = payload.get("media_capture") or {}
    captured_objects = [
        o for o in (capture.get("objects") or []) if int(o.get("bytes") or 0) > 0]
    evidence = {
        "page_loaded": bool(payload.get("ok")),
        "observed_request_count": int(payload.get("observed_request_count") or 0),
        "media_candidates": len(candidates),
        "user_started_candidates": len(user_started),
        "players_seen": len(players),
        "captured_objects": len(captured_objects),
        "captured_bytes": int(capture.get("total_bytes") or 0),
        "rejection_simulated": bool(force_rejection),
        "browser_context_attempted": bool(browser_context_attempted),
    }
    if state == "completed":
        session = "completed"
    elif browser_context_attempted:
        session = ("media_captured_but_transfer_failed" if captured_objects
                   else "browser_context_transfer_failed")
    elif user_started or any(p.get("playing") for p in players):
        # Playback genuinely started; if no usable media followed, only now
        # may the provider be held responsible.
        session = ("media_captured" if captured_objects
                   else "provider_media_delivery_failed")
    elif players or candidates:
        session = "playback_not_initiated"
    else:
        session = "playback_not_initiated"
    evidence["session"] = session
    return evidence
