# Building VRKA from Source

This guide provides instructions for building and running VRKA on Windows 10 and 11 (x64).

---

## Prerequisites

- **Operating System**: Windows 10 or 11 (x64)
- **Python**: Version 3.10, 3.11, or 3.12 (64-bit)
- **Git**: For source version control
- **FFmpeg & FFprobe**: Required for media muxing and audio conversion
- **Inno Setup 6** *(Optional)*: Required only if compiling the Windows setup installer

---

## Step 1: Clone the Repository

```powershell
git clone https://github.com/MaverickRox/VRKA.git
cd VRKA
```

---

## Step 2: Set Up Virtual Environment

```powershell
# Create an isolated Python virtual environment
python -m venv .venv

# Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# Upgrade pip and install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

## Step 3: Run the Application

```powershell
python vrka_qml_app.py
```

---

## Step 4: Compiling Executable Packages

### Building Standalone Executable
To compile the standalone `dist\VRKA.exe` binary with PyInstaller:

```powershell
pip install pyinstaller
pyinstaller --clean --noconfirm VRKA-Windows.spec
```

### Building Full Installer
If you have Inno Setup 6 installed, run the build script:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
```
