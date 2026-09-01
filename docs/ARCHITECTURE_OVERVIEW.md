# Architecture Overview

VRKA is a modular desktop media downloader built with a native **Qt 6 QML** interface and a Python backend.

---

## System Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                       Qt 6 QML Shell                        │
│   (MainShell.qml, Theme.qml, Pages, Components, Models)     │
└──────────────────────────────┬──────────────────────────────┘
                               │ PySide6 Bridge & Signals
┌──────────────────────────────▼──────────────────────────────┐
│                    QML Application Layer                    │
│   (vrka_qml_app.py, app.py, bridge.py, controllers)         │
└──────────────────────────────┬──────────────────────────────┘
                               │ Thread-Safe Task Protocol
┌──────────────────────────────▼──────────────────────────────┐
│                     Domain & Core Engine                    │
│  (TaskScheduler, DownloadStateMachine, BrowserFallback)     │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
┌──────────────▼──────────────┐┌──────────────▼───────────────┐
│     yt-dlp Engine Core      ││  Isolated WebView2 Session   │
│ (Extraction, Muxing, Probing)││ (uBOL Protection & Observer) │
└─────────────────────────────┘└──────────────────────────────┘
```

---

## Component Breakdown

### 1. Presentation Layer (`vrka_qml/`)
- **Qt Quick / QML**: Renders a fluid, hardware-accelerated user interface supporting dynamic window scaling, high-DPI displays, and theme switching.
- **Bridge (`bridge.py`)**: Connects QML UI signals to backend controllers through thread-safe Qt slots and properties.
- **Activity Log Model (`activity_log_model.py`)**: Ring buffer model providing smooth real-time log rendering with low memory overhead.

### 2. Task Orchestration (`vrka_core/scheduler.py`, `vrka_core/tasks.py`)
- **Strict FIFO Queue**: Executes downloads sequentially to maximize throughput, prevent connection contention, and ensure predictable execution.
- **Durable Persistence**: Maintains task records and states atomically in `%USERPROFILE%\.vrka\tasks.json`.

### 3. Media Extraction Engine (`vrka_downloader.py`)
- **Direct Extraction**: Manages yt-dlp subprocess execution with structured argument arrays, progress tracking, and format selection.
- **Post-Processing**: Coordinates FFmpeg/FFprobe operations for muxing, audio transcoding, and precision video trimming.

### 4. Passive Browser Fallback (`vrka_core/browser_fallback.py`)
- **Protected Environment**: Launches an on-demand WebView2 instance with uBlock Origin Lite to filter unwanted scripts, popups, and advertisements.
- **Stream Observation**: Passively observes network requests to identify HLS master playlists, DASH manifests, and direct MP4 streams.
- **Automated Handoff**: Passes captured media candidate URLs directly to the downloader backend without manual user intervention.

---

## Security Principles
- **No Inbound Network Ports**: VRKA runs purely as a client application.
- **No Background Telemetry**: Zero analytics, tracking beacons, or remote telemetry.
- **Redacted Diagnostics**: Sensitive session tokens, cookies, and URLs are stripped before display in logs.
- **Process Cleanup**: Helper processes are registered in an ownership registry and terminated cleanly on task cancellation or app exit.
