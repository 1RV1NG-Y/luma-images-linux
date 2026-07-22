from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .models import Album, ImageRecord


ROLE_IMAGE_ID = int(Qt.ItemDataRole.UserRole) + 1
ROLE_RECORD = ROLE_IMAGE_ID + 1
ROLE_MISSING = ROLE_IMAGE_ID + 2
ROLE_FAVORITE = ROLE_IMAGE_ID + 3
ROLE_DATE = ROLE_IMAGE_ID + 4
IMAGE_MIME_TYPE = "application/x-luma-image-ids"


class NavButton(QPushButton):
    def __init__(self, label: str, symbol: str = "", parent=None) -> None:
        super().__init__(f"{symbol}   {label}" if symbol else label, parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class FolderTreeRow(QWidget):
    disclosure_toggled = Signal(str)

    def __init__(
        self,
        nav_button: NavButton,
        folder_path: str,
        *,
        depth: int,
        has_children: bool,
        expanded: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FolderTreeRow")
        self.nav_button = nav_button
        self.folder_path = folder_path
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4 + depth * 13, 0, 0, 0)
        layout.setSpacing(0)
        self.disclosure = QToolButton()
        self.disclosure.setObjectName("FolderDisclosure")
        self.disclosure.setFixedSize(22, 30)
        self.disclosure.setText("⌄" if expanded else "›" if has_children else "")
        self.disclosure.setEnabled(has_children)
        if has_children:
            self.disclosure.setCursor(Qt.CursorShape.PointingHandCursor)
            self.disclosure.clicked.connect(
                lambda: self.disclosure_toggled.emit(self.folder_path)
            )
        layout.addWidget(self.disclosure)
        layout.addWidget(nav_button, 1)


class AlbumButton(NavButton):
    images_dropped = Signal(str, list)

    def __init__(self, album: Album, parent=None) -> None:
        super().__init__(album.name, "□", parent)
        self.album = album
        self.setAcceptDrops(True)
        self.setToolTip(f"Virtual album · {album.image_count} photos")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(IMAGE_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        try:
            raw = bytes(event.mimeData().data(IMAGE_MIME_TYPE)).decode("utf-8")
            image_ids = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            event.ignore()
            return
        self.images_dropped.emit(self.album.id, image_ids)
        event.acceptProposedAction()


class GalleryList(QListWidget):
    viewport_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("GalleryGrid")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(4)
        self.setGridSize(QSize(202, 205))
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.viewport_changed.emit()

    def startDrag(self, supported_actions) -> None:
        image_ids = [item.data(ROLE_IMAGE_ID) for item in self.selectedItems()]
        if not image_ids:
            return
        mime = QMimeData()
        mime.setData(IMAGE_MIME_TYPE, json.dumps(image_ids).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self.selectedItems()[0].data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            drag.setPixmap(pixmap.scaled(96, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        drag.exec(Qt.DropAction.CopyAction)


class GalleryDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bounds = option.rect.adjusted(6, 5, -6, -5)
        image_rect = QRect(bounds.left(), bounds.top(), bounds.width(), 151)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        missing = bool(index.data(ROLE_MISSING))

        path = QPainterPath()
        path.addRoundedRect(image_rect, 10, 10)
        painter.setClipPath(path)
        painter.fillRect(image_rect, option.palette.color(QPalette.ColorRole.AlternateBase))

        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            scaled = pixmap.scaled(
                image_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            source = QRect(
                max(0, (scaled.width() - image_rect.width()) // 2),
                max(0, (scaled.height() - image_rect.height()) // 2),
                image_rect.width(),
                image_rect.height(),
            )
            painter.drawPixmap(image_rect, scaled, source)
        else:
            painter.setPen(option.palette.color(QPalette.ColorRole.PlaceholderText))
            placeholder_font = QFont(painter.font())
            placeholder_font.setPointSize(25)
            painter.setFont(placeholder_font)
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "◇" if missing else "▧")

        painter.setClipping(False)
        if hovered or selected:
            outline = option.palette.color(QPalette.ColorRole.Highlight if selected else QPalette.ColorRole.Mid)
            painter.setPen(QPen(outline, 3 if selected else 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(image_rect.adjusted(1, 1, -1, -1), 10, 10)

        if index.data(ROLE_FAVORITE):
            badge = QRect(image_rect.right() - 29, image_rect.top() + 8, 22, 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(26, 27, 27, 180))
            painter.drawEllipse(badge)
            painter.setPen(QColor("#ffd36c"))
            star_font = QFont(painter.font())
            star_font.setPointSize(11)
            painter.setFont(star_font)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "★")

        title_rect = QRect(bounds.left() + 2, image_rect.bottom() + 9, bounds.width() - 4, 18)
        title_font = QFont(painter.font())
        title_font.setPointSize(10)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(option.palette.color(QPalette.ColorRole.Text))
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()))

        date_rect = QRect(title_rect.left(), title_rect.bottom() + 1, title_rect.width(), 17)
        detail_font = QFont(painter.font())
        detail_font.setPointSize(9)
        painter.setFont(detail_font)
        painter.setPen(QColor("#bd6257") if missing else option.palette.color(QPalette.ColorRole.PlaceholderText))
        detail = "Original unavailable" if missing else str(index.data(ROLE_DATE) or "")
        painter.drawText(date_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, detail)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(202, 205)


class EmptyState(QWidget):
    add_folder_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setMaximumWidth(430)
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)
        icon = QLabel("▧")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setObjectName("EmptyIcon")
        icon.setFixedSize(82, 82)
        title = QLabel("Bring your photo library into view")
        title.setObjectName("EmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body = QLabel("Choose a folder to index its photos. Luma keeps every original exactly where it is.")
        body.setObjectName("EmptyBody")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        button = QPushButton("Choose picture folder")
        button.setObjectName("PrimaryButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.add_folder_requested)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addSpacing(4)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(card)


class InspectorPanel(QFrame):
    save_requested = Signal(str, str, str, list, int, bool)
    rename_requested = Signal(str)
    reveal_requested = Signal(str)
    add_to_album_requested = Signal(list)
    bulk_favorite_requested = Signal(list, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Inspector")
        self.setMinimumWidth(280)
        self.setMaximumWidth(330)
        self._image: ImageRecord | None = None
        self._selection_ids: list[str] = []
        self._rating = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_editor())
        self.stack.addWidget(self._build_multiple())

    def _build_empty(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("ⓘ")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 26px; color: #a1a29d;")
        title = QLabel("Photo information")
        title.setObjectName("InspectorTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body = QLabel("Select a photo to see file details and add private organization.")
        body.setObjectName("MutedLabel")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(body)
        return page

    def _build_editor(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 22)
        layout.setSpacing(8)

        top = QHBoxLayout()
        heading = QLabel("Information")
        heading.setObjectName("InspectorTitle")
        top.addWidget(heading)
        top.addStretch()
        self.favorite_button = QToolButton()
        self.favorite_button.setObjectName("StarButton")
        self.favorite_button.setText("★")
        self.favorite_button.setCheckable(True)
        self.favorite_button.setToolTip("Favorite · app-only")
        top.addWidget(self.favorite_button)
        layout.addLayout(top)

        self.preview = QLabel()
        self.preview.setObjectName("Preview")
        self.preview.setFixedHeight(168)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)
        self.file_heading = QLabel()
        self.file_heading.setObjectName("InspectorTitle")
        self.file_heading.setWordWrap(True)
        layout.addWidget(self.file_heading)
        self.status_label = QLabel()
        self.status_label.setObjectName("MutedLabel")
        layout.addWidget(self.status_label)

        layout.addSpacing(5)
        layout.addWidget(self._field_label("TITLE · APP-ONLY"))
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("Editor")
        self.title_edit.setPlaceholderText("Add a title")
        layout.addWidget(self.title_edit)

        layout.addWidget(self._field_label("DESCRIPTION · APP-ONLY"))
        self.description_edit = QTextEdit()
        self.description_edit.setObjectName("Editor")
        self.description_edit.setPlaceholderText("Add a note about this photo")
        self.description_edit.setFixedHeight(76)
        layout.addWidget(self.description_edit)

        layout.addWidget(self._field_label("TAGS · APP-ONLY"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setObjectName("Editor")
        self.tags_edit.setPlaceholderText("travel, family, reference")
        layout.addWidget(self.tags_edit)

        rating_row = QHBoxLayout()
        rating_row.addWidget(self._field_label("RATING"))
        rating_row.addStretch()
        self.rating_buttons: list[QToolButton] = []
        for rating in range(1, 6):
            button = QToolButton()
            button.setObjectName("StarButton")
            button.setText("★")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, value=rating: self._set_rating(value))
            self.rating_buttons.append(button)
            rating_row.addWidget(button)
        layout.addLayout(rating_row)

        save = QPushButton("Save details")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self._emit_save)
        layout.addWidget(save)
        layout.addSpacing(6)
        layout.addWidget(self._hairline())
        layout.addSpacing(6)

        layout.addWidget(self._field_label("ORIGINAL FILE"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(7)
        self.path_value = self._info_value(selectable=True)
        self.size_value = self._info_value()
        self.dimensions_value = self._info_value()
        self.type_value = self._info_value()
        self.modified_value = self._info_value()
        form.addRow(self._info_key("Location"), self.path_value)
        form.addRow(self._info_key("Size"), self.size_value)
        form.addRow(self._info_key("Dimensions"), self.dimensions_value)
        form.addRow(self._info_key("Type"), self.type_value)
        form.addRow(self._info_key("Modified"), self.modified_value)
        layout.addLayout(form)

        album_title = self._field_label("VIRTUAL ALBUMS")
        layout.addWidget(album_title)
        self.album_value = QLabel("None")
        self.album_value.setObjectName("InfoValue")
        self.album_value.setWordWrap(True)
        layout.addWidget(self.album_value)
        album_button = QPushButton("Add to album…")
        album_button.setObjectName("SecondaryButton")
        album_button.clicked.connect(lambda: self.add_to_album_requested.emit(self._selection_ids))
        layout.addWidget(album_button)

        layout.addSpacing(6)
        layout.addWidget(self._hairline())
        warning = QLabel("File actions change the original on disk.")
        warning.setObjectName("MutedLabel")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        file_actions = QHBoxLayout()
        rename = QPushButton("Rename file…")
        rename.setObjectName("SecondaryButton")
        rename.clicked.connect(lambda: self._image and self.rename_requested.emit(self._image.id))
        reveal = QPushButton("Show in folder")
        reveal.setObjectName("SecondaryButton")
        reveal.clicked.connect(lambda: self._image and self.reveal_requested.emit(self._image.id))
        file_actions.addWidget(rename)
        file_actions.addWidget(reveal)
        layout.addLayout(file_actions)
        layout.addStretch()
        return scroll

    def _build_multiple(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 28, 26, 28)
        layout.setSpacing(10)
        self.multiple_title = QLabel()
        self.multiple_title.setObjectName("InspectorTitle")
        multiple_body = QLabel("Apply virtual organization to the selected photos together.")
        multiple_body.setObjectName("MutedLabel")
        multiple_body.setWordWrap(True)
        favorite = QPushButton("Mark as favorites")
        favorite.setObjectName("SecondaryButton")
        favorite.clicked.connect(lambda: self.bulk_favorite_requested.emit(self._selection_ids, True))
        album = QPushButton("Add to album…")
        album.setObjectName("PrimaryButton")
        album.clicked.connect(lambda: self.add_to_album_requested.emit(self._selection_ids))
        layout.addWidget(self.multiple_title)
        layout.addWidget(multiple_body)
        layout.addSpacing(6)
        layout.addWidget(album)
        layout.addWidget(favorite)
        layout.addStretch()
        return page

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    @staticmethod
    def _info_key(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("InfoKey")
        return label

    @staticmethod
    def _info_value(selectable: bool = False) -> QLabel:
        label = QLabel()
        label.setObjectName("InfoValue")
        label.setWordWrap(True)
        if selectable:
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _hairline() -> QFrame:
        line = QFrame()
        line.setObjectName("Hairline")
        line.setFixedHeight(1)
        return line

    def _set_rating(self, rating: int) -> None:
        self._rating = 0 if self._rating == rating else rating
        for index, button in enumerate(self.rating_buttons, start=1):
            button.setChecked(index <= self._rating)

    def _emit_save(self) -> None:
        if not self._image:
            return
        tags = [tag.strip() for tag in self.tags_edit.text().split(",") if tag.strip()]
        self.save_requested.emit(
            self._image.id,
            self.title_edit.text(),
            self.description_edit.toPlainText(),
            tags,
            self._rating,
            self.favorite_button.isChecked(),
        )

    def clear(self) -> None:
        self._image = None
        self._selection_ids = []
        self.stack.setCurrentIndex(0)

    def set_multiple(self, image_ids: list[str]) -> None:
        self._image = None
        self._selection_ids = image_ids
        self.multiple_title.setText(f"{len(image_ids)} photos selected")
        self.stack.setCurrentIndex(2)

    def set_image(self, image: ImageRecord, tags: list[str], albums: list[Album], thumbnail: str | None) -> None:
        self._image = image
        self._selection_ids = [image.id]
        self.file_heading.setText(image.display_name)
        self.status_label.setText("Original unavailable" if image.missing else "Original file · connected")
        self.status_label.setStyleSheet("color: #bd6257;" if image.missing else "")
        self.title_edit.setText(image.title or "")
        self.description_edit.setPlainText(image.description or "")
        self.tags_edit.setText(", ".join(tags))
        self.favorite_button.setChecked(image.favorite)
        self._rating = image.rating
        for index, button in enumerate(self.rating_buttons, start=1):
            button.setChecked(index <= image.rating)
        self.path_value.setText(image.path)
        self.size_value.setText(format_file_size(image.file_size))
        self.dimensions_value.setText(f"{image.width:,} × {image.height:,}")
        self.type_value.setText(image.mime_type or "Unknown")
        self.modified_value.setText(format_date(image.date_taken))
        self.album_value.setText(", ".join(album.name for album in albums) or "None")
        self.set_thumbnail(image.id, thumbnail)
        self.stack.setCurrentIndex(1)

    def set_thumbnail(self, image_id: str, path: str | None) -> None:
        if not self._image or self._image.id != image_id:
            return
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self.preview.setText("Original unavailable" if self._image.missing else "Preparing preview…")
            self.preview.setPixmap(QPixmap())
            return
        self.preview.setText("")
        self.preview.setPixmap(
            pixmap.scaled(
                max(1, self.preview.width()),
                self.preview.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


def format_file_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_date(value: str | None) -> str:
    if not value:
        return "Unknown"
    return value[:10]
