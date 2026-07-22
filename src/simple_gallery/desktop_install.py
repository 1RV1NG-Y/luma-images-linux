from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.resources import as_file, files
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage

APP_ID = "luma-gallery"
ICON_SIZES = (48, 64, 128, 256, 512, 1024)


def _data_home() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".local" / "share"


def _application_executable() -> Path:
    invoked = Path(sys.argv[0]).expanduser()
    if not invoked.is_absolute():
        located_installer = shutil.which(sys.argv[0])
        if located_installer:
            invoked = Path(located_installer)
    sibling = invoked.absolute().with_name(APP_ID)
    if sibling.exists():
        return sibling
    located_application = shutil.which(APP_ID)
    if located_application:
        return Path(located_application).absolute()
    raise RuntimeError(
        "Could not locate the luma-gallery executable. Install Luma with "
        "'uv tool install .' before running this command."
    )


def _desktop_exec(path: Path) -> str:
    escaped = str(path).replace("\\", "\\\\")
    for character in ('"', "`", "$"):
        escaped = escaped.replace(character, f"\\{character}")
    return f'"{escaped.replace("%", "%%")}"'


def _install_icons(data_home: Path) -> Path:
    resource = files("simple_gallery.resources").joinpath("luma-gallery.png")
    with as_file(resource) as icon_path:
        image = QImage(str(icon_path))
    if image.isNull():
        raise RuntimeError("The packaged Luma icon could not be decoded.")
    installed_icon = data_home / "icons" / f"{APP_ID}.png"
    installed_icon.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(installed_icon), "PNG"):
        raise RuntimeError(f"Could not install {installed_icon}")
    for size in ICON_SIZES:
        destination = data_home / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        scaled = image.scaled(
            QSize(size, size),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if not scaled.save(str(destination), "PNG"):
            raise RuntimeError(f"Could not install {destination}")
    return installed_icon


def _install_desktop_entry(data_home: Path, executable: Path, icon: Path) -> Path:
    template = (
        files("simple_gallery.resources")
        .joinpath("luma-gallery.desktop.in")
        .read_text(encoding="utf-8")
    )
    desktop_entry = (
        template.replace("@EXECUTABLE@", _desktop_exec(executable))
        .replace("@ICON@", str(icon))
    )
    destination = data_home / "applications" / f"{APP_ID}.desktop"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(desktop_entry, encoding="utf-8")
    destination.chmod(0o644)
    return destination


def _refresh_desktop_database(data_home: Path) -> None:
    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run(
            [updater, str(data_home / "applications")],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> int:
    data_home = _data_home()
    executable = _application_executable()
    icon = _install_icons(data_home)
    desktop_entry = _install_desktop_entry(data_home, executable, icon)
    _refresh_desktop_database(data_home)
    print(f"Installed {desktop_entry}")
    print("Luma is ready in GNOME application search and can be pinned to the dash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
