# Troubleshooting

## A site stopped working

1. Open Settings.
2. Check/update yt-dlp.
3. Restart VRKA.
4. Retry a permitted public URL.
5. Check whether yt-dlp currently supports the site.

Website changes are common and do not necessarily indicate a VRKA regression.

## Video mode produced an unexpected result

Confirm:

- Video mode is selected;
- quality is available at the source;
- FFmpeg is present in the packaged runtime;
- the log shows video and audio formats/merge;
- the media player supports the selected codecs.

## 60 FPS failed

Disable the preference and retry. Some high-bitrate formats expire or fail even when listed.

## Trim delivered lower quality or failed

Try without trim. Some source-side section downloads are limited; VRKA may need a full download and local trim.

## Subtitles are missing

- confirm the source actually provides subtitles/captions;
- check the language pattern;
- check automatic-caption setting;
- confirm custom-command mode is not overriding normal options;
- inspect the Activity Stream.

## Browser verification did not complete

Build008 may require:

- closing the verification window after interaction;
- returning to Queue;
- selecting Retry.

Do not expect automatic same-job continuation in this release.

## App disappears on startup

Possible causes:

- antivirus quarantine;
- incomplete portable extraction;
- missing/corrupt packaged dependency;
- stale or damaged local runtime;
- unsupported Windows installation.

Re-download from the official release and verify the SHA-256 hash.

## SmartScreen warning

Build008 is unsigned. Verify the release source and SHA-256 hash before deciding whether to run it.

## Reporting

Use the bug-report form and attach sanitized logs only.
