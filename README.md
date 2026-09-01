# VRKA

**Lightweight, resilient desktop media downloader for Windows.**

[![Release](https://img.shields.io/github/v/release/MaverickRox/VRKA?include_prereleases&style=flat-square)](https://github.com/MaverickRox/VRKA/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-blue?style=flat-square)](https://github.com/MaverickRox/VRKA/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-green?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue?style=flat-square)](https://www.python.org/)

[Download Latest Release](https://github.com/MaverickRox/VRKA/releases/latest) • [Report Issue](https://github.com/MaverickRox/VRKA/issues/new/choose) • [Documentation](docs/)

---

## Overview

VRKA is an open-source, desktop media downloader designed for single-stream and batch downloads on Windows 10 and 11 (x64). Built with a native **Qt 6 QML** interface and a Python backend, VRKA combines direct extraction via [yt-dlp](https://github.com/yt-dlp/yt-dlp) with an automated, passive **Browser Fallback** subsystem powered by uBlock Origin Lite.

VRKA operates strictly on user demand with **zero telemetry**, no background services, redacted secret handling, and full respect for DRM.

---

## Key Features

- **Modern Qt 6 QML Interface**: High-performance, responsive UI featuring real-time download status, multi-line selectable activity logs (with `Ctrl+A` / `Ctrl+C` support), and an animated compact Day/Night theme toggle.
- **Strict FIFO Task Queue & Persistence**: One immutable task record per job, ensuring deterministic ordering, atomic state machine transitions, and state recovery upon application restart.
- **Automated Browser Fallback**: When direct extractor attempts encounter Cloudflare or complex media players, VRKA seamlessly launches an isolated, content-filtered WebView2 session to observe media streams (HLS master manifests, DASH, direct MP4) and hands them off to the downloader.
- **Managed yt-dlp Runtime Updater**: Built-in runtime manager in `%LOCALAPPDATA%\VRKA\runtime` that can check for official yt-dlp updates, verify SHA-256 signatures, and fall back to the bundled frozen engine with one-click rollback.
- **Precise Audio Extraction**: Supports MP3 (320, 256, 192, 128 kbps), WAV (uncompressed), and FLAC audio transcoding with accurate codec metadata.
- **Privacy & Security by Design**: No telemetry, no persistent tracking, automatic redaction of cookies and session tokens in logs, and DRM non-circumvention by design.

---

## Quick Start (Windows)

### Installation Options

1. **Installer (Recommended)**:
   - Download `VRKA-4.0.0-build016-setup-Windows-x64.exe` from [Releases](https://github.com/MaverickRox/VRKA/releases/latest).
   - Run the setup wizard to install VRKA and create a Start Menu / Desktop shortcut.

2. **Portable Version**:
   - Download `VRKA-4.0.0-build016-portable-Windows-x64.zip` or the standalone `VRKA-4.0.0-build016-portable-Windows-x64.exe`.
   - Extract and run `VRKA.exe` directly. No administrative rights or Python installation required.

### Verifying Checksums

Verify the integrity of downloaded binaries against `SHA256SUMS.txt`:
```powershell
Get-FileHash .\VRKA-4.0.0-build016-setup-Windows-x64.exe -Algorithm SHA256
```

---

## Building from Source

### Prerequisites

- Windows 10/11 x64
- Python 3.10, 3.11, or 3.12
- Git

### Setup & Execution

```powershell
# Clone the repository
git clone https://github.com/MaverickRox/VRKA.git
cd VRKA

# Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install required dependencies
pip install -r requirements.txt

# Launch VRKA
python vrka_qml_app.py
```

### Running Tests

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

### Compiling Standalone Executable

```powershell
pip install pyinstaller
pyinstaller VRKA-Windows.spec
```

---

## Project Structure

```text
VRKA/
├── assets/                 # Wolf branding icons, Space Mono fonts, uBOL extension
├── docs/                   # Architecture, build, compliance, and user guides
├── tests/                  # Full automated regression & unit test suite
├── tools/                  # Build pipeline, packaging scripts, test runners
├── vrka_core/              # Core domain: scheduler, state machine, browser fallback
├── vrka_qml/               # Qt 6 QML interface, models, and controllers
├── vrka_downloader.py      # Authoritative backend engine & helper dispatch
├── vrka_qml_app.py         # Top-level desktop entrypoint
├── requirements.txt        # Python dependency manifest
├── VRKA-Windows.spec       # PyInstaller packaging specification
├── VRKA-4.0.iss            # Inno Setup installer script
└── LICENSE                 # GNU General Public License v3.0
```

---

## Security & Responsible Use

VRKA is intended for downloading user-authorized content, personal archiving, and accessing openly available media.
- **DRM Policy**: VRKA does not bypass Widevine, FairPlay, PlayReady, or any Digital Rights Management systems. If DRM is detected on a media candidate, the task terminates immediately with a clear explanation.
- **Privacy**: No telemetry, analytics, or remote beacons are embedded. Cookie values and access tokens are never logged.
- **Reporting Vulnerabilities**: See [SECURITY.md](SECURITY.md) for vulnerability disclosure procedures.

---

## License

VRKA is licensed under the [GNU General Public License v3.0 or later (GPL-3.0-or-later)](LICENSE).
Third-party component notices and licenses are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
