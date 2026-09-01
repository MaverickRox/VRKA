# Architecture overview — build016

VRKA build016 is a local Python desktop application.

## Main layers

### User interface

Qt 6 QML / PySide6/Tkinter runs on the main UI thread. Background work communicates with the UI through a thread-safe event/update queue.

### Task orchestration

A sequential event-driven worker processes one task at a time. Each task snapshots its URL and download options.

### Download engine

yt-dlp handles extraction and downloads. FFmpeg/FFprobe handle merge, remux, conversion, probing, subtitle embedding, and local trimming.

### Browser verification

pywebview uses Microsoft Edge WebView2 on Windows. It is created on demand and uses temporary session storage. Build008 session capture feeds a later Retry rather than a fully automatic same-job continuation.

### Persistence

Settings and History use local JSON files written atomically. Runtime/updater data and browser sessions use local VRKA directories.

### Updates

The managed yt-dlp updater obtains official release information, verifies hashes and executable health, activates updates atomically, preserves rollback, and can restore the bundled runtime.

## Security boundaries

- no network server/listening port;
- no VRKA telemetry service;
- structured process arguments;
- sensitive log redaction;
- bounded staging/history/logs;
- tracked helper-process cleanup;
- no DRM circumvention.

For user-facing limitations, see `KNOWN_LIMITATIONS.md`.
