# VRKA user guide — build016

## Download screen

### Source and destination

- Paste a media or playlist URL.
- Select the output folder.
- Use only URLs you are authorized to access.

### Media profile

Video mode downloads playable video and audio when the source provides them.

Audio mode supports configured MP3 bitrates, WAV, and FLAC.

Resolution values are maximum preferences, not upscaling requests.

The 60 FPS option is a preference and can fall back when unavailable.

### Playlist range

Enable playlist mode only for a playlist URL. Use inclusive start/end values. Keep tests small when reporting issues.

### Subtitles and captions

Global subtitle preferences are configured in Settings. Per-task options determine whether matching subtitles/captions are included for the current download.

### Precision trim

Enter start/end times in the format shown by the application. Blank values keep the full media.

High-quality sources may need to download fully before local FFmpeg trimming.

## Queue

Queue displays the active task, progress, speed, ETA, stage, and Activity Stream.

- Retry repeats a failed task with its preserved options.
- Cancel requests task cancellation and process cleanup.
- Clear Log removes visible log text, not downloaded files.
- Review sanitized logs before sharing.

Build008 processes downloads sequentially for reliability.

## History

History contains local completed/archived records. It is not a cloud account and does not scan the entire drive.

Use available actions to open output, repeat a task, or clear records.

## Settings

### yt-dlp runtime

VRKA can check Stable or Nightly yt-dlp releases, validate the downloaded runtime, activate it atomically, roll back, or restore the bundled version.

Do not interrupt runtime replacement.

### Cookies and sign-in

Use browser cookies or a cookie file only for accounts/content you are authorized to access.

Never share cookie files in issues.

### Subtitle defaults

Set preferred language patterns and automatic-caption behavior.

### Browser verification

Use the on-demand browser when direct extraction asks for interaction. Complete only normal permitted interaction. Build008 may require closing the browser and pressing Retry.

## Troubleshooting

See `TROUBLESHOOTING.md` and `KNOWN_LIMITATIONS.md`.
