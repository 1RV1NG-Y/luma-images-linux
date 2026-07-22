from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .styles import apply_theme

APP_ID = "luma-gallery"
ICON_PATH = Path(__file__).parent / "resources" / "luma-gallery.png"


def application_paths() -> tuple[Path, Path]:
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    cache_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "thumbnails"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "library.sqlite3", cache_dir


def main() -> int:
    QCoreApplication.setOrganizationName("Luma")
    QCoreApplication.setOrganizationDomain("app.luma.gallery")
    QCoreApplication.setApplicationName("Luma")
    application = QApplication(sys.argv)
    QGuiApplication.setDesktopFileName(APP_ID)
    application.setWindowIcon(QIcon(str(ICON_PATH)))
    application.setStyle("Fusion")
    apply_theme(application, str(QSettings().value("appearance/theme", "light")))

    database_path, cache_dir = application_paths()
    window = MainWindow(database_path, cache_dir)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
