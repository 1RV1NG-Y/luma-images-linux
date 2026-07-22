from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QCursor, QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .database import Database
from .file_operations import FileOperationError, FileOperations
from .models import Album, ImageRecord, Library
from .scanner import ScanResult, ScanThread
from .styles import apply_theme, normalized_theme
from .thumbnails import ThumbnailManager
from .viewer import ImageViewer
from .widgets import (
    ROLE_DATE,
    ROLE_FAVORITE,
    ROLE_IMAGE_ID,
    ROLE_MISSING,
    ROLE_RECORD,
    AlbumButton,
    EmptyState,
    GalleryDelegate,
    FolderTreeRow,
    GalleryList,
    InspectorPanel,
    NavButton,
    format_date,
)
from .window_chrome import FramelessWindow, TitleBar


class MainWindow(FramelessWindow):
    def __init__(self, database_path: Path, cache_dir: Path) -> None:
        super().__init__()
        self.theme = normalized_theme(str(QSettings().value("appearance/theme", "light")))
        self.inspector_visible = QSettings().value("appearance/inspector-visible", True, type=bool)
        stored_expanded = QSettings().value("folders/expanded", [])
        if isinstance(stored_expanded, str):
            self.expanded_folders = {stored_expanded} if stored_expanded else set()
        else:
            self.expanded_folders = {str(path) for path in stored_expanded}
        application = QApplication.instance()
        if isinstance(application, QApplication):
            apply_theme(application, self.theme)
        self.database_path = database_path
        self.cache_dir = cache_dir
        self.database = Database(database_path)
        self.file_operations = FileOperations(self.database)
        self.thumbnail_manager = ThumbnailManager(cache_dir, self)
        self.thumbnail_manager.ready.connect(self._thumbnail_ready)
        self._thumbnail_timer = QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.setInterval(45)
        self._thumbnail_timer.timeout.connect(self._load_visible_thumbnails)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.reload_gallery)
        self._folder_search_timer = QTimer(self)
        self._folder_search_timer.setSingleShot(True)
        self._folder_search_timer.setInterval(140)
        self._folder_search_timer.timeout.connect(self.reload_sidebar)
        self._startup_scan_timer = QTimer(self)
        self._startup_scan_timer.setSingleShot(True)
        self._startup_scan_timer.setInterval(350)
        self._startup_scan_timer.timeout.connect(self.rescan_all)

        self.mode = "all"
        self.mode_value: str | None = None
        self.sort_mode = "date_desc"
        self.current_images: list[ImageRecord] = []
        self._items_by_id: dict[str, QListWidgetItem] = {}
        self._library_buttons: list[NavButton] = []
        self._folder_rows_by_path: dict[str, FolderTreeRow] = {}
        self._album_buttons: list[AlbumButton] = []
        self._scan_queue: list[Library] = []
        self._scan_thread: ScanThread | None = None
        self._last_scan_result: ScanResult | None = None

        self.setWindowTitle("Luma")
        self.setMinimumSize(1040, 680)
        self.resize(1440, 880)
        self._build_interface()
        self._connect_signals()
        self._restore_window_state()
        self.reload_sidebar()
        self.reload_gallery()
        if self.database.libraries():
            self._startup_scan_timer.start()

    # Interface ---------------------------------------------------------
    def _build_interface(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.title_bar = self._build_top_bar()
        self.set_title_bar(self.title_bar)
        root_layout.addWidget(self.title_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_gallery_pane(), 1)
        self.inspector = InspectorPanel()
        self.inspector.setVisible(self.inspector_visible)
        body_layout.addWidget(self.inspector)
        root_layout.addWidget(body, 1)
        self.setCentralWidget(root)
        self.statusBar().setSizeGripEnabled(False)
        self.add_resize_grip()
        self.statusBar().showMessage("Ready")

    def _build_top_bar(self) -> TitleBar:
        title_bar = TitleBar()
        title_bar.set_theme(self.theme)
        title_bar.theme_toggle_requested.connect(self.toggle_theme)
        title_bar.set_information_visible(self.inspector_visible)
        title_bar.information_toggle_requested.connect(self.toggle_inspector)
        self.rescan_button = QToolButton()
        self.rescan_button.setObjectName("SecondaryButton")
        self.rescan_button.setText("↻  Rescan")
        self.rescan_button.setToolTip("Reconcile registered folders")
        self.rescan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        title_bar.add_action(self.rescan_button)
        self.top_add_folder = QPushButton("＋  Add folder")
        self.top_add_folder.setObjectName("PrimaryButton")
        self.top_add_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        title_bar.add_action(self.top_add_folder)
        return title_bar

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(226)
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(10, 16, 10, 12)
        outer.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self.sidebar_layout = QVBoxLayout(content)
        self.sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_layout.setSpacing(2)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.sidebar_layout.addWidget(self._section_label("Library"))
        self.all_button = NavButton("All Photos", "▧")
        self.favorite_button = NavButton("Favorites", "★")
        self.missing_button = NavButton("Missing", "!")
        self.all_button.setChecked(True)
        for button in (self.all_button, self.favorite_button, self.missing_button):
            self.nav_group.addButton(button)
            self.sidebar_layout.addWidget(button)

        self.sidebar_layout.addSpacing(12)
        folder_header, self.folder_search_toggle = self._section_header("Folders", action="⌕")
        self.folder_search_toggle.setToolTip("Search indexed folders")
        self.folder_search_toggle.clicked.connect(self.toggle_folder_search)
        self.sidebar_layout.addWidget(folder_header)
        self.folder_search = QLineEdit()
        self.folder_search.setObjectName("FolderSearch")
        self.folder_search.setPlaceholderText("Find a folder")
        self.folder_search.setClearButtonEnabled(True)
        self.folder_search.setVisible(False)
        self.folder_search.textChanged.connect(
            lambda: self._folder_search_timer.start()
        )
        self.sidebar_layout.addWidget(self.folder_search)
        self.library_container = QWidget()
        self.library_container.setStyleSheet("background: transparent;")
        self.library_layout = QVBoxLayout(self.library_container)
        self.library_layout.setContentsMargins(0, 0, 0, 0)
        self.library_layout.setSpacing(2)
        self.sidebar_layout.addWidget(self.library_container)

        self.sidebar_layout.addSpacing(12)
        album_header, album_action = self._section_header("Albums", action="＋")
        album_action.clicked.connect(self.create_album)
        self.sidebar_layout.addWidget(album_header)
        self.album_container = QWidget()
        self.album_container.setStyleSheet("background: transparent;")
        self.album_layout = QVBoxLayout(self.album_container)
        self.album_layout.setContentsMargins(0, 0, 0, 0)
        self.album_layout.setSpacing(2)
        self.sidebar_layout.addWidget(self.album_container)
        self.sidebar_layout.addStretch()

        add_folder = QPushButton("＋  Add picture folder")
        add_folder.setObjectName("AddFolderButton")
        add_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        add_folder.clicked.connect(self.add_folder)
        outer.addWidget(add_folder)
        return sidebar

    def _build_gallery_pane(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("GalleryPane")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(22, 20, 22, 0)
        layout.setSpacing(12)

        heading_row = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(2)
        self.page_title = QLabel("All Photos")
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = QLabel("0 photos")
        self.page_subtitle.setObjectName("PageSubtitle")
        heading_text.addWidget(self.page_title)
        heading_text.addWidget(self.page_subtitle)
        heading_row.addLayout(heading_text)
        heading_row.addStretch()
        layout.addLayout(heading_row)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search_field = QLineEdit()
        self.search_field.setObjectName("SearchField")
        self.search_field.setPlaceholderText("Search photos, paths, titles, and tags")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setMinimumWidth(260)
        toolbar.addWidget(self.search_field, 1)
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Newest first", "date_desc")
        self.sort_combo.addItem("Oldest first", "date_asc")
        self.sort_combo.addItem("Name", "name")
        self.sort_combo.addItem("Highest rated", "rating")
        self.sort_combo.addItem("Largest files", "size")
        toolbar.addWidget(self.sort_combo)
        layout.addLayout(toolbar)

        self.content_stack = QStackedWidget()
        self.empty_state = EmptyState()
        self.content_stack.addWidget(self.empty_state)
        self.gallery = GalleryList()
        self.gallery.setItemDelegate(GalleryDelegate(self.gallery))
        self.gallery.setMouseTracking(True)
        self.content_stack.addWidget(self.gallery)
        self.no_results_page = self._build_no_results()
        self.content_stack.addWidget(self.no_results_page)
        layout.addWidget(self.content_stack, 1)
        return pane

    def _build_no_results(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("⌕")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setObjectName("NoResultsIcon")
        self.no_results_title = QLabel("No photos here")
        self.no_results_title.setObjectName("NoResultsTitle")
        self.no_results_body = QLabel("Try another view or choose a different search.")
        self.no_results_body.setObjectName("MutedLabel")
        self.no_results_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        layout.addWidget(self.no_results_title, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.no_results_body, 0, Qt.AlignmentFlag.AlignHCenter)
        return page

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("SectionLabel")
        label.setContentsMargins(9, 0, 0, 3)
        return label

    def _section_header(self, text: str, action: str | None = None) -> tuple[QWidget, QToolButton]:
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 2, 2)
        layout.addWidget(self._section_label(text))
        layout.addStretch()
        button = QToolButton()
        button.setObjectName("SectionAction")
        button.setFixedSize(25, 25)
        button.setText(action or "")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setVisible(bool(action))
        layout.addWidget(button)
        return widget, button

    def toggle_inspector(self) -> None:
        self.inspector_visible = not self.inspector_visible
        self.inspector.setVisible(self.inspector_visible)
        self.title_bar.set_information_visible(self.inspector_visible)
        QSettings().setValue("appearance/inspector-visible", self.inspector_visible)
        self._schedule_visible_thumbnails()

    def toggle_theme(self) -> None:
        self.theme = "dark" if self.theme == "light" else "light"
        QSettings().setValue("appearance/theme", self.theme)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            apply_theme(application, self.theme)
        self.title_bar.set_theme(self.theme)
        self.gallery.viewport().update()

    def toggle_folder_search(self) -> None:
        showing = self.folder_search.isHidden()
        self.folder_search.setVisible(showing)
        self.folder_search_toggle.setText("×" if showing else "⌕")
        self.folder_search_toggle.setToolTip(
            "Close folder search" if showing else "Search indexed folders"
        )
        if showing:
            self.folder_search.setFocus()
        else:
            self.folder_search.clear()
        self.reload_sidebar()

    def _toggle_folder_expansion(self, folder_path: str) -> None:
        if folder_path in self.expanded_folders:
            self.expanded_folders.remove(folder_path)
        else:
            self.expanded_folders.add(folder_path)
        self._save_expanded_folders()
        self.reload_sidebar()

    def _save_expanded_folders(self) -> None:
        QSettings().setValue("folders/expanded", sorted(self.expanded_folders))

    def _connect_signals(self) -> None:
        self.all_button.clicked.connect(lambda: self.select_mode("all", None, "All Photos", self.all_button))
        self.favorite_button.clicked.connect(lambda: self.select_mode("favorites", None, "Favorites", self.favorite_button))
        self.missing_button.clicked.connect(lambda: self.select_mode("missing", None, "Missing", self.missing_button))
        self.top_add_folder.clicked.connect(self.add_folder)
        self.rescan_button.clicked.connect(self.rescan_all)
        self.empty_state.add_folder_requested.connect(self.add_folder)
        self.search_field.textChanged.connect(lambda: self._search_timer.start())
        self.sort_combo.currentIndexChanged.connect(self._sort_changed)
        self.gallery.itemSelectionChanged.connect(self._selection_changed)
        self.gallery.itemDoubleClicked.connect(lambda item: self.open_viewer(str(item.data(ROLE_IMAGE_ID))))
        self.gallery.customContextMenuRequested.connect(self._gallery_context_menu)
        self.gallery.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnails)
        self.gallery.viewport_changed.connect(self._schedule_visible_thumbnails)
        self.inspector.save_requested.connect(self.save_image_details)
        self.inspector.rename_requested.connect(self.rename_file)
        self.inspector.reveal_requested.connect(self.reveal_file)
        self.inspector.add_to_album_requested.connect(self.show_album_picker)
        self.inspector.bulk_favorite_requested.connect(self.set_favorite)
        self.inspector_shortcut = QShortcut(QKeySequence("Ctrl+I"), self)
        self.inspector_shortcut.activated.connect(self.toggle_inspector)

    # Navigation and data ----------------------------------------------
    def _folder_hierarchy(self, library: Library) -> dict[str, list[str]]:
        root = Path(library.path)
        folders = {str(root)}
        for indexed_path in self.database.indexed_folders(library.id):
            folder = Path(indexed_path)
            try:
                relative = folder.relative_to(root)
            except ValueError:
                continue
            for depth in range(1, len(relative.parts) + 1):
                folders.add(str(root.joinpath(*relative.parts[:depth])))
        children = {folder: [] for folder in folders}
        for folder in folders:
            if folder == str(root):
                continue
            parent = str(Path(folder).parent)
            if parent in children:
                children[parent].append(folder)
        for paths in children.values():
            paths.sort(key=lambda path: Path(path).name.casefold())
        return children

    def _make_folder_button(
        self,
        library: Library,
        folder_path: str,
        display_label: str | None = None,
    ) -> NavButton:
        is_root = folder_path == library.path
        offline = is_root and not library.available
        label = display_label or (library.name if is_root else Path(folder_path).name)
        if offline:
            label = f"{label} — Offline"
        button = NavButton(label, "!" if offline else "⌂" if is_root else "⌑")
        button.setProperty("offline", offline)
        button.setProperty("library_id", library.id)
        button.setProperty("folder_path", folder_path)
        if is_root:
            status = (
                "Offline · mount or unlock the drive, then Rescan"
                if offline
                else "Connected · includes nested folders"
            )
            button.setToolTip(
                f"{status}\n{library.path}\nRight-click for reconnect options"
            )
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda position, item=library, nav=button: self._library_context_menu(
                    item, nav.mapToGlobal(position)
                )
            )
        else:
            button.setToolTip(
                f"Filesystem subfolder · includes nested folders\n{folder_path}"
            )
        button.clicked.connect(
            lambda _checked=False, item=library, path=folder_path, nav=button: self._select_folder(
                item, path, nav
            )
        )
        self.nav_group.addButton(button)
        self._library_buttons.append(button)
        return button

    def _select_folder(
        self,
        library: Library,
        folder_path: str,
        button: NavButton,
    ) -> None:
        if self.folder_search.text().strip():
            self._expand_folder_ancestors(library, folder_path)
        if folder_path == library.path:
            self.select_mode("folder", library.id, library.name, button)
            return
        relative = Path(folder_path).relative_to(Path(library.path))
        self.select_mode(
            "folder_path",
            folder_path,
            " / ".join(relative.parts),
            button,
        )

    def _expand_folder_ancestors(self, library: Library, folder_path: str) -> None:
        root = Path(library.path)
        try:
            relative = Path(folder_path).relative_to(root)
        except ValueError:
            return
        before = len(self.expanded_folders)
        self.expanded_folders.add(str(root))
        current = root
        for part in relative.parts[:-1]:
            current /= part
            self.expanded_folders.add(str(current))
        if len(self.expanded_folders) != before:
            self._save_expanded_folders()

    def _append_folder_row(
        self,
        library: Library,
        hierarchy: dict[str, list[str]],
        folder_path: str,
        depth: int,
    ) -> None:
        children = hierarchy.get(folder_path, [])
        expanded = folder_path in self.expanded_folders
        button = self._make_folder_button(library, folder_path)
        row = FolderTreeRow(
            button,
            folder_path,
            depth=depth,
            has_children=bool(children),
            expanded=expanded,
        )
        row.disclosure_toggled.connect(self._toggle_folder_expansion)
        self.library_layout.addWidget(row)
        self._folder_rows_by_path[folder_path] = row
        if expanded:
            for child in children:
                self._append_folder_row(library, hierarchy, child, depth + 1)

    def reload_sidebar(self) -> None:
        self._clear_layout(self.library_layout, self._library_buttons)
        self._folder_rows_by_path.clear()
        libraries = self.database.libraries()
        query = self.folder_search.text().strip().casefold()
        if query:
            matches = 0
            for library in libraries:
                hierarchy = self._folder_hierarchy(library)
                root = Path(library.path)
                ordered = sorted(
                    hierarchy,
                    key=lambda path: tuple(
                        part.casefold()
                        for part in Path(path).relative_to(root).parts
                    ),
                )
                for folder_path in ordered:
                    relative = Path(folder_path).relative_to(root)
                    name = library.name if not relative.parts else Path(folder_path).name
                    if query not in name.casefold():
                        continue
                    context = (
                        library.name
                        if not relative.parts
                        else f"{library.name} / {' / '.join(relative.parts)}"
                    )
                    parent = Path(folder_path).parent
                    parent_label = (
                        library.name if parent == root else parent.name
                    )
                    result_label = (
                        name if not relative.parts else f"{name}  ·  {parent_label}"
                    )
                    button = self._make_folder_button(
                        library,
                        folder_path,
                        display_label=result_label,
                    )
                    button.setToolTip(f"{context}\n{folder_path}")
                    row = FolderTreeRow(
                        button,
                        folder_path,
                        depth=0,
                        has_children=False,
                        expanded=False,
                    )
                    self.library_layout.addWidget(row)
                    self._folder_rows_by_path[folder_path] = row
                    matches += 1
            if not matches:
                empty = QLabel("No matching folders")
                empty.setObjectName("MutedLabel")
                empty.setContentsMargins(10, 6, 0, 6)
                self.library_layout.addWidget(empty)
        else:
            for library in libraries:
                hierarchy = self._folder_hierarchy(library)
                self._append_folder_row(
                    library,
                    hierarchy,
                    library.path,
                    depth=0,
                )
        self._clear_layout(self.album_layout, self._album_buttons)
        albums = self.database.albums()
        if not albums:
            label = QLabel("No albums yet")
            label.setObjectName("MutedLabel")
            label.setContentsMargins(10, 5, 0, 5)
            self.album_layout.addWidget(label)
        for album in albums:
            button = AlbumButton(album)
            button.clicked.connect(
                lambda _checked=False, item=album, nav=button: self.select_mode("album", item.id, item.name, nav)
            )
            button.images_dropped.connect(self.add_images_to_album)
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda position, item=album, nav=button: self._album_context_menu(item, nav.mapToGlobal(position))
            )
            self.nav_group.addButton(button)
            self.album_layout.addWidget(button)
            self._album_buttons.append(button)

        self._restore_checked_navigation()

    def _clear_layout(self, layout: QVBoxLayout, tracked: list) -> None:
        for button in tracked:
            self.nav_group.removeButton(button)
        tracked.clear()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.deleteLater()

    def _restore_checked_navigation(self) -> None:
        target = None
        if self.mode == "all":
            target = self.all_button
        elif self.mode == "favorites":
            target = self.favorite_button
        elif self.mode == "missing":
            target = self.missing_button
        elif self.mode == "folder":
            library = self.database.library(self.mode_value or "")
            if library:
                target = next(
                    (
                        button
                        for button in self._library_buttons
                        if button.property("library_id") == library.id
                        and button.property("folder_path") == library.path
                    ),
                    None,
                )
        elif self.mode == "folder_path":
            target = next(
                (button for button in self._library_buttons if button.property("folder_path") == self.mode_value),
                None,
            )
        elif self.mode == "album":
            target = next((button for button in self._album_buttons if button.album.id == self.mode_value), None)
        if target:
            target.setChecked(True)
        elif self.mode == "album" or (
            self.mode == "folder" and not self.folder_search.text().strip()
        ):
            self.mode, self.mode_value = "all", None
            self.page_title.setText("All Photos")
            self.all_button.setChecked(True)

    def select_mode(self, mode: str, value: str | None, title: str, button: QPushButton) -> None:
        self.mode = mode
        self.mode_value = value
        button.setChecked(True)
        self.page_title.setText(title)
        self.reload_gallery()

    def reload_gallery(self, *_args) -> None:
        selected_ids = {str(item.data(ROLE_IMAGE_ID)) for item in self.gallery.selectedItems()}
        self.current_images = self.database.images(
            mode=self.mode,
            value=self.mode_value,
            search=self.search_field.text() if hasattr(self, "search_field") else "",
            sort=self.sort_mode,
        )
        self.gallery.clear()
        self._items_by_id.clear()
        for image in self.current_images:
            item = QListWidgetItem(image.display_name)
            item.setData(ROLE_IMAGE_ID, image.id)
            item.setData(ROLE_RECORD, image)
            item.setData(ROLE_MISSING, image.missing)
            item.setData(ROLE_FAVORITE, image.favorite)
            item.setData(ROLE_DATE, format_date(image.date_taken))
            item.setToolTip(image.path)
            self.gallery.addItem(item)
            self._items_by_id[image.id] = item
            if image.id in selected_ids:
                item.setSelected(True)

        self._schedule_visible_thumbnails()

        count = len(self.current_images)
        label = "photo" if count == 1 else "photos"
        self.page_subtitle.setText(f"{count:,} {label} · originals stay on disk")
        if not self.database.libraries():
            self.content_stack.setCurrentIndex(0)
        elif count:
            self.content_stack.setCurrentIndex(1)
        else:
            self.content_stack.setCurrentIndex(2)
            self.no_results_title.setText("No matching photos" if self.search_field.text().strip() else "No photos here")
        if not self.gallery.selectedItems():
            self.inspector.clear()

    def _schedule_visible_thumbnails(self, *_args) -> None:
        self._thumbnail_timer.start()

    def _load_visible_thumbnails(self) -> None:
        if self.content_stack.currentWidget() is not self.gallery or not self.current_images:
            return
        grid_size = self.gallery.gridSize()
        viewport = self.gallery.viewport()
        columns = max(1, viewport.width() // max(1, grid_size.width()))
        first_row = max(0, self.gallery.verticalScrollBar().value() // max(1, grid_size.height()) - 1)
        visible_rows = viewport.height() // max(1, grid_size.height()) + 3
        first_index = first_row * columns
        last_index = min(len(self.current_images), (first_row + visible_rows) * columns)
        for index in range(first_index, last_index):
            image = self.current_images[index]
            if image.missing:
                continue
            item = self._items_by_id.get(image.id)
            if item is None:
                continue
            pixmap = item.data(Qt.ItemDataRole.DecorationRole)
            thumbnail = self.thumbnail_manager.path_for(image.id)
            if (not isinstance(pixmap, QPixmap) or pixmap.isNull()) and thumbnail.exists():
                item.setData(Qt.ItemDataRole.DecorationRole, QPixmap(str(thumbnail)))
            self.thumbnail_manager.request(image.id, image.path)

    def _sort_changed(self) -> None:
        self.sort_mode = str(self.sort_combo.currentData())
        self.reload_gallery()

    def _selection_changed(self) -> None:
        selected = self.gallery.selectedItems()
        if not selected:
            self.inspector.clear()
            return
        image_ids = [str(item.data(ROLE_IMAGE_ID)) for item in selected]
        if len(image_ids) > 1:
            self.inspector.set_multiple(image_ids)
            return
        image = self.database.image(image_ids[0])
        if not image:
            self.inspector.clear()
            return
        thumbnail = self.thumbnail_manager.path_for(image.id)
        self.inspector.set_image(
            image,
            self.database.tags_for_image(image.id),
            self.database.albums_for_image(image.id),
            str(thumbnail) if thumbnail.exists() else None,
        )
        if not image.missing:
            self.thumbnail_manager.request(image.id, image.path)

    def _thumbnail_ready(self, image_id: str, path: str) -> None:
        item = self._items_by_id.get(image_id)
        if item:
            item.setData(Qt.ItemDataRole.DecorationRole, QPixmap(path))
        self.inspector.set_thumbnail(image_id, path)

    # Folders and scans -------------------------------------------------
    def add_folder(self) -> None:
        initial = str(Path.home() / "Pictures")
        path = QFileDialog.getExistingDirectory(self, "Choose a picture folder", initial)
        if not path:
            return
        library = self.database.add_library(path)
        self.reload_sidebar()
        self.select_mode("folder", library.id, library.name, self._button_for_library(library.id) or self.all_button)
        self.queue_scan([library])

    def _button_for_library(self, library_id: str) -> NavButton | None:
        library = self.database.library(library_id)
        if not library:
            return None
        return next(
            (
                button
                for button in self._library_buttons
                if button.property("library_id") == library_id
                and button.property("folder_path") == library.path
            ),
            None,
        )

    def _library_context_menu(self, library: Library, global_position) -> None:
        current = self.database.library(library.id)
        if current is None:
            return
        menu = QMenu(self)
        status = menu.addAction("Connected" if current.available else "Offline — mount or unlock the drive")
        status.setEnabled(False)
        menu.addSeparator()
        rescan = menu.addAction("Rescan this folder")
        locate = menu.addAction("Locate or reconnect folder…")
        selected = menu.exec(global_position)
        if selected == rescan:
            refreshed = self.database.library(current.id)
            if refreshed:
                self.queue_scan([refreshed])
        elif selected == locate:
            self.locate_library(current)

    def locate_library(self, library: Library) -> None:
        old_root = Path(library.path)
        initial = old_root if old_root.is_dir() else old_root.parent
        if not initial.is_dir():
            initial = Path.home()
        path = QFileDialog.getExistingDirectory(
            self,
            f"Locate {library.name}",
            str(initial),
        )
        if not path:
            return
        selected_relative: Path | None = None
        if self.mode == "folder_path" and self.mode_value:
            try:
                selected_relative = Path(self.mode_value).relative_to(old_root)
            except ValueError:
                pass
        try:
            updated = self.database.relink_library(library.id, path)
        except (sqlite3.IntegrityError, ValueError) as error:
            QMessageBox.warning(self, "Could not reconnect folder", str(error))
            return
        if selected_relative is not None:
            self.mode_value = str(Path(updated.path) / selected_relative)
        self.reload_sidebar()
        self.reload_gallery()
        self.queue_scan([updated])
        self.statusBar().showMessage(f"Reconnected {updated.name} · rescanning…")

    def rescan_all(self) -> None:
        libraries = self.database.libraries()
        if not libraries:
            self.add_folder()
            return
        self.queue_scan(libraries)

    def queue_scan(self, libraries: list[Library]) -> None:
        queued_ids = {library.id for library in self._scan_queue}
        active_id = self._scan_thread.library.id if self._scan_thread else None
        self._scan_queue.extend(
            library for library in libraries if library.id not in queued_ids and library.id != active_id
        )
        self._start_next_scan()

    def _start_next_scan(self) -> None:
        if self._scan_thread or not self._scan_queue:
            if not self._scan_thread:
                self.rescan_button.setEnabled(True)
            return
        library = self._scan_queue.pop(0)
        self.rescan_button.setEnabled(False)
        self.statusBar().showMessage(f"Scanning {library.name}…")
        thread = ScanThread(self.database_path, library, self)
        self._scan_thread = thread
        self._last_scan_result = None
        thread.progress.connect(lambda count, _path: self.statusBar().showMessage(f"Scanning {library.name} · {count:,} photos found"))
        thread.scan_finished.connect(self._scan_completed)
        thread.scan_failed.connect(self._scan_failed)
        thread.finished.connect(self._scan_thread_finished)
        thread.start()

    def _scan_completed(self, result: ScanResult) -> None:
        self._last_scan_result = result
        self.thumbnail_manager.reset_validation()
        self.reload_sidebar()
        self.reload_gallery()

    def _scan_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"Scan failed: {message}", 8000)

    def _scan_thread_finished(self) -> None:
        thread = self._scan_thread
        self._scan_thread = None
        if thread:
            thread.deleteLater()
        if self._last_scan_result:
            result = self._last_scan_result
            library = self.database.library(result.library_id)
            if not result.available:
                name = library.name if library else "Folder"
                summary = f"{name} is offline · mount or unlock it, then click Rescan"
            else:
                summary = f"Indexed {result.discovered:,} photos"
                if result.reconnected:
                    summary += f" · reconnected {result.reconnected:,} moved files"
                if result.skipped:
                    summary += f" · skipped {result.skipped:,} unreadable files"
            self.statusBar().showMessage(summary, 7000)
        self._start_next_scan()

    # Virtual organization ---------------------------------------------
    def create_album(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "New virtual album",
            "Album name\nPhotos remain in their original folders.",
        )
        if not accepted or not name.strip():
            return
        try:
            album = self.database.create_album(name)
        except sqlite3.IntegrityError:
            QMessageBox.information(self, "Album already exists", "Choose a different album name.")
            return
        self.reload_sidebar()
        self.statusBar().showMessage(f"Created virtual album “{album.name}”", 5000)

    def _album_context_menu(self, album: Album, global_position) -> None:
        menu = QMenu(self)
        delete = menu.addAction("Delete virtual album…")
        selected = menu.exec(global_position)
        if selected != delete:
            return
        answer = QMessageBox.question(
            self,
            "Delete virtual album?",
            f"Delete “{album.name}”? Original files will not be changed.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.database.delete_album(album.id)
            if self.mode == "album" and self.mode_value == album.id:
                self.mode, self.mode_value = "all", None
                self.page_title.setText("All Photos")
            self.reload_sidebar()
            self.reload_gallery()

    def show_album_picker(self, image_ids: list[str]) -> None:
        if not image_ids:
            return
        albums = self.database.albums()
        if not albums:
            self.create_album()
            albums = self.database.albums()
            if not albums:
                return
        menu = QMenu(self)
        title = menu.addAction("Add to virtual album")
        title.setEnabled(False)
        menu.addSeparator()
        actions: dict[QAction, Album] = {}
        for album in albums:
            action = menu.addAction(f"{album.name}   ({album.image_count})")
            actions[action] = album
        selected = menu.exec(QCursor.pos())
        if selected in actions:
            self.add_images_to_album(actions[selected].id, image_ids)

    def add_images_to_album(self, album_id: str, image_ids: list[str]) -> None:
        added = self.database.add_to_album(album_id, image_ids)
        album = next((item for item in self.database.albums() if item.id == album_id), None)
        self.reload_sidebar()
        if self.mode == "album" and self.mode_value == album_id:
            self.reload_gallery()
        if len(image_ids) == 1:
            self._selection_changed()
        name = album.name if album else "album"
        self.statusBar().showMessage(f"Added {added} photo{'s' if added != 1 else ''} to “{name}”", 5000)

    def save_image_details(
        self,
        image_id: str,
        title: str,
        description: str,
        tags: list[str],
        rating: int,
        favorite: bool,
    ) -> None:
        self.database.update_image_metadata(
            image_id,
            title=title,
            description=description,
            rating=rating,
            favorite=favorite,
        )
        self.database.replace_tags(image_id, tags)
        self.reload_gallery()
        item = self._items_by_id.get(image_id)
        if item:
            item.setSelected(True)
            self.gallery.scrollToItem(item)
        self.statusBar().showMessage("Saved app-only details", 4000)

    def set_favorite(self, image_ids: list[str], favorite: bool) -> None:
        self.database.set_favorite(image_ids, favorite)
        self.reload_gallery()
        self.statusBar().showMessage(
            f"{'Added' if favorite else 'Removed'} {len(image_ids)} photo{'s' if len(image_ids) != 1 else ''} {'to' if favorite else 'from'} Favorites",
            4000,
        )

    # File operations --------------------------------------------------
    def rename_file(self, image_id: str) -> None:
        image = self.database.image(image_id)
        if not image:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Rename original file",
            "New file name\nThis changes the original file on disk.",
            text=image.file_name,
        )
        if not accepted:
            return
        try:
            destination = self.file_operations.rename_file(image, name)
        except FileOperationError as error:
            QMessageBox.warning(self, "Could not rename file", str(error))
            return
        self.reload_gallery()
        renamed = self._items_by_id.get(image_id)
        if renamed:
            renamed.setSelected(True)
            self.gallery.scrollToItem(renamed)
        self.statusBar().showMessage(f"Renamed original file to {destination.name}", 5000)

    def reveal_file(self, image_id: str) -> None:
        image = self.database.image(image_id)
        if not image:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(image.path).parent)))

    # Menus and viewer -------------------------------------------------
    def _gallery_context_menu(self, position) -> None:
        item = self.gallery.itemAt(position)
        if not item:
            return
        if not item.isSelected():
            self.gallery.clearSelection()
            item.setSelected(True)
        image_ids = [str(selected.data(ROLE_IMAGE_ID)) for selected in self.gallery.selectedItems()]
        image = self.database.image(str(item.data(ROLE_IMAGE_ID)))
        if not image:
            return

        menu = QMenu(self)
        heading = menu.addAction("Virtual organization")
        heading.setEnabled(False)
        favorite = menu.addAction("Remove from Favorites" if all((self.database.image(value) or image).favorite for value in image_ids) else "Add to Favorites")
        album_menu = menu.addMenu("Add to album")
        album_actions: dict[QAction, str] = {}
        for album in self.database.albums():
            action = album_menu.addAction(album.name)
            album_actions[action] = album.id
        if not album_actions:
            album_menu.setEnabled(False)
        remove_album = None
        if self.mode == "album" and self.mode_value:
            remove_album = menu.addAction("Remove from this album")
        menu.addSeparator()
        file_heading = menu.addAction("Original file")
        file_heading.setEnabled(False)
        rename = menu.addAction("Rename file…")
        reveal = menu.addAction("Show in folder")
        rename.setEnabled(not image.missing and len(image_ids) == 1)
        reveal.setEnabled(not image.missing and len(image_ids) == 1)
        menu.addSeparator()
        open_action = menu.addAction("Open viewer")

        selected = menu.exec(self.gallery.viewport().mapToGlobal(position))
        if selected == favorite:
            make_favorite = favorite.text().startswith("Add")
            self.set_favorite(image_ids, make_favorite)
        elif selected in album_actions:
            self.add_images_to_album(album_actions[selected], image_ids)
        elif remove_album and selected == remove_album and self.mode_value:
            self.database.remove_from_album(self.mode_value, image_ids)
            self.reload_sidebar()
            self.reload_gallery()
        elif selected == rename:
            self.rename_file(image.id)
        elif selected == reveal:
            self.reveal_file(image.id)
        elif selected == open_action:
            self.open_viewer(image.id)

    def open_viewer(self, image_id: str) -> None:
        if not self.current_images:
            return
        viewer = ImageViewer(self.database, self.current_images, image_id, self.cache_dir)
        viewer.favorite_changed.connect(lambda _id, _favorite: self.reload_gallery())
        viewer.set_launch_window_state(self.windowState())
        viewer.exec()
        self.reload_gallery()

    # Window lifecycle -------------------------------------------------
    def _restore_window_state(self) -> None:
        settings = QSettings()
        geometry = settings.value("main-window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        QSettings().setValue("main-window/geometry", self.saveGeometry())
        self._startup_scan_timer.stop()
        self._folder_search_timer.stop()
        self._search_timer.stop()
        self._thumbnail_timer.stop()
        if self._scan_thread:
            self._scan_queue.clear()
            self._scan_thread.requestInterruption()
            self._scan_thread.wait()
            self._scan_thread = None
        self.database.close()
        event.accept()
