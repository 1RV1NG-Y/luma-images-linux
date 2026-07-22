from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


THEMES: dict[str, dict[str, str]] = {
    "light": {
        "window": "#f7f7f5",
        "surface": "#fbfbfa",
        "sidebar": "#f0f0ed",
        "inspector": "#f2f2ef",
        "control": "#efefec",
        "input": "#fbfbfa",
        "border": "#dcdcd7",
        "border_strong": "#c8c8c3",
        "hover": "#e5e5e1",
        "text": "#202124",
        "secondary": "#555752",
        "muted": "#858680",
        "faint": "#a1a29d",
        "accent": "#1684f8",
        "accent_hover": "#0877e9",
        "accent_soft": "#dcecff",
        "menu": "#ffffff",
        "danger": "#d94b45",
        "placeholder": "#e7e7e2",
    },
    "dark": {
        "window": "#151615",
        "surface": "#191a19",
        "sidebar": "#1d1e1d",
        "inspector": "#1b1c1b",
        "control": "#252625",
        "input": "#222322",
        "border": "#303230",
        "border_strong": "#414340",
        "hover": "#292b29",
        "text": "#e7e8e5",
        "secondary": "#c1c2be",
        "muted": "#90928d",
        "faint": "#737570",
        "accent": "#4c9fff",
        "accent_hover": "#67adff",
        "accent_soft": "#24384d",
        "menu": "#252725",
        "danger": "#e45a54",
        "placeholder": "#292b29",
    },
}


STYLESHEET = r"""
* {
    font-family: "Inter", "SF Pro Display", "SF Pro Text", "Noto Sans", sans-serif;
    color: %(text)s;
    font-size: 13px;
}
QMainWindow, QDialog, QWidget#AppRoot {
    background: %(window)s;
}
QFrame#TitleBar, QFrame#TopBar {
    background: %(surface)s;
    border-bottom: 1px solid %(border)s;
}
QLabel#BrandMark {
    background: #242526;
    color: white;
    border-radius: 7px;
    font-size: 14px;
    font-weight: 700;
}
QLabel#WorkspaceLabel {
    color: %(secondary)s;
    font-size: 12px;
    font-weight: 600;
}
QToolButton#WindowButton, QToolButton#CloseButton {
    color: %(secondary)s;
    background: transparent;
    border: none;
    border-radius: 0;
    font-size: 15px;
    padding: 0;
}
QToolButton#WindowButton:hover { background: %(hover)s; color: %(text)s; }
QToolButton#CloseButton:hover { background: %(danger)s; color: white; }
QToolButton#ThemeButton, QToolButton#PanelButton {
    color: %(secondary)s;
    background: transparent;
    border: none;
    border-radius: 7px;
    font-size: 16px;
    padding: 4px 7px;
}
QToolButton#ThemeButton:hover, QToolButton#PanelButton:hover { background: %(hover)s; color: %(text)s; }
QToolButton#PanelButton:checked { background: %(accent_soft)s; color: %(accent)s; }
QFrame#Sidebar {
    background: %(sidebar)s;
    border-right: 1px solid %(border)s;
}
QLabel#SectionLabel {
    color: %(muted)s;
    font-size: 11px;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
QLineEdit#FolderSearch {
    background: %(control)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 6px 9px;
    margin: 0 4px 5px 4px;
    selection-background-color: %(accent)s;
}
QLineEdit#FolderSearch:focus {
    background: %(input)s;
    border-color: %(accent)s;
}
QWidget#FolderTreeRow { background: transparent; }
QToolButton#FolderDisclosure {
    background: transparent;
    border: none;
    border-radius: 5px;
    color: %(muted)s;
    font-size: 15px;
    padding: 0;
}
QToolButton#FolderDisclosure:hover {
    background: %(hover)s;
    color: %(text)s;
}
QToolButton#FolderDisclosure:disabled {
    background: transparent;
    color: transparent;
}

QPushButton#NavButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    color: %(secondary)s;
    text-align: left;
    padding: 7px 10px;
    min-height: 20px;
}
QPushButton#NavButton:hover { background: %(hover)s; color: %(text)s; }
QPushButton#NavButton:checked {
    background: %(accent_soft)s;
    color: %(accent)s;
    font-weight: 600;
}
QPushButton#NavButton[offline="true"] { color: %(danger)s; }
QToolButton#SectionAction {
    color: %(muted)s;
    background: transparent;
    border: none;
    border-radius: 7px;
    font-size: 18px;
    padding: 0;
}
QToolButton#SectionAction:hover { background: %(hover)s; color: %(text)s; }
QPushButton#AddFolderButton {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: 9px;
    padding: 8px 10px;
    color: %(secondary)s;
    font-weight: 600;
    text-align: left;
}
QPushButton#AddFolderButton:hover { background: %(input)s; border-color: %(border_strong)s; }
QWidget#GalleryPane { background: %(surface)s; }
QLabel#PageTitle { font-size: 26px; font-weight: 700; color: %(text)s; }
QLabel#PageSubtitle { color: %(muted)s; }
QLineEdit#SearchField {
    background: %(control)s;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 7px 12px;
    selection-background-color: %(accent)s;
}
QLineEdit#SearchField:focus { background: %(input)s; border-color: %(accent)s; }
QComboBox {
    background: %(window)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 18px;
}
QComboBox:hover { background: %(input)s; border-color: %(border_strong)s; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: %(menu)s;
    border: 1px solid %(border_strong)s;
    selection-background-color: %(accent_soft)s;
    selection-color: %(accent)s;
    outline: none;
}
QPushButton#PrimaryButton {
    background: %(accent)s;
    color: white;
    border: none;
    border-radius: 9px;
    padding: 8px 14px;
    font-weight: 650;
}
QPushButton#PrimaryButton:hover { background: %(accent_hover)s; }
QPushButton#SecondaryButton, QToolButton#SecondaryButton {
    background: %(window)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 6px 10px;
    color: %(secondary)s;
    font-weight: 550;
}
QPushButton#SecondaryButton:hover, QToolButton#SecondaryButton:hover {
    background: %(input)s;
    border-color: %(border_strong)s;
}
QListWidget#GalleryGrid {
    background: %(surface)s;
    border: none;
    outline: none;
    padding: 4px;
}
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 3px;
}
QScrollBar::handle:vertical {
    background: %(border_strong)s;
    border-radius: 4px;
    min-height: 36px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover { background: %(muted)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QFrame#Inspector {
    background: %(inspector)s;
    border-left: 1px solid %(border)s;
}
QLabel#InspectorTitle { font-size: 16px; font-weight: 700; color: %(text)s; }
QLabel#MutedLabel { color: %(muted)s; }
QLabel#Preview {
    background: %(placeholder)s;
    border-radius: 10px;
}
QLineEdit#Editor, QTextEdit#Editor {
    background: %(input)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 7px 9px;
    selection-background-color: %(accent)s;
}
QLineEdit#Editor:focus, QTextEdit#Editor:focus { border-color: %(accent)s; }
QLabel#FieldLabel { color: %(muted)s; font-size: 11px; font-weight: 650; }
QToolButton#StarButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: %(faint)s;
    font-size: 20px;
    padding: 1px;
}
QToolButton#StarButton:checked { color: #f3ad27; }
QToolButton#StarButton:hover { background: %(hover)s; }
QFrame#Hairline { background: %(border)s; max-height: 1px; }
QLabel#InfoKey { color: %(muted)s; }
QLabel#InfoValue { color: %(secondary)s; }
QLabel#EmptyIcon {
    color: %(secondary)s;
    background: %(control)s;
    border-radius: 20px;
    font-size: 44px;
    padding: 14px;
}
QLabel#EmptyTitle, QLabel#NoResultsTitle { color: %(text)s; font-size: 20px; font-weight: 700; }
QLabel#EmptyBody { color: %(muted)s; }
QLabel#NoResultsIcon { color: %(faint)s; font-size: 34px; }
QMenu {
    background: %(menu)s;
    border: 1px solid %(border_strong)s;
    border-radius: 9px;
    padding: 6px;
}
QMenu::item { padding: 7px 24px 7px 10px; border-radius: 6px; }
QMenu::item:selected { background: %(accent_soft)s; color: %(accent)s; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 5px 7px; }
QDialog#Viewer { background: #111312; }
QWidget#ViewerChrome, QFrame#ViewerFilmstrip { background: #171918; }
QLabel#ViewerName { color: #eeeeeb; font-weight: 600; }
QLabel#ViewerZoom { color: #858782; font-size: 11px; min-width: 36px; }
QWidget#ViewerImage { background: #111312; }
QFrame#ViewerInfo { background: #1c1e1d; border-left: 1px solid #303230; }
QFrame#ViewerInfo QLabel { color: #d3d4d0; }
QFrame#ViewerInfo QLabel#MutedLabel { color: #858782; }
QToolButton#ViewerButton {
    color: #e7e7e4;
    background: transparent;
    border: none;
    border-radius: 8px;
    font-size: 18px;
    padding: 6px;
}
QToolButton#ViewerButton:hover { background: #303230; }
QToolButton#ViewerWindowButton, QToolButton#ViewerCloseButton {
    color: #e7e7e4;
    background: transparent;
    border: none;
    border-radius: 0;
    font-size: 16px;
    padding: 0;
}
QToolButton#ViewerWindowButton:hover { background: #303230; }
QToolButton#ViewerCloseButton:hover { background: #d23f3f; color: white; }
QListWidget#Filmstrip { background: #171918; border: none; outline: none; }
QStatusBar { background: %(surface)s; color: %(muted)s; border-top: 1px solid %(border)s; }
QSizeGrip { background: transparent; width: 12px; height: 12px; }
QToolTip { background: #2c2d2c; color: white; border: none; padding: 5px; }
"""


def normalized_theme(name: str | None) -> str:
    return name if name in THEMES else "light"


def build_stylesheet(name: str) -> str:
    return STYLESHEET % THEMES[normalized_theme(name)]


def apply_theme(application: QApplication, name: str) -> str:
    theme_name = normalized_theme(name)
    colors = THEMES[theme_name]
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["input"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["control"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["window"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["muted"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2c2d2c"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#ffffff"))
    application.setPalette(palette)
    application.setStyleSheet(build_stylesheet(theme_name))
    application.setProperty("theme", theme_name)
    return theme_name
