"""Download submission presentation adapter (Stage 3).

The single QML-facing entry point into the existing download workflow.
Validation reuses the monolith validators; submission goes through the
existing ``Build008TaskAdapter`` (durable TaskScheduler path). No download
logic lives here and no backend object crosses into QML.
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import Property, QObject, Signal, Slot

import vrka_downloader as app

# Defaults mirror the 3.0 Download-page widgets and the Settings values that
# add_to_queue reads. Keys and values are the existing ones; none are invented.
DOWNLOAD_OPTION_DEFAULTS = {
    "quality": "1080p (Full HD)",
    "fps60": False,
    "audio_format": "FLAC (Lossless container)",
    "mp3_bitrate": "320 kbps",
    "impersonation": "Automatic",
    "download_subs": False,
    "sub_langs": app.DEFAULT_SUBTITLE_LANGUAGE_PATTERN,
    "embed_subs": False,
    "auto_captions": False,
    "is_playlist": False,
    "playlist_start": "",
    "playlist_end": "",
    "trim_enabled": False,
    "start_time": "",
    "end_time": "",
    "cookie_mode": "none",
    "cookie_browser": "Chrome",
    "cookie_profile": "",
    "cookie_file": "",
    "session_cookie_file": "",
    "session_media_candidates": [],
    "session_drm_detected": False,
    "session_user_agent": "",
    "session_referer": "",
    "session_origin": "",
    "session_page_title": "",
    "embed_thumbnail": False,
    "embed_metadata": False,
    "sponsorblock": False,
    "sponsorblock_categories": "",
    "proxy": "",
    "rate_limit": "",
    "force_ipv4": False,
    "restrict_filenames": False,
    "output_template": app.DEFAULT_OUTPUT_TEMPLATE,
    "use_archive": False,
    "format_sort": "",
    "allow_remote_components": False,
    "use_custom_command": False,
    "browser_fallback_enabled": True,
    "custom_command": "",
}

# The Stage 3 Download page offers the 3.0 page's modes; "custom" arrives
# with the advanced/custom-command surface in a later stage.
_DOWNLOAD_PAGE_MODES = ("video", "audio")


class DownloadController(QObject):
    submissionAccepted = Signal(str, str)  # task_id, url
    submissionFailed = Signal(str, str)  # short title, user-facing message
    outputFolderChanged = Signal()
    prefillRequested = Signal(str)  # url from history "Again" action

    def __init__(self, engine_host, settings_state=None, parent=None):
        super().__init__(parent)
        self._engine = engine_host
        self._settings = settings_state

    @Property(str, notify=outputFolderChanged)
    def outputFolder(self) -> str:
        return self._engine.output_folder

    @outputFolder.setter
    def outputFolder(self, value: str) -> None:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned == self._engine.output_folder:
            return
        self._engine.output_folder = cleaned
        self.outputFolderChanged.emit()

    @Slot(str, "QVariantMap", result=bool)
    def submitDownload(self, url: str, options) -> bool:
        """Validate and submit one download through the existing backend path."""
        # Custom command (transient, per-next-download) takes precedence over
        # the normal video/audio mode, mirroring 3.0's Advanced card.
        use_custom = False
        custom_cmd = ""
        if self._settings is not None and bool(getattr(self._settings, "useCustomCommand", False)):
            custom_cmd = str(getattr(self._settings, "customCommand", "") or "").strip()
            if custom_cmd:
                import shlex
                try:
                    app.validate_custom_ytdlp_arguments(shlex.split(custom_cmd))
                except ValueError as exc:
                    self.submissionFailed.emit("Custom Command Is Not Allowed", str(exc))
                    return False
                use_custom = True
                mode = "custom"
            else:
                self.submissionFailed.emit(
                    "Custom Command Is Empty",
                    "Either enter yt-dlp arguments in the custom command box or turn off the custom-command checkbox.",
                )
                return False
        try:
            clean_url = app.validate_media_url(url)
            options = dict(options or {})
            if not use_custom:
                mode = str(options.get("mode", "video"))
                if mode not in _DOWNLOAD_PAGE_MODES:
                    raise ValueError("Choose video or audio mode.")
        except ValueError as exc:
            self.submissionFailed.emit("Check Download Settings", str(exc))
            return False

        # Effective defaults: persisted settings override the hard-coded
        # DOWNLOAD_OPTION_DEFAULTS for fields that are user-configurable via
        # the Settings page. This preserves the single settings store.
        if self._settings is not None:
            base = dict(DOWNLOAD_OPTION_DEFAULTS)
            base.update(self._settings.download_defaults())
        else:
            base = dict(DOWNLOAD_OPTION_DEFAULTS)
        merged = dict(base)
        for key, value in dict(options).items():
            # Only known output keys reach the backend; mode is handled
            # separately as task.mode.
            if key in merged or key == "mode":
                merged[str(key)] = value
        merged.pop("mode", None)
        # output_folder is authoritative from the engine (synchronized with SettingsState).
        merged["output_folder"] = self._engine.output_folder
        if use_custom:
            merged["use_custom_command"] = True
            merged["custom_command"] = custom_cmd
            merged["browser_fallback_enabled"] = False
        else:
            merged["use_custom_command"] = False
            merged["custom_command"] = ""
        try:
            app.validate_output_template(merged.get("output_template", ""))
        except ValueError as exc:
            self.submissionFailed.emit("Check Download Settings", str(exc))
            return False

        task = app.DownloadTask(
            id=str(uuid.uuid4()), url=clean_url, mode=mode, options=merged,
        )
        try:
            self._engine.submit(task)
        except (RuntimeError, ValueError) as exc:
            self.submissionFailed.emit("Queue Unavailable", str(exc))
            return False

        self._engine.ui_queue.put(("log", f"Added to queue: {clean_url}"))
        self.submissionAccepted.emit(str(task.id), clean_url)
        if use_custom and self._settings is not None:
            # 3.0 clears the checkbox after the custom next-download is consumed
            try:
                self._settings.useCustomCommand = False
            except Exception:
                pass
        return True

    @Slot(str)
    def redownloadFromHistory(self, url: str) -> None:
        """Prefill the Download page URL from a History 'Again' action."""
        self.prefillRequested.emit(str(url))

    @Slot(result=str)
    def getClipboardText(self) -> str:
        """Read system clipboard text for the Download paste button."""
        from PySide6.QtGui import QGuiApplication
        cb = QGuiApplication.clipboard()
        if cb is not None:
            text = cb.text()
            return str(text or "").strip()
        return ""

    @Slot()
    def clearCompleted(self) -> None:
        """Convenience alias delegating clearCompleted through the engine host."""
        if hasattr(self._engine, "_queue_controller") and self._engine._queue_controller:
            self._engine._queue_controller.clearCompleted()
