"""
VRKA 4.0 Standalone Build and Packaging Pipeline.
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
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(sys.executable)

PYINSTALLER_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
if not PYINSTALLER_EXE.exists():
    PYINSTALLER_EXE = Path(shutil.which("pyinstaller") or "pyinstaller")

PORTABLE_DIR = PROJECT_ROOT / "VRKA-portable"

def run_step(title, cmd, cwd=PROJECT_ROOT):
    print("=" * 60)
    print(f">> {title}")
    print("=" * 60)
    t0 = time.time()
    res = subprocess.run(cmd, cwd=cwd)
    dt = time.time() - t0
    if res.returncode != 0:
        print(f"\n[ERROR] Step failed with exit code {res.returncode} after {dt:.1f}s")
        sys.exit(res.returncode)
    print(f"[OK] Completed in {dt:.1f}s")

def main():
    print("=" * 60)
    print("VRKA 4.0 — BUILD & PACKAGING PIPELINE")
    print("=" * 60)
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

    # 2. Stage VRKA-portable directory
    print("\n" + "=" * 60)
    print(">> Step 2: Staging VRKA-portable distribution")
    print("=" * 60)
    PORTABLE_DIR.mkdir(parents=True, exist_ok=True)
    portable_exe = PORTABLE_DIR / "VRKA.exe"
    shutil.copy2(dist_exe, portable_exe)
    shutil.copy2(PROJECT_ROOT / "THIRD_PARTY_NOTICES.md", PORTABLE_DIR / "THIRD_PARTY_NOTICES.md")

    readme_path = PORTABLE_DIR / "README.txt"
    readme_path.write_text(
        "VRKA 4.0.0 — portable Windows x64\n\n"
        "Run VRKA.exe directly. Python, pip and administrator access are not required.\n"
        "Verify against SHA256SUMS.txt before running.\n",
        encoding="utf-8"
    )
    print(f"[OK] VRKA-portable updated ({portable_exe.stat().st_size:,} bytes)")

    # 3. Packaging Pipeline
    run_step(
        "Step 3: Packaging release artifacts",
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", "tools/package_vrka40_release.ps1"]
    )

if __name__ == "__main__":
    main()
