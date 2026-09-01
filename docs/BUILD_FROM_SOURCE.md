# Build VRKA build016 from source

These instructions target Windows 10/11 x64.

## Requirements

- Python 3.12 or 3.13
- Git
- FFmpeg and FFprobe
- Deno for current yt-dlp YouTube challenge solving
- PyInstaller
- Inno Setup for the installer

Use the exact versions recorded for the release when reproducing build016.

## Create an environment

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

If the source snapshot has no complete lock file, reconstruct the exact release environment from the preserved validation/dependency inventory before claiming a reproducible build.

## Run from source

Ensure FFmpeg/FFprobe are on PATH or in the expected `ffmpeg_bin` folder.

```powershell
python vrka_downloader.py
```

## Tests

For the build016 source layout:

```powershell
python -m unittest -v test_vrka.py test_vrka_20.py test_release_rework.py
```

If the repository uses a wrapper script, prefer the documented wrapper. Do not silently skip failing tests.

## Build the Windows executable

Prefer the preserved PyInstaller spec:

```powershell
pyinstaller --clean --noconfirm VRKA-Windows.spec
```

If the exact build016 spec has a different filename, use that file and update this document.

## Build the installer

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" VRKA.iss
```

Verify the exact installer output name and metadata.

## Release verification

- run from a clean folder;
- test installer and portable modes;
- verify icon/title/version;
- run the media acceptance matrix;
- test Cancel and shutdown;
- inspect process cleanup;
- run a secret scan;
- generate SHA-256 hashes;
- record exact dependency versions and FFmpeg configuration.

See `docs/RELEASE_PROCESS.md`.
