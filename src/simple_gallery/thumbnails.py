from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter


class ThumbnailSignals(QObject):
    ready = Signal(str, str)
    failed = Signal(str)


class ThumbnailTask(QRunnable):
    def __init__(self, image_id: str, source: str, destination: Path, size: int) -> None:
        super().__init__()
        self.image_id = image_id
        self.source = source
        self.destination = destination
        self.size = size
        self.signals = ThumbnailSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        source_path = Path(self.source)
        try:
            source_stat = source_path.stat()
            if (
                self.destination.exists()
                and self.destination.stat().st_mtime_ns >= source_stat.st_mtime_ns
                and self.destination.stat().st_size > 0
            ):
                self.signals.ready.emit(self.image_id, str(self.destination))
                return

            reader = QImageReader(str(source_path))
            reader.setAutoTransform(True)
            original_size = reader.size()
            if not original_size.isValid():
                raise ValueError(reader.errorString() or "Unsupported image")
            target = QSize(self.size, self.size)
            original_size.scale(target, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(original_size)
            image = reader.read()
            if image.isNull():
                raise ValueError(reader.errorString() or "Could not decode image")

            if image.hasAlphaChannel():
                flattened = QImage(image.size(), QImage.Format.Format_RGB32)
                flattened.fill(QColor("#f4f4f2"))
                painter = QPainter(flattened)
                painter.drawImage(0, 0, image)
                painter.end()
                image = flattened

            self.destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.destination.with_suffix(".tmp.jpg")
            if not image.save(str(temporary), "JPEG", 88):
                raise OSError("Could not save thumbnail")
            temporary.replace(self.destination)
            self.signals.ready.emit(self.image_id, str(self.destination))
        except (OSError, ValueError):
            self.signals.failed.emit(self.image_id)


class ThumbnailManager(QObject):
    ready = Signal(str, str)
    failed = Signal(str)

    def __init__(self, cache_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max(2, min(4, self.pool.maxThreadCount())))
        self._pending: set[str] = set()
        self._validated: set[str] = set()

    def path_for(self, image_id: str) -> Path:
        return self.cache_dir / f"{image_id}.jpg"

    def request(self, image_id: str, source: str, size: int = 640) -> None:
        if image_id in self._pending or image_id in self._validated:
            return
        self._pending.add(image_id)
        task = ThumbnailTask(image_id, source, self.path_for(image_id), size)
        task.signals.ready.connect(self._on_ready)
        task.signals.failed.connect(self._on_failed)
        self.pool.start(task)

    def reset_validation(self) -> None:
        self._validated.clear()

    def _on_ready(self, image_id: str, path: str) -> None:
        self._pending.discard(image_id)
        self._validated.add(image_id)
        self.ready.emit(image_id, path)

    def _on_failed(self, image_id: str) -> None:
        self._pending.discard(image_id)
        self.failed.emit(image_id)
