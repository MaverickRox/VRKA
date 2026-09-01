#!/usr/bin/env bash
# Run this only on an Apple Silicon Mac. It produces VRKA-2.0.0-build010-macOS-arm64.dmg.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "[1/6] Checking Apple Silicon..."
if [[ "$(uname -m)" != "arm64" ]]; then
    echo "This package targets Apple Silicon (arm64). Current machine: $(uname -m)"
    exit 1
fi

echo "[2/6] Preparing an isolated Python environment..."
if [[ ! -x ".venv-macos/bin/python" ]]; then
    python3 -m venv .venv-macos
fi
PYTHON="$PROJECT_DIR/.venv-macos/bin/python"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt

echo "[3/6] Running regression checks..."
"$PYTHON" -m py_compile vrka_downloader.py test_vrka.py test_vrka_20.py tools/generate_brand_assets.py
"$PYTHON" test_vrka.py
"$PYTHON" test_vrka_20.py

echo "[4/6] Preparing FFmpeg and Deno..."
if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required. Install it from https://brew.sh and run this script again."
    exit 1
fi
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
brew list deno >/dev/null 2>&1 || brew install deno
mkdir -p "$PROJECT_DIR/ffmpeg_bin" "$PROJECT_DIR/deno_bin"
cp "$(command -v ffmpeg)" "$PROJECT_DIR/ffmpeg_bin/ffmpeg"
cp "$(command -v ffprobe)" "$PROJECT_DIR/ffmpeg_bin/ffprobe"
cp "$(command -v deno)" "$PROJECT_DIR/deno_bin/deno"

echo "[5/6] Building VRKA.app..."
"$PYTHON" -m PyInstaller --noconfirm --clean VRKA-macOS.spec
if [[ ! -d "$PROJECT_DIR/dist/VRKA.app" ]]; then
    echo "PyInstaller did not create dist/VRKA.app."
    exit 1
fi

echo "[6/6] Creating the drag-to-Applications disk image..."
STAGING_DIR="$PROJECT_DIR/dmg_staging"
DMG_PATH="$PROJECT_DIR/VRKA-2.0.0-build010-macOS-arm64.dmg"
rm -rf "$STAGING_DIR"
rm -f "$DMG_PATH"
mkdir -p "$STAGING_DIR"
ditto "$PROJECT_DIR/dist/VRKA.app" "$STAGING_DIR/VRKA.app"
ln -s /Applications "$STAGING_DIR/Applications"
hdiutil create -volname "VRKA" -srcfolder "$STAGING_DIR" -ov -format UDZO "$DMG_PATH"
rm -rf "$STAGING_DIR"

echo "Done: $DMG_PATH"
echo "This build is unsigned. On first launch, Control-click VRKA and choose Open."
