"""Operational integration for Stage 7: browser fallback, MediaObserver, yt-dlp updater.

Presentation layer only. No WebView2 objects, no scheduler co-ownership, no
second queue/event bus. Browser events flow via the existing Bridge typed
signals (single queue consumer). Observer/updater run in worker threads and
post results to the GUI thread via Qt queued signals.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

import vrka_downloader as app


class OperationalController(QObject):
    # Browser session state machine (mirrors 3.0 ui_queue contracts):
    # idle -> needed -> ready | error
    browserStateChanged = Signal()
    browserErrorChanged = Signal()
    browserNeededUrlChanged = Signal()
    browserNeededCategoryChanged = Signal()
    browserReadySummaryChanged = Signal()

    # Media observer
    observerStatusChanged = Signal()
    observerHealthChanged = Signal()
    observerStatusTextChanged = Signal()

    # Updater
    updaterBusyChanged = Signal()
    updaterStatusTextChanged = Signal()
    updaterCurrentVersionChanged = Signal()
    updaterAvailableVersionChanged = Signal()
    updaterUpdateAvailableChanged = Signal()

    def __init__(self, engine_host, bridge, settings_state, parent=None):
        super().__init__(parent)
        self._host = engine_host
        self._bridge = bridge
        self._settings = settings_state

        self._browser_state: str = "idle"  # idle | needed | ready | error
        self._browser_error: str = ""
        self._browser_needed_url: str = ""
        self._browser_needed_category: str = ""
        self._browser_ready_summary: str = ""

        self._observer_status_text: str = ""
        self._observer_health_ok: bool = False

        self._updater_busy: bool = False
        self._updater_status_text: str = ""
        self._updater_current_version: str = ""
        self._updater_available_version: str = ""
        self._updater_update_available: bool = False

        # Wire existing bridge signals (single queue consumer stays intact)
        bridge.browserNeeded.connect(self._on_browser_needed)
        bridge.browserSessionReady.connect(self._on_browser_ready)
        bridge.browserSessionError.connect(self._on_browser_error)

        # Seed initial operational snapshots (observer only; updater deferred to avoid yt-dlp at startup).
        self._refresh_observer_snapshot()
        self._updater_status_text = "yt-dlp runtime not yet queried — open Settings to refresh."
        self._updater_current_version = "deferred"

    # ------------------------------------------------------------------
    # Browser session (event-driven via bridge)
    # ------------------------------------------------------------------

    @Property(str, notify=browserStateChanged)
    def browserState(self) -> str:
        return self._browser_state

    @Property(str, notify=browserErrorChanged)
    def browserError(self) -> str:
        return self._browser_error

    @Property(str, notify=browserNeededUrlChanged)
    def browserNeededUrl(self) -> str:
        return self._browser_needed_url

    @Property(str, notify=browserNeededCategoryChanged)
    def browserNeededCategory(self) -> str:
        return self._browser_needed_category

    @Property(str, notify=browserReadySummaryChanged)
    def browserReadySummary(self) -> str:
        return self._browser_ready_summary

    def _on_browser_needed(self, url: str, category: str) -> None:
        self._browser_needed_url = str(url)
        self._browser_needed_category = str(category)
        self._browser_state = "needed"
        self._browser_error = ""
        self.browserNeededUrlChanged.emit()
        self.browserNeededCategoryChanged.emit()
        self.browserStateChanged.emit()
        self.browserErrorChanged.emit()

    def _on_browser_ready(self, payload: dict) -> None:
        # payload is from browser_session_ready: ok, media_candidates, etc.
        try:
            count = len(list(payload.get("media_candidates") or [])[:10])
            observed = int(payload.get("observed_request_count") or 0)
            self._browser_ready_summary = f"Session ready: {observed} request(s), {count} candidate(s)"
        except Exception:
            self._browser_ready_summary = "Session ready"
        # Also mirror host verified session for retry propagation if needed.
        try:
            self._host._verified_session = dict(payload)
        except Exception:
            pass
        self._browser_state = "ready"
        self._browser_error = ""
        self.browserReadySummaryChanged.emit()
        self.browserStateChanged.emit()
        self.browserErrorChanged.emit()

    def _on_browser_error(self, message: str) -> None:
        self._browser_error = str(message)
        self._browser_state = "error"
        self.browserErrorChanged.emit()
        self.browserStateChanged.emit()

    @Slot()
    def clearBrowserSession(self) -> None:
        try:
            self._host._verified_session = {}
            self._host._browser_candidate_map = {"Automatic": None}
        except Exception:
            pass
        self._browser_state = "idle"
        self._browser_error = ""
        self._browser_needed_url = ""
        self._browser_needed_category = ""
        self._browser_ready_summary = ""

    @Slot()
    def openVerificationWindow(self) -> None:
        # Presentation-only trigger: in 3.0 this opened pywebview verification window.
        # In QML the same backend path is exercised via the scheduler's browser fallback;
        # here we surface a log entry and keep the status machine idle/needed.
        try:
            self._host.ui_queue.put(("log", "Browser verification window requested (QML stub)."))
        except Exception:
            pass
        self.browserStateChanged.emit()
        self.browserErrorChanged.emit()
        self.browserNeededUrlChanged.emit()
        self.browserNeededCategoryChanged.emit()
        self.browserReadySummaryChanged.emit()

    @Property(bool)
    def browserFallbackEnabled(self) -> bool:
        # 3.0 disables fallback only for custom command mode; otherwise enabled.
        # Download mode comes from Settings or DownloadPage; we expose the
        # persistent default (Settings.mode) and DownloadController handles
        # per-task override. For operational display, fallback is available.
        return True

    # ------------------------------------------------------------------
    # MediaObserver (on-demand, worker-threaded)
    # ------------------------------------------------------------------

    @Property(str, notify=observerStatusTextChanged)
    def observerStatusText(self) -> str:
        return self._observer_status_text

    @Property(bool, notify=observerHealthChanged)
    def observerHealthOk(self) -> bool:
        return self._observer_health_ok

    def _refresh_observer_snapshot(self) -> None:
        try:
            text = self._host._media_observer_status_text()  # delegated from VRKADownloader
        except Exception as exc:
            text = f"Media observer status unavailable: {exc}"
            self._observer_health_ok = False
            self._observer_status_text = text
            self.observerStatusTextChanged.emit()
            self.observerHealthChanged.emit()
            return
        # Determine health via adapter health() when available.
        health_ok = False
        try:
            adapter = self._host._media_observer_adapter()
            health = adapter.health()
            health_ok = bool(health.get("ok"))
        except Exception:
            pass
        self._observer_status_text = str(text)
        self._observer_health_ok = bool(health_ok)
        self.observerStatusTextChanged.emit()
        self.observerHealthChanged.emit()

    @Slot()
    def refreshObserverStatus(self) -> None:
        # Synchronous refresh (no network) — safe on GUI thread.
        self._refresh_observer_snapshot()

    @Slot()
    def checkObserverUpdate(self) -> None:
        if self._updater_busy:
            return
        self._updater_busy = True
        self.updaterBusyChanged.emit()
        # This slot is for observer check; re-use updater busy flag for simplicity
        # But expose observer via status text after worker.
        def _worker():
            try:
                from vrka_core.media_observer import check_for_update
                info = check_for_update()
                if info.get("error"):
                    text = f"Observer check failed: {info.get('error')}"
                elif info.get("update_available"):
                    text = f"Update available: {info.get('available_version')} (installed {info.get('current_version')})"
                else:
                    text = f"Observer up to date (latest {info.get('available_version')})"
                self._observer_status_text = text
                self.observerStatusTextChanged.emit()
                # Also refresh health snapshot
                self._refresh_observer_snapshot()
            except Exception as exc:
                self._observer_status_text = f"Observer check error: {exc}"
                self.observerStatusTextChanged.emit()
            finally:
                self._updater_busy = False
                self.updaterBusyChanged.emit()
        threading.Thread(target=_worker, daemon=True).start()

    @Slot()
    def applyObserverUpdate(self) -> None:
        if self._updater_busy:
            return
        self._updater_busy = True
        self._observer_status_text = "Updating media observer..."
        self.observerStatusTextChanged.emit()
        self.updaterBusyChanged.emit()
        def _worker():
            try:
                from vrka_core.media_observer import apply_update
                result = apply_update()
                if result.get("updated"):
                    self._observer_status_text = f"Updated to {result.get('installed_version')}"
                elif result.get("message"):
                    self._observer_status_text = str(result.get("message"))
                else:
                    self._observer_status_text = f"Observer update failed: {result.get('error')}"
                self.observerStatusTextChanged.emit()
                self._refresh_observer_snapshot()
            except Exception as exc:
                self._observer_status_text = f"Observer update error: {exc}"
                self.observerStatusTextChanged.emit()
            finally:
                self._updater_busy = False
                self.updaterBusyChanged.emit()
        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # yt-dlp updater (worker-threaded, mirrors VRKADownloader.run_update flow)
    # ------------------------------------------------------------------

    @Property(bool, notify=updaterBusyChanged)
    def updaterBusy(self) -> bool:
        return self._updater_busy

    @Property(str, notify=updaterStatusTextChanged)
    def updaterStatusText(self) -> str:
        return self._updater_status_text

    @Property(str, notify=updaterCurrentVersionChanged)
    def updaterCurrentVersion(self) -> str:
        return self._updater_current_version

    @Property(str, notify=updaterAvailableVersionChanged)
    def updaterAvailableVersion(self) -> str:
        return self._updater_available_version

    @Property(bool, notify=updaterUpdateAvailableChanged)
    def updaterUpdateAvailable(self) -> bool:
        return self._updater_update_available

    def _refresh_updater_snapshot(self) -> None:
        try:
            summary = app.active_ytdlp_summary()
            self._updater_current_version = f"{summary.get('version')} ({summary.get('source')})"
            self._updater_status_text = f"Active: {self._updater_current_version}"
        except Exception as exc:
            self._updater_current_version = "?"
            self._updater_status_text = f"yt-dlp status unavailable: {exc}"
        self.updaterCurrentVersionChanged.emit()
        self.updaterStatusTextChanged.emit()

    @Slot()
    def checkUpdater(self) -> None:
        if self._updater_busy:
            return
        self._updater_busy = True
        self._updater_status_text = "Checking for yt-dlp updates..."
        self.updaterBusyChanged.emit()
        self.updaterStatusTextChanged.emit()
        channel = str(self._settings.ytdlpChannel) if self._settings else app.DEFAULT_YTDLP_CHANNEL
        def _worker():
            try:
                info = app.check_ytdlp_update(channel)
                available = str(info.get("available_version") or "")
                self._updater_available_version = available
                self._updater_update_available = bool(info.get("available"))
                if info.get("error"):
                    self._updater_status_text = f"Check failed: {info.get('error')}"
                elif info.get("available"):
                    self._updater_status_text = f"Update available: {available} (current {self._updater_current_version})"
                else:
                    self._updater_status_text = f"yt-dlp is current ({self._updater_current_version})"
                self.updaterAvailableVersionChanged.emit()
                self.updaterUpdateAvailableChanged.emit()
                self.updaterStatusTextChanged.emit()
            except Exception as exc:
                self._updater_status_text = f"Check failed: {exc}"
                self.updaterStatusTextChanged.emit()
            finally:
                self._updater_busy = False
                self.updaterBusyChanged.emit()
        threading.Thread(target=_worker, daemon=True).start()

    @Slot()
    def installUpdate(self) -> None:
        if self._updater_busy:
            return
        self._updater_busy = True
        self._updater_status_text = "Installing yt-dlp update..."
        self.updaterBusyChanged.emit()
        self.updaterStatusTextChanged.emit()
        channel = str(self._settings.ytdlpChannel) if self._settings else app.DEFAULT_YTDLP_CHANNEL
        def _worker():
            try:
                installed = app.install_ytdlp_update(channel)
                self._refresh_updater_snapshot()
                self._updater_status_text = f"Updated to {installed.get('version')} ({installed.get('channel')})"
                self.updaterStatusTextChanged.emit()
            except Exception as exc:
                self._updater_status_text = f"Update failed: {exc}"
                self.updaterStatusTextChanged.emit()
            finally:
                self._updater_busy = False
                self.updaterBusyChanged.emit()
        threading.Thread(target=_worker, daemon=True).start()

    @Slot()
    def rollbackUpdate(self) -> None:
        if self._updater_busy:
            return
        self._updater_busy = True
        self._updater_status_text = "Rolling back yt-dlp..."
        self.updaterBusyChanged.emit()
        self.updaterStatusTextChanged.emit()
        def _worker():
            try:
                info = app.rollback_ytdlp_update()
                self._updater_status_text = f"Rolled back to {info.get('version')}"
                self._refresh_updater_snapshot()
                self.updaterStatusTextChanged.emit()
            except Exception as exc:
                self._updater_status_text = f"Rollback failed: {exc}"
                self.updaterStatusTextChanged.emit()
            finally:
                self._updater_busy = False
                self.updaterBusyChanged.emit()
        threading.Thread(target=_worker, daemon=True).start()

    @Slot()
    def openNotices(self) -> None:
        try:
            notices = app.resource_path(app.Path("THIRD_PARTY_NOTICES.md"))
            if app.Path(notices).exists():
                app.open_path(str(notices))
        except Exception:
            pass
