"""
VRKA 4.0 Synchronous Build, Visual Capture, Test, and Packaging Pipeline.
Executes all steps synchronously, verifies exit codes, and ensures 100% completion.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)

PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
PYINSTALLER_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
ISCC_PATH = Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe")
if not ISCC_PATH.exists():
    ISCC_PATH = Path("C:/Program Files/Inno Setup 6/ISCC.exe")

OUTPUT_DIR = PROJECT_ROOT / "outputs" / "VRKA-4.0.0-build016-release"
PORTABLE_DIR = PROJECT_ROOT / "VRKA-portable"

def run_step(title, cmd, cwd=PROJECT_ROOT):
    print(f"\n======================================================")
    print(f">> {title}")
    print(f"======================================================")
    t0 = time.time()
    res = subprocess.run(cmd, cwd=cwd)
    dt = time.time() - t0
    if res.returncode != 0:
        print(f"\n[ERROR] Step failed with exit code {res.returncode} after {dt:.1f}s")
        sys.exit(res.returncode)
    print(f"[OK] Completed in {dt:.1f}s")

def main():
    print("======================================================")
    print("VRKA 4.0 — AUTONOMOUS RELEASE PIPELINE")
    print("======================================================")
    print(f"Project root: {PROJECT_ROOT}")

    # 1. PyInstaller standalone build
    run_step(
        "Step 1: Building standalone binary with PyInstaller",
        [str(PYINSTALLER_EXE), "--noconfirm", "--clean", "VRKA-Windows.spec"]
    )

    dist_exe = PROJECT_ROOT / "dist" / "VRKA.exe"
    if not dist_exe.exists():
        print(f"[FATAL] {dist_exe} was not produced!")
        sys.exit(1)
    print(f"Verified {dist_exe.name}: {dist_exe.stat().st_size:,} bytes")

    # 2. Update VRKA-portable directory
    print("\n======================================================")
    print(">> Step 2: Staging VRKA-portable distribution")
    print("======================================================")
    PORTABLE_DIR.mkdir(parents=True, exist_ok=True)
    portable_exe = PORTABLE_DIR / "VRKA.exe"
    shutil.copy2(dist_exe, portable_exe)

    ffmpeg_dst_dir = PORTABLE_DIR / "ffmpeg_bin"
    ffmpeg_dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT_ROOT / "ffmpeg_bin" / "ffmpeg.exe", ffmpeg_dst_dir / "ffmpeg.exe")
    shutil.copy2(PROJECT_ROOT / "ffmpeg_bin" / "ffprobe.exe", ffmpeg_dst_dir / "ffprobe.exe")
    shutil.copy2(PROJECT_ROOT / "THIRD_PARTY_NOTICES.md", PORTABLE_DIR / "THIRD_PARTY_NOTICES.md")

    readme_path = PORTABLE_DIR / "README.txt"
    readme_path.write_text(
        "VRKA 4.0.0 — portable Windows x64\n\n"
        "Run VRKA.exe directly. Python, pip and administrator access are not required.\n"
        "Verify against SHA256SUMS.txt before running.\n",
        encoding="utf-8"
    )

    # Clean up old embedded python folder if present
    old_py = PORTABLE_DIR / "python"
    if old_py.exists():
        shutil.rmtree(old_py, ignore_errors=True)
    print(f"[OK] VRKA-portable updated ({portable_exe.stat().st_size:,} bytes)")

    # 3. Deterministic QML State Captures & Multi-Resolution Sweep
    run_step(
        "Step 3: Generating deterministic QML captures & forensic sweep",
        [str(PYTHON_EXE), "tools/capture_qml_states.py"]
    )

    # 4. Full Unit Test Suite (441 tests)
    run_step(
        "Step 4: Running full regression test suite",
        [str(PYTHON_EXE), "-m", "unittest", "discover", "-p", "test_*.py"]
    )

    # 5. Full Packaging Pipeline (Portable Zip, Inno Setup Installer, Clean Source Archive, SHA256SUMS)
    run_step(
        "Step 5: Packaging release artifacts",
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", "tools/package_vrka40_release.ps1"]
    )

    # 6. Final verification of all output artifacts
    print("\n======================================================")
    print(">> Step 6: Verifying final release directory")
    print("======================================================")
    if not OUTPUT_DIR.exists():
        print(f"[FATAL] Output directory {OUTPUT_DIR} does not exist!")
        sys.exit(1)

    expected_files = [
        "VRKA-4.0.0-build016-portable-Windows-x64.exe",
        "VRKA-4.0.0-build016-portable-Windows-x64.zip",
        "VRKA-4.0.0-build016-setup-Windows-x64.exe",
        "VRKA-4.0.0-build016-complete-source.zip",
        "SHA256SUMS.txt"
    ]

    all_ok = True
    for fname in expected_files:
        fpath = OUTPUT_DIR / fname
        if not fpath.exists():
            print(f"  [MISSING] {fname}")
            all_ok = False
        else:
            print(f"  [PRESENT] {fname:<48} {fpath.stat().st_size:>12,} bytes")

    if not all_ok:
        print("[FATAL] One or more release artifacts are missing!")
        sys.exit(1)

    print("\n======================================================")
    print("RELEASE VERIFICATION COMPLETED WITH 100% SUCCESS!")
    print("======================================================")

if __name__ == "__main__":
    main()
