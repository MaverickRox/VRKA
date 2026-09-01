# Release Process

This document outlines the release verification and packaging procedures for VRKA.

---

## Pre-Release Checklist

1. **Code Quality & Syntax**: Verify that all Python files compile cleanly without errors (`python -m compileall -q .`).
2. **Branding & Versioning**: Confirm version metadata in `version_info.txt`, `VRKA-4.0.iss`, `pyproject.toml`, and QML files.
3. **Security Audit**: Ensure no temporary test profiles, API keys, personal credentials, or local paths are present in source files.
4. **Build Packaging**: Generate standalone binary with PyInstaller and compile Windows installer with Inno Setup.
5. **Checksum Generation**: Produce cryptographic SHA-256 hashes for all output packages in `SHA256SUMS.txt`.

---

## Publishing Releases

- Create a signed Git tag (`v4.0.0`).
- Create a GitHub Release with detailed user-facing notes and attached binary packages.
- Verify asset hashes against `SHA256SUMS.txt`.
