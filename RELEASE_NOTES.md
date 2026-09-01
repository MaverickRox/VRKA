# VRKA 3.5 Release Notes

Release date: 2026-08-27
Build: VRKA 3.5 / Build 012
Baseline: VRKA 3.0.0 build 011 (frozen, `ba374ba`) + QML overhaul Stages 0-9

## Headline

PySide6 + Qt Quick/QML migration: the 3.0 CustomTkinter UI is replaced by a
PySide6 6.11.2 / Qt Quick Controls application that reuses the frozen
vrka_core / vrka_downloader backend (single ui_queue, single scheduler,
single settings file, no second downloader). All download/queue/history
behaviour is preserved; browser fallback, MediaObserver, and yt-dlp updater
are exposed via OperationalController without duplicating backend logic.

## What changed for 3.5

- QML shell (`MainShell.qml` 1240×820 min 1020×700) with Theme singleton (3.0
  palette, Space Mono, 56 px wolf), four pages via StackLayout.
- PresentationBridge (16 ms coalescing, 250 batch) + TaskListModel /
  HistoryListModel / ActivityLogModel (bounded 1000).
- Download workflow end-to-end via DownloadController → EngineHost →
  Build008TaskAdapter → TaskScheduler → ui_queue → Bridge → QML.
- Queue/History views (TaskDelegate / HistoryDelegate, search, clear, retry,
  remove, Again/Open).
- SettingsState (30 keys, `~/.vrka/settings.json`, migrations, validation) +
  DownloadController effective defaults.
- Activity log presentation (Queue log panel, level ERROR/WARNING/INFO).
- Operational integration (Browser Session state, MediaObserver health, yt-dlp
  check/install/rollback) via OperationalController (worker-threaded).
- DEFAULT MODE persisted Setting (`mode` video|audio) added to Appearance card.
- Packaging: PyInstaller `VRKA-Windows.spec` now bundles PySide6/shiboken6
  + `vrka_qml/qml` tree; entry `vrka_qml_app.py` also serves frozen helper
  submodes (`__vrka_protected_browser__`, diagnostics, yt-dlp CLI) via
  delegation to `vrka_downloader`. Frozen EXE is Qt Quick by default.
- Installer identity: `VRKA 3.5` `3.5.0.12` `VRKA-3.5.0-setup-Windows-x64.exe`,
  same `AppId {{7C6E2F1A-4B3D-4E9A-9F2C-1A8D5E6B3C90}}` → 3.0→3.5 upgrades in place,
  `~/.vrka/settings.json` preserved.
- Licensing: `THIRD_PARTY_NOTICES.md` now lists PySide6 6.11.2 (LGPL-3.0).

## Proven in this release cycle

- Deterministic suite: 412 tests PASS (Stage 2–9) + DPI verification
  (100/125/150/175/200 % logical stability, 67 Stage 8 images).
- Window anchors 1020×700 / 1240×820 / 1600×1000 + light/dark + popup at
  100–200 % logical PASS; maximized captured.
- Smoke `QT_QPA_PLATFORM=offscreen dist/VRKA.exe --smoke` EXIT:0 (frozen QML).
- Settings 3.0 → 3.5 preservation verified (30-key round-trip, `mode` audio).

---

# VRKA 3.0.0 Release Notes

Release date: 2026-08-24
Baseline: VRKA 2.0.0 build010 (`0b036b4`, frozen donor, untouched)

## Headline

Generic browser media detection: a pinned, unmodified third-party observer
extension (puemos/hls-downloader v5.5.0 MV3-Chromium, MIT) now runs as a
passive sensor inside VRKA's protected browser and feeds observed media into
VRKA's existing candidate -> ranking -> validation -> transfer pipeline.

## What changed since build010

- Media Observer subsystem (read-only adapter `vrka_core/media_observer.py`,
  production install hook beside uBOL, observation normalization into the
  existing CandidateStore).
- Observer bundled inside the packaged EXE; installs from the app itself.
- Media Observer updater (official upstream releases over HTTPS, SHA-256
  checksum verification against the official release digest, structural
  manifest validation, atomic artifact replacement, previous version stays
  usable on any failure; no mirrors; never updates VRKA itself).
- Settings -> "Media Observer" card: status/version/health, Check for
  updates, Update, source/license line.
- Version identity: VRKA 3.0.0 / BUILD 011 (app, EXE + installer version
  resources, diagnostics, tests).

## Final pass corrections

- Version identity corrected to exactly **v3.0.0 / BUILD 011**
  (app, EXE + installer version resources, diagnostics, tests; the interim
  BUILD 300 was wrong).
- Fixed queue persistence: Clear Completed / Remove now delete durable task
  records, so cleared entries no longer reappear after restart; genuine
  queued/incomplete tasks are preserved; History unaffected.
- Fixed Browser Fallback deadlock on iframe-player pages (Anikoto,
  JAV.GURU, HentaiHaven embeds): the first capture is now requested
  immediately instead of waiting for DOM-visible playback that cross-origin
  provider players never produce. Live frozen-EXE sessions verified.
- Smoother scrolling (wheel step ~120 px/notch instead of ~20 px; measured
  3x less UI work per distance) and throttled progress persistence.

## Proven in this release cycle

- Deterministic suite: 228/228 OK (incl. 27 focused observer/updater tests
  and the new final-pass regressions).
- HentaiHaven end-to-end: real observation -> adapter -> candidates ->
  validation -> transfer -> playable MP4.
- JAV.GURU / Anikoto: passive observation of provider media proven live;
  full-file transfer blocked only by third-party conditions (see limits).
- Frozen EXE + fresh per-user install: observer installs from the packaged
  app alongside uBOL; benign smoke clean; uninstall clean.
- WebView2 evergreen 151 runtime; uBOL active in every session.

## Known limitations

- F1 (JAV.GURU): javclan's variant playlists interleave a third-party ad
  image segment; ffmpeg rejects the whole playlist and native concat bakes
  the ad in, so completed-file transfer is blocked pending a generic
  segment-policy decision or an upstream fix. Observation itself works.
- F2 (Anikoto): provider (megaplay.buzz) media delivery is session-variable;
  some sessions deliver no media to observe.
- Observer scope: HLS manifests via XHR with HLS MIME types. DASH/MSE
  (YouTube) and some fetch-type/strict-MIME patterns are out of its view;
  those sites are served by VRKA's existing paths.
- No universal coverage claim; no DRM support or circumvention.

## Artifacts

See `SHA256SUMS.txt`. Reproduce builds with `BUILD_AND_TEST.md` +
`VRKA-Windows.spec` (PyInstaller) and `VRKA.iss` (Inno Setup 6).
