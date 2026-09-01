"""VRKA 3.5 (Build 012) Qt Quick entry point.

Presentation layer only. Launch:

    python vrka_qml_app.py            # normal launch
    python vrka_qml_app.py --smoke    # headless-safe bootstrap check, exit 0 on success

When frozen via PyInstaller, this executable also serves the internal
CLI helpers (protected browser, browser verification, diagnostics, yt-dlp
CLI) via the same binary (see build_self_invocation() in vrka_downloader.py).
Those submodes are handled here by delegating to vrka_downloader helpers
before the QML application starts, preserving the single-queue/single-scheduler
architecture and the frozen backend contract.
"""

import sys

# Internal CLI submodes (frozen helper invocations) must be dispatched
# before QApplication is created. Reuse the backend implementations verbatim.
if len(sys.argv) > 1 and sys.argv[1] in ("__vrka_protected_browser__", "__vrka_browser__",
                                          "__vrka_diagnostics__", "__ytdlp_cli__"):
    import vrka_downloader as _vd
    # Mirror vrka_downloader.py startup sequence for these modes
    _vd.configure_bundled_runtime_path()
    _vd.configure_windows_app_identity()
    _vd.restore_frozen_cli_streams()
    if len(sys.argv) > 3 and sys.argv[1] == "__vrka_protected_browser__":
        sys.exit(_vd.run_protected_browser_helper(sys.argv[2], sys.argv[3]))
    if len(sys.argv) > 3 and sys.argv[1] == "__vrka_browser__":
        sys.exit(_vd.run_browser_verification_helper(sys.argv[2], sys.argv[3]))
    if len(sys.argv) > 1 and sys.argv[1] == "__vrka_diagnostics__":
        import json
        print(json.dumps({
            "name": _vd.APP_NAME,
            "version": _vd.APP_VERSION,
            "build": _vd.APP_BUILD,
            "display_version": _vd.APP_DISPLAY_VERSION,
            "author": _vd.APP_AUTHOR,
            "copyright": _vd.APP_COPYRIGHT,
            "ytdlp": _vd.active_ytdlp_summary(),
            "frozen": _vd.is_frozen(),
            "font": _vd.get_font_registration_report(),
        }))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "__ytdlp_cli__":
        cli_args = list(sys.argv[2:])
        if not _vd._has_cli_option(cli_args, "--ffmpeg-location"):
            ffmpeg_dir = _vd.resolve_ffmpeg_location()
            if not ffmpeg_dir:
                try:
                    ffmpeg_dir = _vd.ensure_ffmpeg_runtime()
                except Exception:
                    ffmpeg_dir = None
            if ffmpeg_dir:
                cli_args = ["--ffmpeg-location", ffmpeg_dir] + cli_args
        try:
            import yt_dlp
            sys.exit(yt_dlp.main(cli_args))
        except OSError:
            # Parent handed broken stdio (GUI subsystem exe); retry with devnull streams.
            import os as _os
            _vd._DISCARDED_STD_STREAMS.append(getattr(sys, "stdout", None))
            _vd._DISCARDED_STD_STREAMS.append(getattr(sys, "stderr", None))
            try:
                for _name, _mode in (("stdout", "w"), ("stderr", "w")):
                    setattr(sys, _name, open(_os.devnull, _mode, buffering=1, encoding="utf-8", errors="replace"))
                sys.__stdout__ = sys.stdout
                sys.__stderr__ = sys.stderr
            except Exception:
                pass
            import yt_dlp as _yt
            sys.exit(_yt.main(cli_args))

from vrka_qml.app import main

if __name__ == "__main__":
    sys.exit(main())
