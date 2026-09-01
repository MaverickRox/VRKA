# Third-Party Notices

VRKA 4.0.0 (Build 016) utilizes the following open-source components:

---

## Direct Python Dependencies

| Package | Version | License | Upstream Project | Usage |
| :--- | :--- | :--- | :--- | :--- |
| **PySide6** | 6.11.2 | LGPL-3.0 / GPL-2.0 / GPL-3.0 | [https://code.qt.io/cgit/pyside/pyside-setup.git](https://code.qt.io/cgit/pyside/pyside-setup.git) | Native Qt 6 QML desktop GUI runtime. Distributed as dynamically linked libraries. |
| **yt-dlp** | 2026.8.19 | The Unlicense | [https://github.com/yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | Direct media extraction engine and metadata parser. |
| **yt-dlp-ejs** | 0.8.0 | MIT | [https://github.com/yt-dlp/yt-dlp-ejs](https://github.com/yt-dlp/yt-dlp-ejs) | JavaScript evaluation integration for yt-dlp. |
| **curl_cffi** | 0.15.0 | MIT | [https://github.com/yifeikong/curl_cffi](https://github.com/yifeikong/curl_cffi) | High-performance HTTP client bindings. |
| **Pillow** | 12.3.0 | HPND / MIT-CMU | [https://github.com/python-pillow/Pillow](https://github.com/python-pillow/Pillow) | Image processing for icon and branding assets. |
| **pywebview** | 6.2.1 | BSD-3-Clause | [https://github.com/r0x0r/pywebview](https://github.com/r0x0r/pywebview) | WebView2 wrapper for isolated browser fallback sessions. |
| **PyInstaller** | 6.21.0 | GPL-2.0-or-later with exception | [https://github.com/pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller) | Build-time binary compiler for standalone Windows executables. |

---

## Bundled Subsystems & Browser Extensions

### 1. uBlock Origin Lite (uBOL)
- **Version**: 2026.812.1211
- **License**: GNU General Public License v3.0 (GPLv3)
- **Upstream**: [https://github.com/uBlockOrigin/uBOL-home](https://github.com/uBlockOrigin/uBOL-home)
- **Archive**: `assets/browser_protection/ubol.zip`
- **Usage**: Content filtering extension loaded into the isolated WebView2 session during Browser Fallback to block malicious scripts, popups, and nuisance ads. The extension's full source and license text are preserved inside the archive.

### 2. puemos/hls-downloader
- **Version**: 5.5.0 (MV3 Chromium Build)
- **License**: MIT
- **Upstream**: [https://github.com/puemos/hls-downloader](https://github.com/puemos/hls-downloader)
- **Provenance**: `third_party/media_observer/puemos-hls-downloader/PROVENANCE-v5.5.0.md`
- **Usage**: Passive media observer extension used exclusively within the isolated browser fallback profile to detect HLS master playlists and DASH streams.

---

## Typography & Fonts

### Space Mono
- **Designer**: Colophon Foundry
- **Copyright**: Copyright 2016 The Space Mono Project Authors
- **License**: SIL Open Font License 1.1 (`assets/fonts/OFL.txt`)

---

## External Media Tools

### FFmpeg & FFprobe
- **License**: LGPL-2.1-or-later / GPL-2.0-or-later
- **Upstream**: [https://ffmpeg.org/](https://ffmpeg.org/)
- **Integration**: VRKA interfaces with FFmpeg and FFprobe as external subprocesses via standard process pipes for stream multiplexing, audio conversion, and precision video trimming. No FFmpeg code is statically or dynamically linked into the core VRKA binaries.
