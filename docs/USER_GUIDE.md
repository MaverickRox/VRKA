# VRKA User Guide

---

## Main Interface & Navigation

VRKA features four primary sections accessible via the left sidebar:

### 1. Download
- **URL Input**: Paste any valid media, playlist, or webpage URL.
- **Output Folder**: Select your preferred destination folder for saved files.
- **Format Options**:
  - **Video**: Downloads optimal video stream combined with best audio.
  - **Audio Only**: Extracts and converts audio to MP3 (320, 256, 192, 128 kbps), WAV (uncompressed PCM), or FLAC.
- **Advanced Options**:
  - **Playlist Range**: Download specific ranges from playlists by specifying start and end indices.
  - **Subtitles**: Include embedded or external subtitles matching your language preferences.
  - **Precision Trim**: Trim media directly during download using start and end timestamps.

### 2. Queue
- Displays all currently queued and downloading tasks.
- Shows real-time progress, speed, ETA, and download phase.
- Allows pausing, resuming, retrying failed tasks, or cancelling active jobs.

### 3. History
- Searchable archive of all completed downloads.
- Quick actions to open the output file in your default player or locate it in Windows File Explorer.

### 4. Settings
- **Engine Updates**: Check for official yt-dlp updates, verify signatures, or roll back to the bundled release.
- **Network & Proxy**: Configure custom HTTP/SOCKS5 proxy settings for downloads.
- **Cookie Import**: Import authentication cookies from supported browsers or custom cookie files.
- **Theme**: Toggle between Light and Dark interface modes.

---

## Browser Fallback

When downloading from complex streaming websites where direct extractors are blocked by bot-detection or dynamic scripts:
1. VRKA automatically initiates an isolated **Browser Fallback** session using WebView2.
2. The page loads in a content-protected window with ad and popup filtering enabled.
3. Once playback starts, VRKA detects the underlying stream manifest (HLS, DASH, or direct MP4) and hands it off to the download engine.
4. The browser window closes automatically and download proceeds in the queue.
