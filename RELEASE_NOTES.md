# VRKA Release Notes

## VRKA 4.0.1 (Build 017)

**Version**: 4.0.1 (Build 017)
**Release Date**: 2026-09-01
**Target Platform**: Windows 10/11 x64
**License**: GPL-3.0-or-later

### Highlights in 4.0.1
- **UPLINK Status Reactive Synchronization**: Resolved an issue where the compact sidebar UPLINK status light and text remained stuck on `UPLINK QUEUED` (yellow) after task completion. The presentation bridge now connects model `dataChanged` and `layoutChanged` signals directly to reactive telemetry properties.
- **Dynamic Version Headers**: Window title and Settings version badges dynamically reflect active build metadata (`4.0.1 Build 017`).
- **Regression Test Coverage**: Added dedicated UPLINK state machine tests in `tests/test_uplink_state.py` validating queue transitions, task completions, failures, cancellations, and batch drains.
- **Documentation & Screenshot Refresh**: Updated all product screenshots with real native captures demonstrating the verified `UPLINK LIVE` (green) idle state.

---

## VRKA 4.0.0 (Build 016)

## What is VRKA?

VRKA is a lightweight, resilient desktop media downloader designed for seamless video and audio extraction. It pairs a high-performance **Qt 6 QML** interface with an automated, passive **Browser Fallback** engine powered by uBlock Origin Lite.

---

## Major Features & Improvements in 4.0.0

### Native Qt 6 QML Desktop Interface
- Fully responsive, hardware-accelerated interface built with PySide6 and Qt Quick.
- Adaptive layouts supporting dynamic window resizing from compact 1020x700 up to 4K displays.
- High-DPI font scaling and crisp iconography.

### UPLINK Telemetry Console
- Sidebar console displaying live task execution status, queue metrics, and system activity.

### Refined Day / Night Mode
- Compact pill theme toggle with fluid slide transitions and unified semantic palette.

### Selectable Multi-Line Activity Log
- Real-time diagnostic console supporting full text selection, `Ctrl+A` select all, and `Ctrl+C` copying.

### Passive Browser Fallback Subsystem
- Content-filtered WebView2 session for observing media streams (HLS master manifests, DASH, direct MP4).
- Automatic handoff to yt-dlp for reliable multi-stream downloading.

### Strict-FIFO Task Scheduler
- Single-worker queue ensuring deterministic download order.
- Durable persistence in `tasks.json` with state recovery across application restarts.

### Managed yt-dlp Runtime Updater
- Built-in runtime manager in `%LOCALAPPDATA%\VRKA\runtime` supporting official updates with SHA-256 validation and one-click rollback.

### Audio Extraction & Transcoding
- Supports MP3 (320, 256, 192, 128 kbps), WAV (uncompressed PCM), and FLAC audio transcoding with accurate codec metadata.

---

## Privacy & Security
- Zero telemetry and no background services.
- Sensitive cookies and tokens are automatically redacted from activity logs.
- Strict compliance with DRM non-circumvention policies.
