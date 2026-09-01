# VRKA 3.0 - Phase 10 consolidated report

Date: 2026-08-24. State: commit `0880e4a` on
`feature/vrka-3.0-media-observer` (production code identical to `8d39856`;
Phase 10 added lab tooling only).

## Per-site results (source mode)

| Site | Exact URL | Playback | Puemos obs | Adapter | Candidate | Validation | Transfer | Output file | Streams | uBOL |
|---|---|---|---|---|---|---|---|---|---|---|
| HentaiHaven | hentaihaven.xxx/watch/kare-no-shiranai-himitsu-o-irete-the-animation/episode-1/ | player visible; media flowed | 5 (master + variants) | 5 normalized | 5 HLS candidates ranked | PASS (probe rc=0) | PASS | `%TEMP%\vrka10-hh\v.mp4` | video+audio playable | active |
| JAV.GURU | jav.guru/1035117/snos-334-...-seto-kanna/ | exact page; embed clicked via generic driver | 1 (javclan variant, fresh tokens each run) | 1 normalized | 1 HLS candidate (score 84) | PASS (probe rc=0) | BLOCKED - see failure F1 | partial `.part` only | n/a | active |
| Anikoto | anikoto.cz/watch/sakamoto-days-sfdxz/ep-1 | provider iframe loads (megaplay.buzz) | 0 in 3 fresh sessions (provider dry) | 0 | 0 | not reached | not reached | none | n/a | active |

### Failure F1 (recorded per failure policy - not patched around)
JAV.GURU transfer blocked by UPSTREAM AD-INJECTION: the javclan variant m3u8
interleaves a third-party ad segment
(`p16-ad-site-sign-sg.tiktokcdn.com/...~....image`). ffmpeg's HLS demuxer
refuses the entire playlist ("not in allowed_segment_extensions") which kills
VRKA's section-download path; yt-dlp's native downloader concatenates the ad
segment blindly producing an unplayable mux (verified: PNG header, zero TS
sync bytes). Additionally the full episode at current CDN throughput needs
hours (101 MB in 25 min at `worst`). Evidence:
`lab/media_observer/reports/site-javguru-e2e.json`. In-browser this class is
exactly what uBOL blocks; a generic fix belongs upstream/in pipeline policy,
NOT as site-specific code.

Anikoto note: chain proven end-to-end when the provider delivers
(Phase 6 live observation -> Phase 9 adapter replay produced a real HLS
candidate); today's three sessions received no media from megaplay.buzz.

## Existing-path regressions (MISSAV / YouTube)

- YouTube: PASS - managed backend resolved; direct extraction of the exact V2
  regression URL (`youtube.com/watch?v=bv56jWJg6Lw`) returned rc=0 with
  title/duration/2160p. Observer correctly NOT involved.
- MISSAV: PASS - code-path integrity proven: `git diff 0b036b4..HEAD` on
  `vrka_downloader.py` = exactly the 50-line observer hook, zero site-path
  mentions; MISSAV defect-regression deterministic tests green; Phase 6 live
  session showed 74 s real playback with uBOL active and no ad regression.

## Deterministic gates

- Full suite: **214/214 OK** (`test_build010_*` all + preserved build008
  suites + `test_media_observer` focused suite).
- `git diff --check`: CLEAN.

## Frozen / packaged EXE

- Build: PyInstaller CI command, exit 0.
  `dist\VRKA.exe` SHA-256
  `eb40f8a06248443aa698a236d672dfc60ddb67a16bd9c1404971b11809985658`,
  126,072,631 B.
- `__vrka_diagnostics__`: exit 0; frozen=true; build 010; managed runtime
  validated (2026.08.19); fonts from _MEIPASS. (Initial Errno 22 was a pwsh
  `*>` redirection artifact; real process handles pass - no code change.)
- `__ytdlp_cli__ --version`: 2026.08.19, exit 0.
- Benign protected-browser smoke: PASS - ok=true, capture_seq=1 while window
  open, mov_bbb.mp4 candidate with context headers, uBOL installed+enabled
  (uBlock Origin Lite), popup guard webview2-native, 46 requests/0 dropped,
  DRM false, clean exit.
- JAV.GURU protected-browser probe: PASS - ok=true, exact episode page
  rendered (full title), 47 requests/0 dropped, uBOL enabled, popups/navs 0,
  DRM false, clean exit. candidates=0 expected without an in-window Play
  click.
- Known packaging gap (Phase 14 item): the pinned observer archive is not yet
  bundled into the EXE spec, so the observer installs source-mode-only;
  fail-open verified (browser healthy without it).

## Process hygiene

Zero stale processes after every stage: VRKA/python/yt-dlp/ffmpeg = 0,
lab-owned WebView2 = 0. One orphan pair from an externally killed wrapper was
detected and cleaned before the final frozen probe.

## Commits

- `8d39856` ponytail cleanup (pre-phase state used)
- `0880e4a` Phase 10 lab tooling checkpoint (this phase's only commit)

## Verdict

PHASE 10: PARTIAL PASS.
- All infrastructure/browser/uBOL/frozen/regression gates: PASS.
- HentaiHaven full chain incl. playable output: PASS.
- JAV.GURU: chain proven through validation; transfer BLOCKED by upstream
  ad-injected playlist (F1) - requires a generic pipeline-level decision
  (segment policy / upstream report), not site-specific code.
- Anikoto: blocked by third-party provider availability today (chain proven
  in Phases 6+9); retry on a delivering session.

Not claimed: universal coverage; DRM; guarantee that any given provider
session delivers media.
