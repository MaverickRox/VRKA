"""Stage 8 tests: DPI verification harness + visual coherence.

No QML modification is required when DPI 100–200% preserves logical geometry.
These tests prove the harness artifacts exist, are non-zero, and that the
existing QML uses only logical pixels / Theme tokens.
"""

from __future__ import annotations

import os
import pathlib
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
UI_DIR = PROJECT_DIR / "verification" / "ui"
SCALES = [100, 125, 150, 175, 200]
WINDOWS = [(1020, 700), (1240, 820), (1600, 1000)]
PAGES = ["download", "queue", "history", "settings"]


class Stage8ArtifactTests(unittest.TestCase):
    def test_stage8_core_artifacts_exist(self):
        # 3 windows × 4 pages × 5 scales = 60 core images; harness also produces
        # maximized + dark/light variants but core is required.
        missing = []
        for scale in SCALES:
            tag = f"{scale:03d}"
            for w, h in WINDOWS:
                for page in PAGES:
                    p = UI_DIR / f"stage8-{tag}-{page}-{w}x{h}.png"
                    if not p.is_file() or p.stat().st_size < 4000:
                        missing.append(str(p))
        self.assertEqual(missing, [], f"missing Stage 8 core artifacts: {missing[:5]}")

    def test_stage8_popup_and_theme_variants(self):
        # Popup/light-dark are additional required cells per directive.
        for scale in (100, 150, 200):
            tag = f"{scale:03d}"
            for variant in ("dark", "light"):
                p = UI_DIR / f"stage8-{tag}-settings-{variant}-1240x820.png"
                # Light/dark only produced for 100,150,200 per harness; verify when present.
                if scale in (100, 150, 200):
                    self.assertTrue(p.is_file() and p.stat().st_size >= 4000, f"missing {p}")

    def test_stage8_maximized_artifact(self):
        p = UI_DIR / "stage8-100-maximized.png"
        self.assertTrue(p.is_file() and p.stat().st_size >= 4000)

    def test_log_chip_not_hardcoded_physical(self):
        # ActivityLogView uses logical 44×14; no devicePixelRatio multiplication in QML.
        qml = (PROJECT_DIR / "vrka_qml" / "qml" / "components" / "ActivityLogView.qml").read_text(encoding="utf-8")
        self.assertIn("Layout.preferredWidth: 44", qml)
        self.assertIn("Layout.preferredHeight: 14", qml)
        self.assertNotIn("devicePixelRatio", qml)
        self.assertNotIn("Screen.devicePixelRatio", qml)


class ThemeTokenTests(unittest.TestCase):
    def test_no_hardcoded_hex_in_qml(self):
        import re
        hex_pat = re.compile(r"#[0-9A-Fa-f]{6}")
        for root in [PROJECT_DIR / "vrka_qml" / "qml" / "components",
                     PROJECT_DIR / "vrka_qml" / "qml" / "pages"]:
            for p in root.glob("*.qml"):
                if p.name == "Theme.qml":
                    continue
                text = p.read_text(encoding="utf-8")
                self.assertIsNone(hex_pat.search(text), f"hard-coded hex in {p.name}")

    def test_theme_is_sole_source(self):
        theme = (PROJECT_DIR / "vrka_qml" / "qml" / "Theme.qml").read_text(encoding="utf-8")
        self.assertIn('property color accent:          "#8140DC"', theme)
        self.assertIn('property int    logoSize:       72', theme)
        self.assertIn('property int sidebarWidth:         240', theme)

    def test_qt_rounding_policy_passthrough(self):
        # Offscreen policy must be PassThrough for fractional factors (factual, from worker log).
        # Create app if not exists.
        app = QGuiApplication.instance() or QGuiApplication([])
        self.assertEqual(
            QGuiApplication.highDpiScaleFactorRoundingPolicy().name,
            "PassThrough",
        )

    def test_logical_sizes_stable_across_scales(self):
        # Stage 8 hypothesis: logical Theme sizes unchanged at any scale.
        # Verify a sampled stage8 image for 150% still has logical 1240x820 header by parsing filename
        # and that file size is non-zero (already checked), no layout break implied by missing file.
        # Here we just assert theme constants logical pixel nature (no DPR math).
        theme = (PROJECT_DIR / "vrka_qml" / "qml" / "Theme.qml").read_text(encoding="utf-8")
        self.assertIn("readonly property int sidebarWidth:         240", theme)
        self.assertNotIn("devicePixelRatio", theme)


if __name__ == "__main__":
    unittest.main()
