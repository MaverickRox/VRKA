# Third-Party Notices

VRKA 3.5 build 012 is built with the following pinned Python packages:

- CustomTkinter 6.0.0
- yt-dlp 2026.8.19
- yt-dlp-ejs 0.8.0
- curl_cffi 0.15.0
- Pillow 12.3.0
- PyInstaller 6.21.0
- pywebview 6.2.1
- PySide6 6.11.2 (LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only) — Qt 6 QML runtime
  (shiboken6 6.11.2 included). Distributed as dynamically linked libraries inside
  the installer; source at https://code.qt.io/cgit/pyside/pyside-setup.git and
  https://www.qt.io/download-open-source. LGPL-3.0 permits this distribution as
  bundled DLLs with this notice; no VRKA source disclosure is required for the
  unmodified QML application. Users may replace the bundled PySide6/Qt DLLs
  inside the PyInstaller `_MEIPASS` extraction (LGPL relinking right).

The Windows package also bundles FFmpeg and FFprobe executables. Their upstream licences and notices remain applicable. A JavaScript runtime (Deno, Bun, Node.js, or QuickJS) is NOT bundled: when one is present on the user's system, yt-dlp may use it for YouTube challenge solving; without one, most downloads still work and yt-dlp degrades gracefully. yt-dlp can optionally fetch official challenge-solver components only when the user enables that setting.

The protected-browser feature bundles uBlock Origin Lite (uBOL) 2026.812.1211 as a browser extension for content filtering. uBOL is distributed under the GPLv3 licence; the extension's own source and licence texts are included inside the bundled extension package (`assets/browser_protection/ubol.zip`, extracted to `%LOCALAPPDATA%\VRKA\browser-ext` at first use). The extension's filter-list rulesets (uBO filters, EasyList, EasyPrivacy, Peter Lowe's list, uBO Badware risks, and uBlock filters – URLhaus) remain the property of their respective maintainers and are licensed separately by them.

Space Mono is Copyright 2016 The Space Mono Project Authors and is distributed under the SIL Open Font License 1.1. The complete licence text is included at `assets/fonts/OFL.txt`.

VRKA's application source and branding remain subject to the project owner's terms. Third-party names and marks belong to their respective owners.

---

## puemos/hls-downloader (media observer extension)

- Version: 5.5.0 (MV3-Chromium build extension-mv3-chrome.zip)
- Source: https://github.com/puemos/hls-downloader
- Upstream commit at tag v5.5.0: 408b43f7c0f73ea7efd4153199f3935e38e657eb
- Artifact SHA-256: 39dc660989c8a219fd0f85e203e2a268486d40a18f743b43dde8a71c1f680a52
- License: MIT (upstream LICENSE preserved in third_party/media_observer/puemos-hls-downloader/)
- Files shipped: the unmodified upstream extension archive, bundled inside the
  application executable (third_party/media_observer/puemos-hls-downloader/).
- Modifications to upstream code: NONE.
- Usage: passive media observation inside VRKA's own protected browser profile
  only. The upstream downloader/muxer subsystems are not invoked by VRKA.
