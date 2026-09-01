"""Stage 10 packaging/release tests (headless, no full PyInstaller build)."""

import os
import pathlib
import unittest

PROJECT_DIR = pathlib.Path(__file__).resolve().parent


class ApplicationIdentityTests(unittest.TestCase):
    def test_qml_app_identity_is_3_5_012(self):
        text = (PROJECT_DIR / "vrka_qml" / "app.py").read_text(encoding="utf-8")
        self.assertIn('APP_DISPLAY_VERSION = "3.5"', text)
        self.assertIn('APP_BUILD = "012"', text)

    def test_frozen_backend_identity_remains_3_0_011(self):
        text = (PROJECT_DIR / "vrka_downloader.py").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION = "3.0.0"', text)
        self.assertIn('APP_BUILD = "011"', text)

    def test_version_info_is_3_5_0_12(self):
        text = (PROJECT_DIR / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn("filevers=(3, 5, 0, 12)", text)
        self.assertIn("prodvers=(3, 5, 0, 12)", text)
        self.assertIn("FileVersion', '3.5.0.12'", text)
        self.assertIn("ProductVersion', '3.5.0'", text)

    def test_installer_metadata_is_3_5(self):
        text = (PROJECT_DIR / "VRKA.iss").read_text(encoding="utf-8")
        self.assertIn('MyAppVersion "3.5"', text)
        self.assertIn("VersionInfoVersion=3.5.0.12", text)
        self.assertIn("VRKA-3.5.0-setup-Windows-x64", text)
        self.assertIn("{{7C6E2F1A-4B3D-4E9A-9F2C-1A8D5E6B3C90}", text)

    def test_build_script_installer_name(self):
        text = (PROJECT_DIR / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("VRKA-3.5.0-setup-Windows-x64.exe", text)


class PyInstallerPackagingTests(unittest.TestCase):
    def test_spec_collects_pyside6_and_qml(self):
        text = (PROJECT_DIR / "VRKA-Windows.spec").read_text(encoding="utf-8")
        # PySide6 handled via standard hook (rth_pyside6) to avoid bundling 80+ unused Qt modules;
        # spec intentionally does not use collect_all("PySide6") / "shiboken6" / "vrka_qml"
        self.assertIn('vrka_qml/qml', text)
        self.assertIn('vrka_qml_app.py', text)
        self.assertIn('rth_pyside6', text.lower())

    def test_spec_preserves_existing_assets(self):
        text = (PROJECT_DIR / "VRKA-Windows.spec").read_text(encoding="utf-8")
        self.assertIn('assets/branding', text)
        self.assertIn('assets/fonts', text)
        self.assertIn('assets/browser_protection', text)
        self.assertIn('puemos-hls-downloader', text)
        self.assertIn('ffmpeg_bin', text)
        self.assertIn('THIRD_PARTY_NOTICES.md', text)

    def test_qml_app_handles_frozen_submodes(self):
        text = (PROJECT_DIR / "vrka_qml_app.py").read_text(encoding="utf-8")
        self.assertIn("__vrka_protected_browser__", text)
        self.assertIn("__vrka_browser__", text)
        self.assertIn("__ytdlp_cli__", text)
        self.assertIn("__vrka_diagnostics__", text)
        self.assertIn("run_protected_browser_helper", text)

    def test_app_frozen_resource_paths(self):
        text = (PROJECT_DIR / "vrka_qml" / "app.py").read_text(encoding="utf-8")
        self.assertIn('sys._MEIPASS', text)
        self.assertIn('QML_DIR', text)
        self.assertIn('_load_brand_fonts', text)

    def test_required_qml_files_exist(self):
        for rel in ["vrka_qml/qml/MainShell.qml", "vrka_qml/qml/Theme.qml",
                    "vrka_qml/qml/qmldir",
                    "vrka_qml/qml/components/TaskDelegate.qml",
                    "vrka_qml/qml/pages/SettingsPage.qml",
                    "vrka_qml/qml/pages/DownloadPage.qml"]:
            self.assertTrue((PROJECT_DIR / rel).is_file(), f"missing {rel}")

    def test_required_assets_exist(self):
        for rel in ["assets/branding/vrka.ico", "assets/branding/vrka-wolf-256.png",
                    "assets/fonts/SpaceMono-Regular.ttf", "assets/fonts/OFL.txt",
                    "third_party/media_observer/puemos-hls-downloader/extension-mv3-chrome-v5.5.0.zip"]:
            self.assertTrue((PROJECT_DIR / rel).is_file(), f"missing {rel}")


class LicensingTests(unittest.TestCase):
    def test_third_party_notices_header_and_pyside6(self):
        text = (PROJECT_DIR / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("VRKA 3.5 build 012", text)
        self.assertIn("PySide6 6.11.2", text)
        self.assertIn("LGPL-3.0", text)
        self.assertIn("Space Mono", text)
        self.assertIn("uBlock Origin Lite", text)
        self.assertIn("puemos/hls-downloader", text)

    def test_release_notes_has_3_5_section(self):
        text = (PROJECT_DIR / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("VRKA 3.5", text)
        self.assertIn("Build 012", text)
        self.assertIn("VRKA 3.0.0", text)  # historical preserved

    def test_ci_artifacts_and_regression(self):
        text = (PROJECT_DIR / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("VRKA-3.5.0-Windows-x64", text)
        self.assertIn("python -m unittest", text)


if __name__ == "__main__":
    unittest.main()
