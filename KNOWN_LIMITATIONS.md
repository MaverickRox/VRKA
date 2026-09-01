# Known Limitations

This document outlines the current technical boundaries and operational characteristics of VRKA.

---

## Dynamic Website Changes
VRKA utilizes [yt-dlp](https://github.com/yt-dlp/yt-dlp) for direct media extraction. When streaming platforms update their player scripts, APIs, or bot-detection systems, extraction may temporarily fail until an updated yt-dlp release is deployed. Use the in-app managed updater in Settings to ensure the latest extraction definitions are active.

## Browser-Assisted Media Detection
When direct extraction encounters protected pages, VRKA launches an isolated WebView2 session with uBlock Origin Lite content filtering to observe network traffic. While this successfully detects HLS master manifests, DASH streams, and direct MP4 URLs on most sites, complex interactive players may require brief manual playback initiation by the user before streams become detectable.

## Digital Rights Management (DRM)
VRKA **does not** circumvent or decrypt DRM-protected streams (such as Widevine, FairPlay, or PlayReady). If encrypted media segments or license challenge handshakes are detected, the download terminates immediately with an explicit explanation.

## Quality & Source Availability
- **No Artificial Upscaling**: VRKA only downloads video and audio streams made available by the hosting provider.
- **High Frame Rates**: 60 FPS options are preferred when provided by the server; otherwise, the engine falls back to standard frame rates.
- **Audio Conversion**: Transcoding audio to lossless formats (such as FLAC) preserves existing fidelity but cannot recover frequency data lost in lossy source streams.

## Platform Support
VRKA is officially built and packaged for **Windows 10 and Windows 11 (x64)**. Embedded browser-assisted capture requires the Microsoft Edge WebView2 Runtime, which is pre-installed on modern Windows systems.

## Unsigned Windows Binaries
Official release binaries are currently self-published and not signed with an EV Code Signing Certificate. Windows Defender SmartScreen may display an unrecognized publisher warning upon first launch. Verify the SHA-256 hash against `SHA256SUMS.txt` before execution.
