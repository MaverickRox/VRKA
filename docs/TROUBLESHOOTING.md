# Troubleshooting Guide

---

## Common Issues & Solutions

### A Specific Website Fails to Download
1. Open **Settings** > **Engine Updates**.
2. Check for an update to yt-dlp and apply it.
3. Restart VRKA and retry the download.
4. If direct extraction still fails, allow VRKA to attempt Browser Fallback.

### Audio Transcoding Fails
- Ensure FFmpeg is accessible on your system PATH or located in the application directory.
- Verify that your output drive has sufficient free space for intermediate audio conversion buffers.

### Windows Defender SmartScreen Warning
- Official VRKA release binaries are currently self-signed.
- Click **More info** > **Run anyway** to launch.
- You can verify binary authenticity by comparing the SHA-256 checksum with `SHA256SUMS.txt`.

### WebView2 / Browser Fallback Does Not Open
- Ensure the Microsoft Edge WebView2 Runtime is installed on your Windows system. (Pre-installed on Windows 11 and Windows 10 version 2004+).

---

## Submitting an Issue
If an issue persists, report it on [GitHub Issues](https://github.com/MaverickRox/VRKA/issues/new/choose):
- Include the exact VRKA version (`4.0.0`).
- Provide reproduction steps.
- Paste relevant activity log output with any personal paths, tokens, or cookies removed.
