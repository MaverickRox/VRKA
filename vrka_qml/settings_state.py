"""Settings persistence for the QML application (Stage 6).

Owns the single SETTINGS_FILE (``~/.vrka/settings.json``) using the exact
3.0 JSON schema returned by ``VRKADownloader.collect_settings``. No second
store exists. Validation reuses the monolith validators; migration reuses the
monolith migrate helpers; persistence uses the monolith atomic writer.
QML binds to this object; Python owns the authoritative copy.
"""

from __future__ import annotations

import vrka_downloader as app

from PySide6.QtCore import Property, QObject, Signal, Slot

# Exact key set from VRKADownloader.collect_settings (vrka_downloader.py:7646).
SETTINGS_KEYS = (
    "appearance_mode",
    "output_folder",
    "mode",
    "quality",
    "fps60",
    "audio_format",
    "mp3_bitrate",
    "download_subs",
    "sub_langs",
    "embed_subs",
    "auto_captions",
    "embed_thumbnail",
    "embed_metadata",
    "sponsorblock",
    "sponsorblock_categories",
    "proxy",
    "rate_limit",
    "force_ipv4",
    "restrict_filenames",
    "output_template",
    "use_archive",
    "format_sort",
    "allow_remote_components",
    "impersonation",
    "ytdlp_channel",
    "ytdlp_check_on_startup",
    "cookie_mode",
    "cookie_browser",
    "cookie_profile",
    "cookie_file",
)


def _default_settings(output_folder: str) -> dict:
    return {
        "appearance_mode": "Dark",
        "output_folder": output_folder,
        "mode": "video",
        "quality": "1080p (Full HD)",
        "fps60": False,
        "audio_format": "FLAC (Lossless container)",
        "mp3_bitrate": "320 kbps",
        "download_subs": False,
        "sub_langs": app.DEFAULT_SUBTITLE_LANGUAGE_PATTERN,
        "embed_subs": False,
        "auto_captions": False,
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
        "impersonation": "Automatic",
        "ytdlp_channel": app.DEFAULT_YTDLP_CHANNEL,
        "ytdlp_check_on_startup": False,
        "cookie_mode": "Disabled",
        "cookie_browser": "Chrome",
        "cookie_profile": "",
        "cookie_file": "",
    }


class SettingsState(QObject):
    """QML-facing settings container — one instance, one file."""

    # Per-field change signals (QML bindings need distinct notifies).
    appearanceModeChanged = Signal()
    outputFolderChanged = Signal()
    modeChanged = Signal()
    qualityChanged = Signal()
    fps60Changed = Signal()
    audioFormatChanged = Signal()
    mp3BitrateChanged = Signal()
    downloadSubsChanged = Signal()
    subLangsChanged = Signal()
    embedSubsChanged = Signal()
    autoCaptionsChanged = Signal()
    embedThumbnailChanged = Signal()
    embedMetadataChanged = Signal()
    sponsorblockChanged = Signal()
    sponsorblockCategoriesChanged = Signal()
    proxyChanged = Signal()
    rateLimitChanged = Signal()
    forceIpv4Changed = Signal()
    restrictFilenamesChanged = Signal()
    outputTemplateChanged = Signal()
    useArchiveChanged = Signal()
    formatSortChanged = Signal()
    allowRemoteComponentsChanged = Signal()
    impersonationChanged = Signal()
    ytdlpChannelChanged = Signal()
    ytdlpCheckOnStartupChanged = Signal()
    cookieModeChanged = Signal()
    cookieBrowserChanged = Signal()
    cookieProfileChanged = Signal()
    cookieFileChanged = Signal()

    # Batch signals
    settingsLoaded = Signal()
    settingsSaved = Signal()
    settingsSaveFailed = Signal(str, str)  # title, message

    def __init__(self, engine_host, parent=None):
        super().__init__(parent)
        self._host = engine_host
        self._data: dict = _default_settings(engine_host.output_folder)
        self._last_error: str = ""
        self._use_custom_command: bool = False
        self._custom_command: str = ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_for_key(self, key: str) -> None:
        mapping = {
            "appearance_mode": self.appearanceModeChanged,
            "output_folder": self.outputFolderChanged,
            "mode": self.modeChanged,
            "quality": self.qualityChanged,
            "fps60": self.fps60Changed,
            "audio_format": self.audioFormatChanged,
            "mp3_bitrate": self.mp3BitrateChanged,
            "download_subs": self.downloadSubsChanged,
            "sub_langs": self.subLangsChanged,
            "embed_subs": self.embedSubsChanged,
            "auto_captions": self.autoCaptionsChanged,
            "embed_thumbnail": self.embedThumbnailChanged,
            "embed_metadata": self.embedMetadataChanged,
            "sponsorblock": self.sponsorblockChanged,
            "sponsorblock_categories": self.sponsorblockCategoriesChanged,
            "proxy": self.proxyChanged,
            "rate_limit": self.rateLimitChanged,
            "force_ipv4": self.forceIpv4Changed,
            "restrict_filenames": self.restrictFilenamesChanged,
            "output_template": self.outputTemplateChanged,
            "use_archive": self.useArchiveChanged,
            "format_sort": self.formatSortChanged,
            "allow_remote_components": self.allowRemoteComponentsChanged,
            "impersonation": self.impersonationChanged,
            "ytdlp_channel": self.ytdlpChannelChanged,
            "ytdlp_check_on_startup": self.ytdlpCheckOnStartupChanged,
            "cookie_mode": self.cookieModeChanged,
            "cookie_browser": self.cookieBrowserChanged,
            "cookie_profile": self.cookieProfileChanged,
            "cookie_file": self.cookieFileChanged,
        }
        sig = mapping.get(key)
        if sig is not None:
            sig.emit()

    def _normalized_for_file(self) -> dict:
        """Return a filtered, persisted-safe copy (known keys only, no transient session keys)."""
        return {k: self._data[k] for k in SETTINGS_KEYS if k in self._data}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @Slot(result=bool)
    def load(self) -> bool:
        """Load from SETTINGS_FILE via the 3.0 loader (migrations included)."""
        try:
            raw = self._host.load_settings()  # delegates to VRKADownloader.load_settings
        except Exception:
            raw = {}
        if not isinstance(raw, dict) or not raw:
            # No file or empty — keep defaults (output_folder may still update).
            if isinstance(raw, dict) and "output_folder" in raw:
                pass  # handled below
            self.settingsLoaded.emit()
            return True
        changed: list[str] = []
        for key in SETTINGS_KEYS:
            if key in raw:
                value = raw[key]
                # Preserve types as stored, but keep file truth for strings/bools.
                if self._data.get(key) != value:
                    self._data[key] = value
                    changed.append(key)
        # Sync host output_folder (single authoritative runtime location).
        if "output_folder" in changed:
            self._host.output_folder = str(self._data["output_folder"] or self._data["output_folder"])
        # Also sync if the file had output_folder but defaults matched; still sync.
        if "output_folder" in raw:
            self._host.output_folder = str(raw["output_folder"])
            self._data["output_folder"] = str(raw["output_folder"])
        for key in changed:
            self._emit_for_key(key)
        # Always emit outputFolderChanged if raw had it (even if same, QML bindings still correct).
        self.settingsLoaded.emit()
        return True

    @Slot(result=bool)
    def save(self) -> bool:
        """Validate then atomically persist the current settings."""
        # 3.0 validation pass: output_template is the only settings field with a dedicated validator.
        try:
            app.validate_output_template(self._data.get("output_template", ""))
        except ValueError as exc:
            self.settingsSaveFailed.emit("Check Download Settings", str(exc))
            return False
        # Unknown keys are never persisted (filter to SETTINGS_KEYS).
        payload = self._normalized_for_file()
        try:
            app._atomic_write_json(app.SETTINGS_FILE, payload)
            # Keep host output_folder authoritative.
            self._host.output_folder = str(payload.get("output_folder", self._host.output_folder))
            self.settingsSaved.emit()
            return True
        except Exception as exc:
            self.settingsSaveFailed.emit("Could Not Save", str(exc))
            return False

    # ------------------------------------------------------------------
    # Snapshot helpers (used by DownloadController and persistence checks)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        return dict(self._data)

    def download_defaults(self) -> dict:
        """Subset that corresponds to DOWNLOAD_OPTION_DEFAULTS keys (effective download options)."""
        # Only keys that appear in DOWNLOAD_OPTION_DEFAULTS matter for download submission.
        from .download_controller import DOWNLOAD_OPTION_DEFAULTS
        return {k: self._data[k] for k in DOWNLOAD_OPTION_DEFAULTS if k in self._data}

    # ------------------------------------------------------------------
    # QML Properties — one per collect_settings key
    # ------------------------------------------------------------------

    @Property(str, notify=appearanceModeChanged)
    def appearanceMode(self) -> str:
        v = self._data.get("appearance_mode", "Dark")
        return str(v) if v in ("Light", "Dark") else "Dark"

    @appearanceMode.setter
    def appearanceMode(self, v: str) -> None:
        cleaned = str(v or "").strip().capitalize()
        if cleaned not in ("Light", "Dark"):
            return
        if self._data.get("appearance_mode") == cleaned:
            return
        self._data["appearance_mode"] = cleaned
        self.appearanceModeChanged.emit()

    @Property(str, notify=outputFolderChanged)
    def outputFolder(self) -> str:
        return str(self._data.get("output_folder", self._host.output_folder))

    @outputFolder.setter
    def outputFolder(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if not cleaned or cleaned == self._data.get("output_folder"):
            return
        self._data["output_folder"] = cleaned
        self._host.output_folder = cleaned
        self.outputFolderChanged.emit()

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        return str(self._data.get("mode", "video"))

    @mode.setter
    def mode(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if cleaned not in ("video", "audio"):
            return
        if self._data.get("mode") == cleaned:
            return
        self._data["mode"] = cleaned
        self.modeChanged.emit()

    @Property(str, notify=qualityChanged)
    def quality(self) -> str:
        return str(self._data.get("quality", "1080p (Full HD)"))

    @quality.setter
    def quality(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if not cleaned or self._data.get("quality") == cleaned:
            return
        self._data["quality"] = cleaned
        self.qualityChanged.emit()

    @Property(bool, notify=fps60Changed)
    def fps60(self) -> bool:
        return bool(self._data.get("fps60", False))

    @fps60.setter
    def fps60(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("fps60") == b:
            return
        self._data["fps60"] = b
        self.fps60Changed.emit()

    @Property(str, notify=audioFormatChanged)
    def audioFormat(self) -> str:
        return str(self._data.get("audio_format", "FLAC (Lossless container)"))

    @audioFormat.setter
    def audioFormat(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if not cleaned or self._data.get("audio_format") == cleaned:
            return
        self._data["audio_format"] = cleaned
        self.audioFormatChanged.emit()

    @Property(str, notify=mp3BitrateChanged)
    def mp3Bitrate(self) -> str:
        return str(self._data.get("mp3_bitrate", "320 kbps"))

    @mp3Bitrate.setter
    def mp3Bitrate(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if not cleaned or self._data.get("mp3_bitrate") == cleaned:
            return
        self._data["mp3_bitrate"] = cleaned
        self.mp3BitrateChanged.emit()

    @Property(bool, notify=downloadSubsChanged)
    def downloadSubs(self) -> bool:
        return bool(self._data.get("download_subs", False))

    @downloadSubs.setter
    def downloadSubs(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("download_subs") == b:
            return
        self._data["download_subs"] = b
        self.downloadSubsChanged.emit()

    @Property(str, notify=subLangsChanged)
    def subLangs(self) -> str:
        return str(self._data.get("sub_langs", app.DEFAULT_SUBTITLE_LANGUAGE_PATTERN))

    @subLangs.setter
    def subLangs(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if self._data.get("sub_langs") == cleaned:
            return
        self._data["sub_langs"] = cleaned
        self.subLangsChanged.emit()

    @Property(bool, notify=embedSubsChanged)
    def embedSubs(self) -> bool:
        return bool(self._data.get("embed_subs", False))

    @embedSubs.setter
    def embedSubs(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("embed_subs") == b:
            return
        self._data["embed_subs"] = b
        self.embedSubsChanged.emit()

    @Property(bool, notify=autoCaptionsChanged)
    def autoCaptions(self) -> bool:
        return bool(self._data.get("auto_captions", False))

    @autoCaptions.setter
    def autoCaptions(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("auto_captions") == b:
            return
        self._data["auto_captions"] = b
        self.autoCaptionsChanged.emit()

    @Property(bool, notify=embedThumbnailChanged)
    def embedThumbnail(self) -> bool:
        return bool(self._data.get("embed_thumbnail", False))

    @embedThumbnail.setter
    def embedThumbnail(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("embed_thumbnail") == b:
            return
        self._data["embed_thumbnail"] = b
        self.embedThumbnailChanged.emit()

    @Property(bool, notify=embedMetadataChanged)
    def embedMetadata(self) -> bool:
        return bool(self._data.get("embed_metadata", False))

    @embedMetadata.setter
    def embedMetadata(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("embed_metadata") == b:
            return
        self._data["embed_metadata"] = b
        self.embedMetadataChanged.emit()

    @Property(bool, notify=sponsorblockChanged)
    def sponsorblock(self) -> bool:
        return bool(self._data.get("sponsorblock", False))

    @sponsorblock.setter
    def sponsorblock(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("sponsorblock") == b:
            return
        self._data["sponsorblock"] = b
        self.sponsorblockChanged.emit()

    @Property(str, notify=sponsorblockCategoriesChanged)
    def sponsorblockCategories(self) -> str:
        return str(self._data.get("sponsorblock_categories", ""))

    @sponsorblockCategories.setter
    def sponsorblockCategories(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if self._data.get("sponsorblock_categories") == cleaned:
            return
        self._data["sponsorblock_categories"] = cleaned
        self.sponsorblockCategoriesChanged.emit()

    @Property(str, notify=proxyChanged)
    def proxy(self) -> str:
        return str(self._data.get("proxy", ""))

    @proxy.setter
    def proxy(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if self._data.get("proxy") == cleaned:
            return
        self._data["proxy"] = cleaned
        self.proxyChanged.emit()

    @Property(str, notify=rateLimitChanged)
    def rateLimit(self) -> str:
        return str(self._data.get("rate_limit", ""))

    @rateLimit.setter
    def rateLimit(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if self._data.get("rate_limit") == cleaned:
            return
        self._data["rate_limit"] = cleaned
        self.rateLimitChanged.emit()

    @Property(bool, notify=forceIpv4Changed)
    def forceIpv4(self) -> bool:
        return bool(self._data.get("force_ipv4", False))

    @forceIpv4.setter
    def forceIpv4(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("force_ipv4") == b:
            return
        self._data["force_ipv4"] = b
        self.forceIpv4Changed.emit()

    @Property(bool, notify=restrictFilenamesChanged)
    def restrictFilenames(self) -> bool:
        return bool(self._data.get("restrict_filenames", False))

    @restrictFilenames.setter
    def restrictFilenames(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("restrict_filenames") == b:
            return
        self._data["restrict_filenames"] = b
        self.restrictFilenamesChanged.emit()

    @Property(str, notify=outputTemplateChanged)
    def outputTemplate(self) -> str:
        return str(self._data.get("output_template", app.DEFAULT_OUTPUT_TEMPLATE))

    @outputTemplate.setter
    def outputTemplate(self, v: str) -> None:
        cleaned = str(v or "").strip() or app.DEFAULT_OUTPUT_TEMPLATE
        if self._data.get("output_template") == cleaned:
            return
        self._data["output_template"] = cleaned
        self.outputTemplateChanged.emit()

    @Property(bool, notify=useArchiveChanged)
    def useArchive(self) -> bool:
        return bool(self._data.get("use_archive", False))

    @useArchive.setter
    def useArchive(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("use_archive") == b:
            return
        self._data["use_archive"] = b
        self.useArchiveChanged.emit()

    @Property(str, notify=formatSortChanged)
    def formatSort(self) -> str:
        return str(self._data.get("format_sort", ""))

    @formatSort.setter
    def formatSort(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if self._data.get("format_sort") == cleaned:
            return
        self._data["format_sort"] = cleaned
        self.formatSortChanged.emit()

    @Property(bool, notify=allowRemoteComponentsChanged)
    def allowRemoteComponents(self) -> bool:
        return bool(self._data.get("allow_remote_components", False))

    @allowRemoteComponents.setter
    def allowRemoteComponents(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("allow_remote_components") == b:
            return
        self._data["allow_remote_components"] = b
        self.allowRemoteComponentsChanged.emit()

    @Property(str, notify=impersonationChanged)
    def impersonation(self) -> str:
        return str(self._data.get("impersonation", "Automatic"))

    @impersonation.setter
    def impersonation(self, v: str) -> None:
        cleaned = str(v or "").strip() or "Automatic"
        if self._data.get("impersonation") == cleaned:
            return
        self._data["impersonation"] = cleaned
        self.impersonationChanged.emit()

    @Property(str, notify=ytdlpChannelChanged)
    def ytdlpChannel(self) -> str:
        return str(self._data.get("ytdlp_channel", app.DEFAULT_YTDLP_CHANNEL))

    @ytdlpChannel.setter
    def ytdlpChannel(self, v: str) -> None:
        cleaned = str(v or "").strip() or app.DEFAULT_YTDLP_CHANNEL
        if cleaned not in ("Stable", "Nightly"):
            return
        if self._data.get("ytdlp_channel") == cleaned:
            return
        self._data["ytdlp_channel"] = cleaned
        self.ytdlpChannelChanged.emit()

    @Property(bool, notify=ytdlpCheckOnStartupChanged)
    def ytdlpCheckOnStartup(self) -> bool:
        return bool(self._data.get("ytdlp_check_on_startup", False))

    @ytdlpCheckOnStartup.setter
    def ytdlpCheckOnStartup(self, v: bool) -> None:
        b = bool(v)
        if self._data.get("ytdlp_check_on_startup") == b:
            return
        self._data["ytdlp_check_on_startup"] = b
        self.ytdlpCheckOnStartupChanged.emit()

    @Property(str, notify=cookieModeChanged)
    def cookieMode(self) -> str:
        return str(self._data.get("cookie_mode", "Disabled"))

    @cookieMode.setter
    def cookieMode(self, v: str) -> None:
        cleaned = str(v or "").strip() or "Disabled"
        if cleaned not in ("Disabled", "Selected Browser", "cookies.txt File"):
            # 3.0 migration maps "Verified Session"->Disabled and legacy labels;
            # we only accept current labels.
            if cleaned == "Verified Session":
                cleaned = "Disabled"
            else:
                return
        if self._data.get("cookie_mode") == cleaned:
            return
        self._data["cookie_mode"] = cleaned
        self.cookieModeChanged.emit()

    @Property(str, notify=cookieBrowserChanged)
    def cookieBrowser(self) -> str:
        return str(self._data.get("cookie_browser", "Chrome"))

    @cookieBrowser.setter
    def cookieBrowser(self, v: str) -> None:
        cleaned = str(v or "").strip() or "Chrome"
        if self._data.get("cookie_browser") == cleaned:
            return
        self._data["cookie_browser"] = cleaned
        self.cookieBrowserChanged.emit()

    @Property(str, notify=cookieProfileChanged)
    def cookieProfile(self) -> str:
        return str(self._data.get("cookie_profile", ""))

    @cookieProfile.setter
    def cookieProfile(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if self._data.get("cookie_profile") == cleaned:
            return
        self._data["cookie_profile"] = cleaned
        self.cookieProfileChanged.emit()

    @Property(str, notify=cookieFileChanged)
    def cookieFile(self) -> str:
        return str(self._data.get("cookie_file", ""))

    @cookieFile.setter
    def cookieFile(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if self._data.get("cookie_file") == cleaned:
            return
        self._data["cookie_file"] = cleaned
        self.cookieFileChanged.emit()

    # Transient Advanced custom command (not persisted, per-next-download, mirrors 3.0's
    # "I understand: use this custom command for the next queued download").
    useCustomCommandChanged = Signal()
    customCommandChanged = Signal()

    @Property(bool, notify=useCustomCommandChanged)
    def useCustomCommand(self) -> bool:
        return bool(getattr(self, "_use_custom_command", False))

    @useCustomCommand.setter
    def useCustomCommand(self, v: bool) -> None:
        b = bool(v)
        if getattr(self, "_use_custom_command", False) == b:
            return
        self._use_custom_command = b
        self.useCustomCommandChanged.emit()

    @Property(str, notify=customCommandChanged)
    def customCommand(self) -> str:
        return str(getattr(self, "_custom_command", ""))

    @customCommand.setter
    def customCommand(self, v: str) -> None:
        cleaned = str(v or "").strip()
        if getattr(self, "_custom_command", "") == cleaned:
            return
        self._custom_command = cleaned
        self.customCommandChanged.emit()
