from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QMouseEvent, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QSizeGrip, QToolButton, QWidget


class TitleBar(QFrame):
    theme_toggle_requested = Signal()
    information_toggle_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(8)

        brand = QLabel("L")
        brand.setObjectName("BrandMark")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setFixedSize(28, 28)
        brand.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(brand)

        context = QLabel("Photo Library")
        context.setObjectName("WorkspaceLabel")
        context.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(context)
        layout.addStretch()

        self.action_area = QHBoxLayout()
        self.action_area.setSpacing(6)
        layout.addLayout(self.action_area)

        self.information_button = QToolButton()
        self.information_button.setObjectName("PanelButton")
        self.information_button.setText("◫")
        self.information_button.setCheckable(True)
        self.information_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.information_button.clicked.connect(lambda: self.information_toggle_requested.emit())
        layout.addWidget(self.information_button)

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("ThemeButton")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self.theme_toggle_requested)
        layout.addWidget(self.theme_button)

        self.minimize_button = self._window_button("−", "Minimize")
        self.minimize_button.clicked.connect(lambda: self.window().showMinimized())
        layout.addWidget(self.minimize_button)

        self.maximize_button = self._window_button("□", "Maximize")
        self.maximize_button.clicked.connect(self.toggle_maximized)
        layout.addWidget(self.maximize_button)

        self.close_button = self._window_button("×", "Close", close=True)
        self.close_button.clicked.connect(lambda: self.window().close())
        layout.addWidget(self.close_button)
        self.set_theme("light")

    @staticmethod
    def _window_button(text: str, tooltip: str, close: bool = False) -> QToolButton:
        button = QToolButton()
        button.setObjectName("CloseButton" if close else "WindowButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(44, 43)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def add_action(self, widget: QWidget) -> None:
        self.action_area.addWidget(widget)

    def set_information_visible(self, visible: bool) -> None:
        self.information_button.setChecked(visible)
        action = "Hide" if visible else "Show"
        self.information_button.setToolTip(f"{action} photo information   Ctrl+I")

    def set_theme(self, theme: str) -> None:
        dark = theme == "dark"
        self.theme_button.setText("☀" if dark else "☾")
        self.theme_button.setToolTip("Use light theme" if dark else "Use dark theme")

    def update_window_state(self) -> None:
        maximized = self.window().isMaximized()
        self.maximize_button.setText("❐" if maximized else "□")
        self.maximize_button.setToolTip("Restore" if maximized else "Maximize")

    def toggle_maximized(self) -> None:
        if self.window().isMaximized():
            self.window().showNormal()
        else:
            self.window().showMaximized()
        self.update_window_state()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.window().isMaximized():
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


class ResizeHandle(QWidget):
    def __init__(self, edges: Qt.Edge, parent: QWidget) -> None:
        super().__init__(parent)
        self.edges = edges
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            same_direction = edges in (
                Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
                Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
            )
            cursor = Qt.CursorShape.SizeFDiagCursor if same_direction else Qt.CursorShape.SizeBDiagCursor
        elif horizontal:
            cursor = Qt.CursorShape.SizeHorCursor
        else:
            cursor = Qt.CursorShape.SizeVerCursor
        self.setCursor(cursor)
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemResize(self.edges):
                event.accept()
                return
        super().mousePressEvent(event)


RESIZE_EDGES = (
    Qt.Edge.LeftEdge,
    Qt.Edge.RightEdge,
    Qt.Edge.TopEdge,
    Qt.Edge.BottomEdge,
    Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
    Qt.Edge.RightEdge | Qt.Edge.TopEdge,
    Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
    Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
)


def create_resize_handles(parent: QWidget) -> list[ResizeHandle]:
    return [ResizeHandle(edges, parent) for edges in RESIZE_EDGES]


def position_resize_handles(
    window: QWidget,
    handles: list[ResizeHandle],
) -> None:
    width, height = window.width(), window.height()
    border, corner = 5, 10
    full_surface = window.isMaximized() or window.isFullScreen()
    for handle in handles:
        edges = handle.edges
        if full_surface:
            handle.hide()
            continue
        handle.show()
        if edges == Qt.Edge.LeftEdge:
            handle.setGeometry(0, corner, border, max(0, height - corner * 2))
        elif edges == Qt.Edge.RightEdge:
            handle.setGeometry(width - border, corner, border, max(0, height - corner * 2))
        elif edges == Qt.Edge.TopEdge:
            handle.setGeometry(corner, 0, max(0, width - corner * 2), border)
        elif edges == Qt.Edge.BottomEdge:
            handle.setGeometry(corner, height - border, max(0, width - corner * 2), border)
        elif edges == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge):
            handle.setGeometry(0, 0, corner, corner)
        elif edges == (Qt.Edge.RightEdge | Qt.Edge.TopEdge):
            handle.setGeometry(width - corner, 0, corner, corner)
        elif edges == (Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
            handle.setGeometry(0, height - corner, corner, corner)
        else:
            handle.setGeometry(width - corner, height - corner, corner, corner)
        handle.raise_()


class FramelessWindow(QMainWindow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._title_bar: TitleBar | None = None
        self._resize_handles = create_resize_handles(self)

    def set_title_bar(self, title_bar: TitleBar) -> None:
        self._title_bar = title_bar

    def add_resize_grip(self) -> None:
        self.statusBar().addPermanentWidget(QSizeGrip(self), 0)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        position_resize_handles(self, self._resize_handles)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self._title_bar:
                self._title_bar.update_window_state()
            position_resize_handles(self, self._resize_handles)

    def _position_resize_handles(self) -> None:
        position_resize_handles(self, self._resize_handles)
