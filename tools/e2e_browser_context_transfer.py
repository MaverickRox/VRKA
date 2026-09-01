"""Live end-to-end driver for the browser-context media transfer chain.

Drives the REAL production components from the source tree against a real
delivering provider page:

  real helper process (protected browser, uBOL, observer)
    -> real ProtectedBrowserFallback loop
    -> real external replay (yt-dlp) and its rejection
    -> real browser-context body capture + generic assembly + ffprobe

Direct extraction is represented as an immediate fallback-eligible failure
(its unsupported-URL behavior for this class is separately established).

Usage:
  python tools/e2e_browser_context_transfer.py --url <page> --out <dir>
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import vrka_downloader as app_module  # noqa: E402
from vrka_core import (  # noqa: E402
    ActivityPhase,
    AutomaticFallbackExecutor,
    DownloadState,
    ProcessInactivity,
    ProtectedBrowserFallback,
    SubprocessBrowserLauncher,
    TaskScheduler,
    TaskSpec,
    TaskStore,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--task-id", default="00000000-0000-4000-8000-000000000399")
    parser.add_argument("--wait-seconds", type=float, default=240.0)
    parser.add_argument("--force-rejection", action="store_true",
                        help="simulate the external-replay 403 (its natural "
                             "occurrence is proven on the target class) so the "
                             "browser-context chain can be exercised against "
                             "any delivering provider.  The resulting report "
                             "is marked rejection_simulated and is NOT "
                             "evidence that the provider delivered media to "
                             "a real replay attempt.")
    parser.add_argument("--click-delay", type=float, default=25.0,
                        help="seconds after launch before the harness "
                             "reproduces a user click on the largest video "
                             "element (playback initiation)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    app = object.__new__(app_module.VRKADownloader)
    app.ui_queue = queue.Queue()
    app._protected_browser_launcher = SubprocessBrowserLauncher(
        app_module.BROWSER_SESSION_DIR,
        app._protected_browser_command,
    )

    task = app_module.DownloadTask(args.task_id, args.url, "video", {
        "output_folder": str(out_dir),
        "browser_fallback_enabled": True,
    })
    task.title = "E2E browser-context transfer"
    task.options["_staging_dir"] = str(out_dir / "staging")
    Path(task.options["_staging_dir"]).mkdir(parents=True, exist_ok=True)

    def direct(_record, _context):
        # Established separately: this page class is unsupported by direct
        # extraction; the fallback chain under test starts here.
        raise ProcessInactivity(ActivityPhase.DIRECT_EXTRACTION, 45,
                                eligible_for_fallback=True)

    holder = {"episode": None, "last_payload": {}, "bctx_attempted": False}

    def launching_wrapper(record, context):
        episode = app._protected_browser_launcher(record, context)
        original_capture = episode.capture
        original_close = episode.close

        def capturing_capture(cancel_event, since_seq=0):
            payload = original_capture(cancel_event, since_seq)
            try:
                holder["last_payload"] = dict(payload)
            except Exception:
                pass
            return payload

        def closing():
            try:
                capture = holder["last_payload"].get("media_capture") or {}
                objects_dir = capture.get("objects_dir")
                if objects_dir and Path(objects_dir).is_dir():
                    import shutil
                    shutil.copytree(objects_dir,
                                    Path(out_dir) / "captured-objects",
                                    dirs_exist_ok=True)
                    (Path(out_dir) / "last-payload.json").write_text(
                        json.dumps(holder["last_payload"], indent="1"),
                        encoding="utf-8")
            except Exception:
                pass
            original_close()

        episode.capture = capturing_capture
        episode.close = closing
        holder["episode"] = episode
        return episode

    def playback_initiator():
        """Reproduce the user gesture that starts playback (harness-only):
        OS clicks at the reported player rect (screen coords of the largest
        player iframe/video) until observable media activity appears, then
        STOP - further clicks would pause playback.  Clicks resume only if
        media activity ceases while the transfer still needs bytes."""
        import ctypes
        from ctypes import wintypes

        def click_player():
            user32 = ctypes.windll.user32
            results = []

            def visit(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if title.value.startswith("VRKA Browser Verification"):
                    results.append(hwnd)
                return True

            callback = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
            user32.EnumWindows(callback, 0)
            if not results:
                return False
            hwnd = results[0]
            window_rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
                return False
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            rect = (holder["last_payload"].get("player_screen_rect") or {}) \
                if holder["last_payload"] else {}
            if rect.get("x") is not None and rect.get("w"):
                x = int(rect["x"] + rect["w"] / 2)
                y = int(rect["y"] + rect["h"] / 2)
            else:
                x = window_rect.left + (window_rect.right - window_rect.left) // 2
                y = window_rect.top + (window_rect.bottom - window_rect.top) // 2
            user32.SetCursorPos(x, y)
            time.sleep(0.05)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            return True

        try:
            time.sleep(args.click_delay)
        except InterruptedError:
            return
        while not stop.is_set():
            payload = holder["last_payload"]
            candidates = len(payload.get("media_candidates") or [])
            capture = payload.get("media_capture") or {}
            playing = candidates > 0 or int(capture.get("total_bytes") or 0) > 0
            if playing:
                break  # playback confirmed; clicking would pause it
            click_player()
            episode = holder.get("episode")
            if episode is not None:
                try:
                    episode.send_harness_command("clickvideo")
                except Exception:
                    pass
            try:
                time.sleep(15.0)
            except InterruptedError:
                return

    def capture_screenshots():
        """QA diagnostics: grab the helper window periodically during the
        browser-context capture so the seek-bar interaction is verifiable."""
        import ctypes
        from ctypes import wintypes
        from PIL import ImageGrab

        def find_helper_window():
            user32 = ctypes.windll.user32
            found = []

            def visit(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if title.value.startswith("VRKA Browser Verification"):
                    found.append(hwnd)
                return True

            callback = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
            user32.EnumWindows(callback, 0)
            return found[0] if found else None

        saved = 0
        while not stop.is_set() and saved < 6:
            if not holder["bctx_attempted"]:
                time.sleep(2.0)
                continue
            hwnd = find_helper_window()
            if hwnd:
                rect = wintypes.RECT()
                if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    image = ImageGrab.grab(bbox=(
                        rect.left, rect.top, rect.right, rect.bottom))
                    image.save(Path(out_dir) / f"capture-window-{saved}.png")
                    saved += 1
            time.sleep(12.0)

    def resume(bundle, context):
        if args.force_rejection:
            from vrka_core import ExternalReplayRejected
            context.log(
                "E2E: SIMULATED external-replay rejection (harness input, "
                "not provider evidence); exercising the browser-context "
                "chain.")
            raise ExternalReplayRejected(
                "The protected browser fetched this media, but the media "
                "server rejected the independent transfer replay.")
        return app._resume_protected_browser_transfer(
            task, str(out_dir), bundle, context)

    def browser_context_transfer(episode, bundle, context):
        holder["bctx_attempted"] = True
        return app._run_browser_context_transfer(
            episode, task, str(out_dir), bundle, context)

    browser = ProtectedBrowserFallback(
        launching_wrapper,
        resume,
        browser_context_transfer=browser_context_transfer,
        interaction_wait_seconds=120.0,
    )

    order = []
    stop = threading.Event()
    original_transition_holder = {"transition": None}

    with __import__("tempfile").TemporaryDirectory() as store_dir:
        scheduler = TaskScheduler(
            TaskStore(Path(store_dir) / "tasks.json"),
            AutomaticFallbackExecutor(direct, browser),
        )
        scheduler.submit(TaskSpec.create(
            args.url, "video", {"browser_fallback_enabled": True},
            task_id=args.task_id,
        ))
        done = threading.Event()

        def wait_idle():
            if scheduler.wait_for_idle(args.wait_seconds):
                done.set()

        watcher = threading.Thread(target=wait_idle, daemon=True)
        watcher.start()
        clicker = threading.Thread(target=playback_initiator, daemon=True)
        clicker.start()
        shooter = threading.Thread(target=capture_screenshots, daemon=True)
        shooter.start()
        done.wait(args.wait_seconds + 5)
        stop.set()

        record = scheduler.get(args.task_id)
        from vrka_core.browser_capture import classify_session_evidence
        evidence = classify_session_evidence(
            holder["last_payload"],
            record.state.value if hasattr(record.state, "value") else str(record.state),
            record.error or "",
            force_rejection=args.force_rejection,
            browser_context_attempted=holder["bctx_attempted"],
        )
        capture_state = holder["last_payload"].get("media_capture")
        log_messages = []
        try:
            for event in scheduler.events.snapshot():
                message = getattr(event, "message", "") or ""
                if message:
                    log_messages.append(message)
        except Exception:
            pass
        report = {
            "url": args.url,
            "state": record.state.value if hasattr(record.state, "value") else str(record.state),
            "error": record.error or "",
            "output_path": record.output_path or "",
            "task_output_path": task.output_path or "",
            "task_title": task.title or "",
            "evidence": evidence,
            "log_tail": log_messages[-14:],
            "capture_state_present": capture_state is not None,
            "capture_errors": (capture_state or {}).get("errors", [])[:5],
            "capture_stopped": (capture_state or {}).get("stopped"),
            "capture_active": (capture_state or {}).get("active"),
            "capture_attach_error": holder["last_payload"].get(
                "media_capture_error", ""),
            "last_payload_keys": sorted(holder["last_payload"].keys())[:40],
            "last_payload_seq": holder["last_payload"].get("capture_seq"),
            "note": (
                "external-replay rejection was SIMULATED by the harness; "
                "this run is not provider-replay evidence"
                if args.force_rejection else ""),
        }
        if task.output_path and Path(task.output_path).exists():
            summary = app._probe_media_summary(task.output_path)
            report["probe"] = summary
            report["output_bytes"] = Path(task.output_path).stat().st_size
        (out_dir / "e2e-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "probe"}, indent=2))
        if "probe" in report:
            print(json.dumps(report["probe"], indent=2)[:600])
        scheduler.shutdown()
    success = (record.state == DownloadState.COMPLETED
               and bool(task.output_path)
               and Path(task.output_path).exists())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
