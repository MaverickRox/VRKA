# Changelog

All notable changes to VRKA are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [4.0.0] - 2026-09-01 (Build 016)

### Added
- **Qt 6 QML Desktop Interface**: Completely re-engineered frontend using PySide6 and Qt Quick for smooth rendering, responsive layout, and visual fidelity.
- **UPLINK Telemetry Console**: Sidebar console displaying real-time task queue statistics (Queued, Active, Archived, Done).
- **Theme System**: Dedicated Day / Night theme toggle with compact capsule design and animated sliding indicator.
- **Selectable Activity Log**: High-performance multi-line activity log supporting text selection, `Ctrl+A` select-all, and `Ctrl+C` copying.
- **Passive Browser Fallback Subsystem**: Isolated WebView2 execution with uBlock Origin Lite content protection, ranking HLS master manifests, DASH, and direct MP4 streams.
- **Strict-FIFO Scheduler**: Durable single-worker execution model with persistent `tasks.json` storage and state recovery.
- **Managed Runtime Updater**: In-app yt-dlp update manager with SHA-256 validation, atomic rollout, and instant rollback.

### Changed
- Migrated primary desktop interface from legacy widgets to native Qt Quick / QML.
- Polished top-left sidebar branding with 72px VRKA Wolf logo and refined typography.
- Deduplicated retry arguments in yt-dlp command construction (`--impersonate` and extractor arguments).

### Fixed
- Fixed child helper process standard stream restoration in frozen PyInstaller windowed builds (`noconsole=True`).
- Fixed responsive width propagation on high-DPI displays across all window sizes.

---

## [3.0.0] - 2026-08-20

### Added
- Passive media observation engine integration with uBlock Origin Lite.
- End-to-end browser-context transfer protocol for protected streaming sites.
- Deterministic candidate ranking algorithm (`CandidateRanker`).

---

## [2.0.0] - 2026-08-03

### Added
- Initial modular non-Flutter architecture with Python backend.
- Standalone Windows installer and single-file portable builds.
- Contextual Cloudflare challenge handling and cookie import options.
