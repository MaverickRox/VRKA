# FFmpeg Compliance & Integration

VRKA interfaces with FFmpeg and FFprobe as external subprocess tools for stream remuxing, format merging, audio conversion, and precision trimming.

---

## Managed Runtime Architecture

To preserve a lightweight, high-performance application executable (~92.5 MB), FFmpeg and FFprobe binaries are managed separately in the user's local runtime directory:

`%LOCALAPPDATA%\VRKA\runtime\`

- **Automatic Provisioning**: On first required media operation, VRKA automatically provisions official, version-pinned static builds from [GyanD/codexffmpeg](https://github.com/GyanD/codexffmpeg/releases/tag/9.0.1) (release `9.0.1-essentials_build`).
- **Cryptographic Integrity Verification**: Every downloaded runtime archive is verified against an immutable, hardcoded SHA-256 checksum before extraction and activation.
- **Offline Operation**: Once provisioned, the local runtime is stored and reused for all subsequent operations without requiring further network access.
- **Subprocess Isolation**: VRKA communicates with FFmpeg strictly via standard command-line flags (`--ffmpeg-location`) and process pipes. No FFmpeg code is statically or dynamically linked into the core VRKA binary.
- **Local Overrides**: Users may place compatible `ffmpeg.exe` and `ffprobe.exe` binaries into an `ffmpeg_bin/` folder beside `VRKA.exe` to override automatic provisioning.

---

## Distribution & Licensing

- **License**: FFmpeg is licensed under the LGPL-2.1-or-later / GPL-2.0-or-later (depending on codec configuration).
- **Source Availability**: Upstream FFmpeg source code and build recipes are available from the [official FFmpeg project](https://ffmpeg.org/).
