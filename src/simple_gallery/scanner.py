from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImageReader

from .database import Database
from .models import Library


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}


@dataclass(slots=True)
class ScanResult:
    library_id: str
    discovered: int
    reconnected: int
    skipped: int
    available: bool = True


def _capture_date(reader: QImageReader, modified: float) -> str:
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime", "CreationTime"):
        value = reader.text(key).strip()
        if not value:
            continue
        for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value[:19], pattern).replace(tzinfo=UTC).isoformat(timespec="seconds")
            except ValueError:
                pass
    return datetime.fromtimestamp(modified, UTC).isoformat(timespec="seconds")


def scan_library(
    database: Database,
    library: Library,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    on_progress: Callable[[int, str], None] = lambda _count, _path: None,
) -> ScanResult:
    root = Path(library.path)
    found_ids: set[str] = set()
    reconnected = 0
    skipped = 0
    discovered = 0
    if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
        message = "Folder is not mounted or accessible."
        database.set_library_availability(library.id, False, message)
        return ScanResult(library.id, 0, 0, 0, available=False)


    try:
        for directory, folders, files in os.walk(root, followlinks=False):
            if should_stop():
                database.rollback()
                return ScanResult(library.id, discovered, reconnected, skipped)
            folders[:] = [folder for folder in folders if not folder.startswith(".")]
            for file_name in files:
                if should_stop():
                    database.rollback()
                    return ScanResult(library.id, discovered, reconnected, skipped)
                path = Path(directory, file_name)
                if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    stat = path.stat()
                    reader = QImageReader(str(path))
                    reader.setAutoTransform(True)
                    size = reader.size()
                    if not size.isValid():
                        skipped += 1
                        continue
                    mime_type = mimetypes.guess_type(path.name)[0] or f"image/{path.suffix.lstrip('.').lower()}"
                    image_id, was_reconnected = database.upsert_scanned_image(
                        library_id=library.id,
                        path=str(path.resolve()),
                        device_id=stat.st_dev,
                        inode=stat.st_ino,
                        file_size=stat.st_size,
                        modified_ns=stat.st_mtime_ns,
                        mime_type=mime_type,
                        width=size.width(),
                        height=size.height(),
                        date_taken=_capture_date(reader, stat.st_mtime),
                    )
                    found_ids.add(image_id)
                    discovered += 1
                    reconnected += int(was_reconnected)
                    if discovered == 1 or discovered % 25 == 0:
                        on_progress(discovered, str(path))
                except (OSError, ValueError):
                    skipped += 1
    except OSError:
        skipped += 1

    if not should_stop():
        database.finish_library_scan(library.id, found_ids)
    else:
        database.rollback()
    return ScanResult(library.id, discovered, reconnected, skipped)


class ScanThread(QThread):
    progress = Signal(int, str)
    scan_finished = Signal(object)
    scan_failed = Signal(str)

    def __init__(self, database_path: Path, library: Library, parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.library = library

    def run(self) -> None:
        database: Database | None = None
        try:
            database = Database(self.database_path)
            result = scan_library(
                database,
                self.library,
                should_stop=self.isInterruptionRequested,
                on_progress=self.progress.emit,
            )
            if not self.isInterruptionRequested():
                self.scan_finished.emit(result)
        except Exception as error:  # Surface worker failures to the UI.
            self.scan_failed.emit(str(error))
        finally:
            if database is not None:
                database.close()
