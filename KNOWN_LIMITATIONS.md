# Known limitations — build008

## Website support changes

VRKA relies on yt-dlp. A website can change without warning and temporarily break extraction. Use the managed updater and include logs when reporting a reproducible problem.

## Browser verification is manual

Build008 can open an on-demand WebView2 browser for pages requiring interaction. Session capture occurs when the browser closes, and the task may then require a manual Retry.

It is not yet the planned same-job automatic Browser Fallback 2.0 workflow.

## Ads, popups, and redirects

Build008 contains containment and conservative blocking, but it is not a complete browser-extension-class blocker. Aggressive pages may still show nuisance content.

## Pre-roll/media ambiguity

The browser observer may see multiple media candidates. Build008 does not have the full planned candidate lifecycle and stabilization system, so an early advertisement or pre-roll can be difficult to distinguish from requested media.

## DRM

VRKA does not circumvent DRM. Encrypted or protected media may be detected as unsupported.

## Authentication and cookies

Cookie/browser access works only when:

- the user is authorized to access the media;
- the browser profile is readable;
- the source permits the request;
- yt-dlp supports the authentication flow.

VRKA does not guarantee access to paid, private, or region-restricted media.

## Quality and formats

Available quality is limited by the source. VRKA does not upscale.

The 60 FPS preference may fall back when a suitable format is unavailable or fails.

FLAC conversion does not restore quality from a lossy source.

## Trimming

Precision trim can require a full download and local FFmpeg processing. Some fast section-download paths are source/format limited.

## Unsigned Windows binaries

The installer and executable are not code-signed. Windows SmartScreen may warn.

## WebView2

Browser verification requires a compatible Microsoft Edge WebView2 Runtime.

## Performance

Startup and UI performance vary with antivirus scanning, one-file extraction, disk speed, Windows scaling, and Python/CustomTkinter behavior.

## No guarantee

No downloader can guarantee permanent support for every site. Report reproducible issues with sanitized logs.
