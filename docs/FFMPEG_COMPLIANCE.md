# FFmpeg Compliance & Integration

VRKA interfaces with FFmpeg and FFprobe as external subprocess tools for stream remuxing, audio conversion, and precision trimming.

---

## Distribution & Licensing

- **License**: FFmpeg is licensed under the LGPL-2.1-or-later (or GPL-2.0-or-later depending on build configuration).
- **Subprocess Isolation**: VRKA communicates with FFmpeg via standard command-line arguments and standard I/O streams. VRKA does not statically or dynamically link FFmpeg code into its core binary.
- **Source Availability**: Upstream FFmpeg source code and official build specifications are available from the [FFmpeg official website](https://ffmpeg.org/).

Users are free to substitute their own compatible FFmpeg binaries on their system PATH.
