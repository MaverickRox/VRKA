"""Core Qt Quick bootstrap for VRKA 4.0.0 (Build 016).

Presentation layer: loads the QML shell and wires the presentation bridge
to the engine host. Stage 4 adds QueueController, restored-task seeding
and history serving.
"""

from __future__ import annotations

import os
import queue
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine

from .bridge import PresentationBridge
from .download_controller import DownloadController
from .engine_host import EngineHost
from .operational_controller import OperationalController
from .queue_controller import QueueController
from .settings_state import SettingsState

# Active VRKA 3.5 application identity (the frozen 3.0 / Build 011 metadata in
# vrka_downloader.py, version_info.txt and the installer stays untouched).
APP_DISPLAY_VERSION = "4.0.0"
APP_BUILD = "016"

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_ROOT = Path(sys._MEIPASS)
    QML_DIR = Path(sys._MEIPASS) / "vrka_qml" / "qml"
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    QML_DIR = Path(__file__).resolve().parent / "qml"


def _load_brand_fonts() -> None:
    fonts_dir = PROJECT_ROOT / "assets" / "fonts"
    for name in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
        QFontDatabase.addApplicationFont(str(fonts_dir / name))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    smoke = "--smoke" in argv
    if smoke:
        # Headless-safe default; an explicit QT_QPA_PLATFORM always wins.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QApplication([sys.argv[0]] + [a for a in argv if a != "--smoke"])
    app.setApplicationName("VRKA")
    app.setOrganizationName("MVRK")
    app.setApplicationDisplayName("VRKA - Media Downloader")
    app.setApplicationVersion(APP_DISPLAY_VERSION)
    # Taskbar grouping: match 3.0's explicit AppUserModelID so Windows groups correctly
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VRKA.Downloader")
    except Exception:
        pass
    # Window / taskbar icon — same VRKA wolf used for packaged EXE (vrka.ico)
    try:
        app.setWindowIcon(QIcon(str(PROJECT_ROOT / "assets" / "branding" / "vrka.ico")))
    except Exception:
        pass
    _load_brand_fonts()

    engine = QQmlApplicationEngine()
    # QML exposure: identity strings, the presentation bridge, the download
    # controller, and the queue/history action controller. One shared queue
    # feeds the bridge; the engine host produces into it exactly like the CTk
    # application does.
    shared_queue = queue.Queue()
    engine_host = EngineHost(shared_queue)
    bridge = PresentationBridge(shared_queue)
    settings = SettingsState(engine_host)
    # Load persisted settings before any QML binding evaluates.
    settings.load()
    controller = DownloadController(engine_host, settings)
    queue_ctrl = QueueController(engine_host, bridge)
    engine_host._queue_controller = queue_ctrl
    operational = OperationalController(engine_host, bridge, settings)

    engine.rootContext().setContextProperty("APP_DISPLAY_VERSION", APP_DISPLAY_VERSION)
    engine.rootContext().setContextProperty("APP_BUILD", APP_BUILD)
    engine.rootContext().setContextProperty("Bridge", bridge)
    engine.rootContext().setContextProperty("Controller", controller)
    engine.rootContext().setContextProperty("QueueController", queue_ctrl)
    engine.rootContext().setContextProperty("Settings", settings)
    engine.rootContext().setContextProperty("Operational", operational)

    app.aboutToQuit.connect(bridge.shutdown)
    app.aboutToQuit.connect(lambda: engine_host.shutdown())

    def _serve_history():
        bridge.history.set_entries(engine_host.history)

    bridge.historyRefreshRequested.connect(_serve_history)
    # Stage 5: History "Again" must prefill the Download page via the
    # existing DownloadController seam without exposing history internals.
    queue_ctrl.redownloadRequested.connect(
        lambda url: controller.prefillRequested.emit(str(url))
    )

    if smoke:
        # Full stack construction, but the scheduler worker is never started
        # here, so a smoke run can never begin a persisted download.
        pass
    else:
        engine_host.start()
        # Seed restored durable tasks into the presentation model.
        for snapshot in engine_host.restored_task_snapshots():
            bridge.seed_task(snapshot)
        # Serve persisted history entries.
        _serve_history()

    bridge.start()
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "MainShell.qml")))
    if not engine.rootObjects():
        # QML syntax errors, missing files or failed resource resolution all
        # surface here as "no root object".
        return 1

    if smoke:
        # Exercise one event-loop pass so bindings/delegates actually run,
        # then terminate cleanly with success.
        QTimer.singleShot(300, app.quit)
        return 0 if app.exec() == 0 else 1

    return app.exec()
