from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QImageReader,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .database import Database
from .models import ImageRecord
from .widgets import ROLE_IMAGE_ID, format_date, format_file_size
from .window_chrome import create_resize_handles, position_resize_handles


class FullImageSignals(QObject):
    ready = Signal(str, object)
    failed = Signal(str, str)


class FullImageTask(QRunnable):
    def __init__(self, image_id: str, source: str) -> None:
        super().__init__()
        self.image_id = image_id
        self.source = source
        self.signals = FullImageSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        if not Path(self.source).is_file():
            self.signals.failed.emit(self.image_id, "Original file is unavailable")
            return
        reader = QImageReader(self.source)
        reader.setAutoTransform(True)
        decoded = reader.read()
        if decoded.isNull():
            self.signals.failed.emit(
                self.image_id,
                reader.errorString() or "Could not display this image",
            )
            return
        self.signals.ready.emit(self.image_id, decoded)


class ZoomableImageView(QWidget):
    zoom_changed = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerImage")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pixmap = QPixmap()
        self._message = ""
        self._zoom = 1.0
        self._offset = QPointF()
        self._drag_origin: QPointF | None = None
        self._drag_offset = QPointF()

    @property
    def zoom_factor(self) -> float:
        return self._zoom

    def set_image(self, image: QImage, *, reset_zoom: bool = True) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self._message = ""
        if reset_zoom:
            self.reset_zoom()
        else:
            self._clamp_offset()
            self.update()

    def set_message(self, message: str) -> None:
        self._pixmap = QPixmap()
        self._message = message
        self.reset_zoom()

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._offset = QPointF()
        self._update_cursor()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def _fit_scale(self) -> float:
        if self._pixmap.isNull():
            return 1.0
        available_width = max(1, self.width() - 28)
        available_height = max(1, self.height() - 28)
        return min(
            1.0,
            available_width / self._pixmap.width(),
            available_height / self._pixmap.height(),
        )

    def _display_scale(self) -> float:
        return self._fit_scale() * self._zoom

    def _centered_top_left(self, scale: float) -> QPointF:
        return QPointF(
            (self.width() - self._pixmap.width() * scale) / 2,
            (self.height() - self._pixmap.height() * scale) / 2,
        )

    def _image_top_left(self, scale: float) -> QPointF:
        return self._centered_top_left(scale) + self._offset

    def _clamp_offset(self) -> None:
        if self._pixmap.isNull():
            self._offset = QPointF()
            return
        scale = self._display_scale()
        overflow_x = max(0.0, (self._pixmap.width() * scale - self.width()) / 2)
        overflow_y = max(0.0, (self._pixmap.height() * scale - self.height()) / 2)
        self._offset.setX(max(-overflow_x, min(overflow_x, self._offset.x())))
        self._offset.setY(max(-overflow_y, min(overflow_y, self._offset.y())))

    def _update_cursor(self) -> None:
        cursor = Qt.CursorShape.OpenHandCursor if self._zoom > 1.0 else Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#111312"))
        if self._pixmap.isNull():
            painter.setPen(QColor("#8d8f89"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        scale = self._display_scale()
        top_left = self._image_top_left(scale)
        target = QRectF(
            top_left.x(),
            top_left.y(),
            self._pixmap.width() * scale,
            self._pixmap.height() * scale,
        )
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if self._pixmap.isNull() or delta == 0:
            super().wheelEvent(event)
            return
        old_zoom = self._zoom
        new_zoom = max(1.0, min(8.0, old_zoom * (1.18 ** (delta / 120))))
        if new_zoom == old_zoom:
            event.accept()
            return
        old_scale = self._fit_scale() * old_zoom
        anchor = event.position()
        old_top_left = self._image_top_left(old_scale)
        image_point = QPointF(
            (anchor.x() - old_top_left.x()) / old_scale,
            (anchor.y() - old_top_left.y()) / old_scale,
        )
        self._zoom = new_zoom
        new_scale = self._display_scale()
        centered = self._centered_top_left(new_scale)
        self._offset = QPointF(
            anchor.x() - image_point.x() * new_scale - centered.x(),
            anchor.y() - image_point.y() * new_scale - centered.y(),
        )
        self._clamp_offset()
        self._update_cursor()
        self.zoom_changed.emit(self._zoom)
        self.update()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._zoom > 1.0:
            self._drag_origin = event.position()
            self._drag_offset = QPointF(self._offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self._offset = self._drag_offset + event.position() - self._drag_origin
            self._clamp_offset()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            self._drag_origin = None
            self._update_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._pixmap.isNull():
            self.reset_zoom()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._clamp_offset()


class ViewerChrome(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.maximize_button: QToolButton | None = None
        self.setObjectName("ViewerChrome")
        self.setFixedHeight(56)

    def update_window_state(self) -> None:
        if self.maximize_button is None:
            return
        expanded = self.window().isMaximized() or self.window().isFullScreen()
        self.maximize_button.setText("❐" if expanded else "□")
        self.maximize_button.setToolTip("Restore" if expanded else "Maximize")

    def toggle_maximized(self) -> None:
        if self.window().isMaximized() or self.window().isFullScreen():
            self.window().showNormal()
        else:
            self.window().showMaximized()
        self.update_window_state()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.window().isMaximized()
            and not self.window().isFullScreen()
        ):
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ImageViewer(QDialog):
    favorite_changed = Signal(str, bool)
    full_image_ready = Signal(str)

    def __init__(
        self,
        database: Database,
        images: list[ImageRecord],
        current_id: str,
        cache_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._resize_handles = create_resize_handles(self)
        self.setObjectName("Viewer")
        self.setWindowTitle("Luma Viewer")
        self.setMinimumSize(900, 620)
        self.resize(1220, 780)
        self.database = database
        self.images = images
        self.cache_dir = cache_dir
        self.index = next((index for index, image in enumerate(images) if image.id == current_id), 0)
        self._info_visible = True
        self._image_pool = QThreadPool.globalInstance()
        self._pending_image_loads: set[str] = set()
        self._filmstrip_loaded: set[str] = set()
        self._launch_window_state = Qt.WindowState.WindowNoState
        self._launch_state_pending = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.chrome = self._build_chrome()
        root.addWidget(self.chrome)

        self.content = QWidget()
        content_layout = QHBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        previous = self._viewer_button("‹", "Previous photo")
        previous.setFixedWidth(54)
        previous.clicked.connect(self.previous)
        content_layout.addWidget(previous)
        self.image_view = ZoomableImageView()
        self.image_view.zoom_changed.connect(self._zoom_changed)
        content_layout.addWidget(self.image_view, 1)
        following = self._viewer_button("›", "Next photo")
        following.setFixedWidth(54)
        following.clicked.connect(self.next)
        content_layout.addWidget(following)
        self.info_panel = self._build_info_panel()
        content_layout.addWidget(self.info_panel)
        root.addWidget(self.content, 1)
        root.addWidget(self._build_filmstrip())

        self._populate_filmstrip()
        self._show_current()

    def _build_chrome(self) -> ViewerChrome:
        chrome = ViewerChrome()
        layout = QHBoxLayout(chrome)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(6)
        close = self._viewer_button("‹", "Close viewer")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.name_label = QLabel()
        self.name_label.setObjectName("ViewerName")
        self.name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.name_label)
        layout.addStretch()
        self.zoom_label = QLabel("Fit")
        self.zoom_label.setObjectName("ViewerZoom")
        self.zoom_label.setToolTip("Scroll to zoom · drag to pan · double-click to fit")
        layout.addWidget(self.zoom_label)
        self.favorite_button = self._viewer_button("☆", "Favorite · app-only")
        self.favorite_button.clicked.connect(self._toggle_favorite)
        layout.addWidget(self.favorite_button)
        info = self._viewer_button("ⓘ", "Toggle information")
        info.clicked.connect(self._toggle_info)
        layout.addWidget(info)
        minimize = self._window_button("−", "Minimize")
        minimize.clicked.connect(self.showMinimized)
        layout.addWidget(minimize)
        chrome.maximize_button = self._window_button("□", "Maximize")
        chrome.maximize_button.clicked.connect(chrome.toggle_maximized)
        layout.addWidget(chrome.maximize_button)
        close_window = self._window_button("×", "Close", close=True)
        close_window.clicked.connect(self.accept)
        layout.addWidget(close_window)
        return chrome

    def _build_info_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("ViewerInfo")
        panel.setFixedWidth(270)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Details")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)
        self.detail_title = QLabel()
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-size: 14px; font-weight: 600; margin-top: 8px;")
        layout.addWidget(self.detail_title)
        self.detail_description = QLabel()
        self.detail_description.setObjectName("MutedLabel")
        self.detail_description.setWordWrap(True)
        layout.addWidget(self.detail_description)
        layout.addSpacing(12)
        self.detail_labels: dict[str, QLabel] = {}
        for key in ("Date", "Dimensions", "Size", "Rating", "Camera", "Lens", "Albums", "Tags"):
            row = QHBoxLayout()
            key_label = QLabel(key)
            key_label.setObjectName("MutedLabel")
            value = QLabel()
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            value.setWordWrap(True)
            row.addWidget(key_label)
            row.addWidget(value, 1)
            layout.addLayout(row)
            self.detail_labels[key] = value
        layout.addSpacing(10)
        path_title = QLabel("Original location")
        path_title.setObjectName("MutedLabel")
        layout.addWidget(path_title)
        self.detail_path = QLabel()
        self.detail_path.setWordWrap(True)
        self.detail_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail_path)
        layout.addStretch()
        hint = QLabel("Scroll zooms   ·   drag pans   ·   double-click fits\n← → move   ·   I details   ·   Esc close")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return panel

    def _build_filmstrip(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("ViewerFilmstrip")
        frame.setFixedHeight(92)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 9, 14, 9)
        self.filmstrip = QListWidget()
        self.filmstrip.setObjectName("Filmstrip")
        self.filmstrip.setViewMode(QListWidget.ViewMode.IconMode)
        self.filmstrip.setFlow(QListWidget.Flow.LeftToRight)
        self.filmstrip.setWrapping(False)
        self.filmstrip.setMovement(QListWidget.Movement.Static)
        self.filmstrip.setIconSize(QSize(74, 58))
        self.filmstrip.setGridSize(QSize(82, 68))
        self.filmstrip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.filmstrip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.filmstrip.currentRowChanged.connect(self._filmstrip_changed)
        layout.addWidget(self.filmstrip)
        return frame

    @staticmethod
    def _viewer_button(text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("ViewerButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    @staticmethod
    def _window_button(text: str, tooltip: str, close: bool = False) -> QToolButton:
        button = QToolButton()
        button.setObjectName("ViewerCloseButton" if close else "ViewerWindowButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(44, 55)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _populate_filmstrip(self) -> None:
        for image in self.images:
            item = QListWidgetItem()
            item.setData(ROLE_IMAGE_ID, image.id)
            item.setToolTip(image.display_name)
            self.filmstrip.addItem(item)
        self.filmstrip.setCurrentRow(self.index)
        self._load_filmstrip_thumbnails()

    def _load_filmstrip_thumbnails(self) -> None:
        start = max(0, self.index - 12)
        end = min(len(self.images), self.index + 13)
        for row in range(start, end):
            image = self.images[row]
            if image.id in self._filmstrip_loaded:
                continue
            thumbnail = self.cache_dir / f"{image.id}.jpg"
            if not thumbnail.exists():
                continue
            self.filmstrip.item(row).setIcon(QPixmap(str(thumbnail)))
            self._filmstrip_loaded.add(image.id)

    @property
    def current(self) -> ImageRecord:
        return self.images[self.index]

    def _show_current(self) -> None:
        image = self.current
        self.name_label.setText(image.file_name)
        self.favorite_button.setText("★" if image.favorite else "☆")
        self.detail_title.setText(image.display_name)
        self.detail_description.setText(image.description or "No description")
        self.detail_labels["Date"].setText(format_date(image.date_taken))
        self.detail_labels["Dimensions"].setText(f"{image.width:,} × {image.height:,}")
        self.detail_labels["Size"].setText(format_file_size(image.file_size))
        self.detail_labels["Rating"].setText("★" * image.rating or "—")
        tags = self.database.tags_for_image(image.id)
        albums = self.database.albums_for_image(image.id)
        self.detail_labels["Tags"].setText(", ".join(tags) or "—")
        self.detail_labels["Albums"].setText(", ".join(album.name for album in albums) or "—")
        camera, lens = self._embedded_details(image.path)
        self.detail_labels["Camera"].setText(camera or "—")
        self.detail_labels["Lens"].setText(lens or "—")
        self.detail_path.setText(image.path)
        if self.filmstrip.currentRow() != self.index:
            self.filmstrip.blockSignals(True)
            self.filmstrip.setCurrentRow(self.index)
            self.filmstrip.blockSignals(False)
        self._load_filmstrip_thumbnails()
        self.filmstrip.scrollToItem(self.filmstrip.item(self.index), QListWidget.ScrollHint.PositionAtCenter)
        self._request_full_image()

    @staticmethod
    def _embedded_details(path: str) -> tuple[str, str]:
        reader = QImageReader(path)
        values = {key.casefold(): reader.text(key).strip() for key in reader.textKeys()}
        make = values.get("make", "")
        model = values.get("model", "")
        camera = " ".join(part for part in (make, model) if part)
        lens = values.get("lensmodel", "") or values.get("lens", "")
        return camera, lens

    def _request_full_image(self) -> None:
        image = self.current
        if image.missing or not Path(image.path).exists():
            self.image_view.set_message("Original file is unavailable")
            return
        thumbnail = self.cache_dir / f"{image.id}.jpg"
        preview = QImage(str(thumbnail)) if thumbnail.exists() else QImage()
        if preview.isNull():
            self.image_view.set_message("Loading photo…")
        else:
            self.image_view.set_image(preview)
        if image.id in self._pending_image_loads:
            return
        self._pending_image_loads.add(image.id)
        task = FullImageTask(image.id, image.path)
        task.signals.ready.connect(self._full_image_loaded)
        task.signals.failed.connect(self._full_image_failed)
        self._image_pool.start(task)

    def _full_image_loaded(self, image_id: str, decoded: object) -> None:
        self._pending_image_loads.discard(image_id)
        if image_id != self.current.id or not isinstance(decoded, QImage):
            return
        self.image_view.set_image(decoded, reset_zoom=False)
        self.full_image_ready.emit(image_id)

    def _full_image_failed(self, image_id: str, message: str) -> None:
        self._pending_image_loads.discard(image_id)
        if image_id == self.current.id and self.image_view._pixmap.isNull():
            self.image_view.set_message(message)

    def _zoom_changed(self, factor: float) -> None:
        self.zoom_label.setText("Fit" if factor == 1.0 else f"{factor * 100:.0f}%")

    def _filmstrip_changed(self, row: int) -> None:
        if 0 <= row < len(self.images) and row != self.index:
            self.index = row
            self._show_current()

    def previous(self) -> None:
        self.index = (self.index - 1) % len(self.images)
        self._show_current()

    def next(self) -> None:
        self.index = (self.index + 1) % len(self.images)
        self._show_current()

    def _toggle_favorite(self) -> None:
        image = self.current
        image.favorite = not image.favorite
        self.database.set_favorite([image.id], image.favorite)
        self.favorite_button.setText("★" if image.favorite else "☆")
        self.favorite_changed.emit(image.id, image.favorite)

    def _toggle_info(self) -> None:
        self._info_visible = not self._info_visible
        self.info_panel.setVisible(self._info_visible)

    def set_launch_window_state(self, state: Qt.WindowState) -> None:
        if state & Qt.WindowState.WindowFullScreen:
            self._launch_window_state = Qt.WindowState.WindowFullScreen
        elif state & Qt.WindowState.WindowMaximized:
            self._launch_window_state = Qt.WindowState.WindowMaximized
        else:
            self._launch_window_state = Qt.WindowState.WindowNoState
        self._launch_state_pending = self._launch_window_state != Qt.WindowState.WindowNoState
        self.setWindowState(self._launch_window_state)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._launch_state_pending:
            return
        self._launch_state_pending = False
        if self._launch_window_state == Qt.WindowState.WindowFullScreen:
            QTimer.singleShot(0, self.showFullScreen)
        elif self._launch_window_state == Qt.WindowState.WindowMaximized:
            QTimer.singleShot(0, self.showMaximized)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_resize_handles"):
            position_resize_handles(self, self._resize_handles)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "chrome"):
            self.chrome.update_window_state()
            position_resize_handles(self, self._resize_handles)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Left:
            self.previous()
        elif event.key() == Qt.Key.Key_Right:
            self.next()
        elif event.key() == Qt.Key.Key_I:
            self._toggle_info()
        elif event.key() == Qt.Key.Key_Escape:
            self.accept()
        elif event.key() == Qt.Key.Key_F:
            self._toggle_favorite()
        elif event.key() == Qt.Key.Key_0:
            self.image_view.reset_zoom()
        else:
            super().keyPressEvent(event)
