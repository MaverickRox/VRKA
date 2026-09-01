# Project Roadmap

This document outlines planned improvements and future directions for VRKA.

---

## Current Release (v4.0.0)
- Native Qt 6 QML desktop interface with Day/Night theme toggle.
- Single-worker FIFO task queue with persistent `tasks.json` storage and state recovery.
- Automated passive Browser Fallback powered by uBlock Origin Lite content protection.
- In-app yt-dlp runtime manager with SHA-256 validation and instant rollback.
- High-fidelity audio extraction supporting MP3, WAV, and FLAC formats.

---

## Planned Enhancements

### User Interface & Experience
- Custom output template builder for flexible naming patterns.
- Expanded localization and multi-language interface translations.
- Enhanced speed graphing and visual bandwidth throttling controls.

### Downloader & Engine
- Fine-grained segment connection limits for high-bandwidth connections.
- Extended subtitle styling options and multi-track audio stream extraction.
- Automatic retry tuning with exponential backoff on intermittent network drops.

### Packaging & Infrastructure
- Automated reproducible build verification.
- Code signing integration as project funding permits.
