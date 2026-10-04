from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (
    QCoreApplication,
    QEventLoop,
    QPoint,
    QPointF,
    QSettings,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QAction, QColor, QImage, QImageReader, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication, QListWidgetItem, QMessageBox, QToolButton
from PySide6.QtTest import QTest

from simple_gallery.app import APP_ID, ICON_PATH
from simple_gallery.database import Database
from simple_gallery.desktop_install import (
    ICON_SIZES,
    _install_desktop_entry,
    _install_icons,
)
from simple_gallery.file_operations import FileOperationError, FileOperations
from simple_gallery.main_window import MainWindow
from simple_gallery.scanner import scan_library
from simple_gallery.viewer import FullImageTask, ImageViewer
from simple_gallery.widgets import ROLE_FAVORITE, ROLE_IMAGE_ID, GalleryList


def fresh_workspace() -> Path:
    path = Path(tempfile.gettempdir()) / "luma-contract-tests" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def create_image(path: Path, color: str = "#688fba") -> None:
    image = QImage(640, 420, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    if not image.save(str(path), "JPEG", 90):
        raise RuntimeError(f"Could not create fixture image at {path}")


class CatalogContracts(unittest.TestCase):
    def test_image_read_failure_preserves_previous_catalog(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        create_image(photos / "original.jpg")
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        record = database.images()[0]
        reader = Mock()
        reader.size.return_value = QSize()
        reader.error.return_value = QImageReader.ImageReaderError.DeviceError
        reader.errorString.return_value = "Permission denied"
        with patch("simple_gallery.scanner.QImageReader", return_value=reader) as reader_class:
            reader_class.ImageReaderError = QImageReader.ImageReaderError
            result = scan_library(database, library)
        self.assertIn("Permission denied", result.error)
        self.assertFalse(database.image(record.id).missing)
        database.close()

    def test_existing_database_migrates_mount_tracking_without_losing_metadata(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        create_image(photos / "original.jpg")
        database_path = workspace / "library.sqlite3"
        database = Database(database_path)
        library = database.add_library(photos)
        scan_library(database, library)
        record = database.images()[0]
        database.replace_tags(record.id, ["keep"])
        database.connection.execute("ALTER TABLE libraries DROP COLUMN mount_path")
        database.connection.commit()
        database.close()
        migrated = Database(database_path)
        self.assertIsNone(migrated.library(library.id).mount_path)
        self.assertEqual(migrated.images()[0].id, record.id)
        self.assertEqual(migrated.tags_for_image(record.id), ["keep"])
        scan_library(migrated, migrated.library(library.id))
        self.assertTrue(migrated.library(library.id).available)
        migrated.close()

    def test_unlink_removes_catalog_metadata_and_keeps_originals_and_other_libraries(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        other = workspace / "other"
        photos.mkdir()
        other.mkdir()
        original = photos / "keeper.jpg"
        create_image(original)
        create_image(other / "other.jpg")
        original_bytes = original.read_bytes()
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        other_library = database.add_library(other)
        scan_library(database, library)
        scan_library(database, other_library)
        record = database.images(mode="folder", value=library.id)[0]
        other_record = database.images(mode="folder", value=other_library.id)[0]
        album = database.create_album("Keepers")
        database.add_to_album(album.id, [record.id, other_record.id])
        database.replace_tags(record.id, ["keeper"])

        database.unlink_library(library.id)
        self.assertIsNone(database.library(library.id))
        self.assertIsNone(database.image(record.id))
        self.assertEqual(database.tags_for_image(record.id), [])
        self.assertEqual([image.id for image in database.images(mode="album", value=album.id)], [other_record.id])
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertTrue(photos.is_dir())
        self.assertEqual(database.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        relinked = database.add_library(photos)
        scan_library(database, relinked)
        self.assertNotEqual(database.images(mode="folder", value=relinked.id)[0].id, record.id)
        database.close()

    def test_unmounted_drive_with_empty_mountpoint_preserves_catalog_and_recovers(self) -> None:
        workspace = fresh_workspace()
        mount = workspace / "drive"
        mount.mkdir()
        create_image(mount / "archive.jpg")
        database = Database(workspace / "library.sqlite3")
        with patch("simple_gallery.database.os.path.ismount", side_effect=lambda path: str(path) == str(mount)):
            library = database.add_library(mount)
            scan_library(database, library)
        record = database.images()[0]
        last_scan = database.library(library.id).last_scan
        database.close()
        detached = workspace / "detached"
        mount.rename(detached)
        mount.mkdir()  # Linux can leave the underlying mountpoint in place.
        database = Database(workspace / "library.sqlite3")
        with patch("simple_gallery.scanner.os.path.ismount", return_value=False):
            result = scan_library(database, database.library(library.id))
        self.assertFalse(result.available)
        self.assertFalse(database.image(record.id).missing)
        self.assertEqual(database.library(library.id).last_scan, last_scan)
        mount.rmdir()
        detached.rename(mount)
        with patch("simple_gallery.scanner.os.path.ismount", side_effect=lambda path: str(path) == str(mount)):
            result = scan_library(database, database.library(library.id))
        self.assertTrue(result.available)
        self.assertEqual(database.images()[0].id, record.id)
        database.close()

    def test_directory_read_error_rolls_back_partial_scan_then_retry_reconciles(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        child = photos / "child"
        child.mkdir(parents=True)
        create_image(child / "original.jpg")
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        record = database.images()[0]
        database.replace_tags(record.id, ["keep"])
        last_scan = database.library(library.id).last_scan
        create_image(photos / "new.jpg")

        def partial_walk(root, *, followlinks, onerror):
            yield str(root), ["child"], ["new.jpg"]
            onerror(PermissionError(13, "Permission denied", str(child)))

        with patch("simple_gallery.scanner.os.walk", side_effect=partial_walk):
            result = scan_library(database, library)
        self.assertTrue(result.available)
        self.assertIn("Scan incomplete", result.error)
        self.assertEqual([image.id for image in database.images()], [record.id])
        self.assertEqual(database.library(library.id).last_scan, last_scan)
        self.assertEqual(database.tags_for_image(record.id), ["keep"])
        result = scan_library(database, database.library(library.id))
        self.assertIsNone(result.error)
        self.assertEqual(result.discovered, 2)
        self.assertIsNone(database.library(library.id).last_error)
        database.close()

    def test_drive_disappearing_mid_scan_does_not_commit_new_or_missing_records(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        create_image(photos / "original.jpg")
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        record = database.images()[0]
        create_image(photos / "new.jpg")

        def disconnect(_count, _path):
            photos.rename(workspace / "detached")

        result = scan_library(database, library, on_progress=disconnect)
        self.assertFalse(result.available)
        self.assertFalse(database.image(record.id).missing)
        self.assertEqual([image.id for image in database.images()], [record.id])
        database.close()

    def test_cancelled_scan_preserves_previous_catalog(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        create_image(photos / "original.jpg")
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        record = database.images()[0]
        create_image(photos / "new.jpg")
        stopped = False

        def stop(_count, _path):
            nonlocal stopped
            stopped = True

        scan_library(database, library, should_stop=lambda: stopped, on_progress=stop)
        self.assertEqual([image.id for image in database.images()], [record.id])
        database.close()

    def test_external_rename_and_missing_state_preserve_identity_and_metadata(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        original = photos / "original.jpg"
        create_image(original)

        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        first_scan = scan_library(database, library)
        self.assertEqual(first_scan.discovered, 1)
        record = database.images()[0]
        album = database.create_album("Keepers")
        database.add_to_album(album.id, [record.id])
        database.replace_tags(record.id, ["blue", "reference"])
        database.update_image_metadata(
            record.id,
            title="Blue study",
            description="Identity contract",
            rating=4,
            favorite=True,
        )

        externally_renamed = photos / "renamed-outside-luma.jpg"
        original.rename(externally_renamed)
        second_scan = scan_library(database, library)
        self.assertEqual(second_scan.reconnected, 1)
        reconnected = database.image(record.id)
        self.assertIsNotNone(reconnected)
        self.assertEqual(reconnected.path, str(externally_renamed.resolve()))
        self.assertEqual(database.tags_for_image(record.id), ["blue", "reference"])
        self.assertEqual(database.albums_for_image(record.id)[0].id, album.id)
        self.assertEqual(reconnected.title, "Blue study")

        temporarily_away = workspace / "temporarily-away.jpg"
        externally_renamed.rename(temporarily_away)
        scan_library(database, library)
        missing = database.image(record.id)
        self.assertTrue(missing.missing)
        self.assertEqual(database.tags_for_image(record.id), ["blue", "reference"])
        self.assertEqual(database.albums_for_image(record.id)[0].id, album.id)
        database.close()

    def test_file_rename_changes_original_only_after_validation(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        original = photos / "first.jpg"
        collision = photos / "existing.jpg"
        create_image(original, "#c86f5d")
        create_image(collision, "#70a080")

        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        record = next(image for image in database.images() if image.file_name == "first.jpg")
        operations = FileOperations(database)

        with self.assertRaises(FileOperationError):
            operations.rename_file(record, collision.name)
        self.assertTrue(original.exists())
        self.assertEqual(database.image(record.id).path, str(original.resolve()))

        destination = operations.rename_file(record, "descriptive-name.jpg")
        self.assertTrue(destination.exists())
        renamed_record = database.image(record.id)
        self.assertEqual(renamed_record.id, record.id)
        self.assertEqual(renamed_record.file_name, "descriptive-name.jpg")
        database.close()

    def test_subfolder_query_preserves_recursive_filesystem_organization(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        iceland = photos / "trips" / "iceland"
        mexico = photos / "trips" / "mexico"
        scans = photos / "scans"
        for folder in (iceland, mexico, scans):
            folder.mkdir(parents=True)
        create_image(photos / "loose.jpg")
        create_image(iceland / "waterfall.jpg")
        create_image(mexico / "market.jpg")
        create_image(scans / "negative.jpg")

        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        self.assertEqual(len(database.images(mode="folder", value=library.id)), 4)
        self.assertEqual(
            {Path(path).name for path in database.indexed_folders(library.id)},
            {"photos", "iceland", "mexico", "scans"},
        )
        self.assertEqual(
            {image.file_name for image in database.images(mode="folder_path", value=str(photos / "trips"))},
            {"waterfall.jpg", "market.jpg"},
        )
        self.assertEqual(
            [image.file_name for image in database.images(mode="folder_path", value=str(iceland))],
            ["waterfall.jpg"],
        )
        database.close()


    def test_offline_drive_preserves_records_and_recovers_on_same_mount_path(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "external-drive"
        photos.mkdir()
        create_image(photos / "archive.jpg")
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        record = database.images()[0]
        album = database.create_album("Drive archive")
        database.add_to_album(album.id, [record.id])
        database.replace_tags(record.id, ["external-drive"])

        detached = workspace / "external-drive-detached"
        photos.rename(detached)
        offline = scan_library(database, library)
        self.assertFalse(offline.available)
        self.assertFalse(database.library(library.id).available)
        self.assertFalse(database.image(record.id).missing)
        self.assertEqual(database.tags_for_image(record.id), ["external-drive"])
        self.assertEqual(database.albums_for_image(record.id)[0].id, album.id)

        detached.rename(photos)
        recovered = scan_library(database, database.library(library.id))
        self.assertTrue(recovered.available)
        self.assertTrue(database.library(library.id).available)
        self.assertEqual(database.images()[0].id, record.id)
        database.close()

    def test_library_can_be_relinked_to_a_new_mount_path_without_new_ids(self) -> None:
        workspace = fresh_workspace()
        original_root = workspace / "old-mount"
        original_root.mkdir()
        create_image(original_root / "keeper.jpg")
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(original_root)
        scan_library(database, library)
        record = database.images()[0]
        database.replace_tags(record.id, ["keeper"])

        new_root = workspace / "replacement-mount"
        original_root.rename(new_root)
        scan_library(database, library)
        updated = database.relink_library(library.id, new_root)
        self.assertEqual(updated.id, library.id)
        self.assertEqual(database.image(record.id).id, record.id)
        self.assertEqual(database.image(record.id).path, str(new_root / "keeper.jpg"))
        self.assertEqual(database.tags_for_image(record.id), ["keeper"])
        scan_library(database, updated)
        self.assertTrue(database.library(library.id).available)
        database.close()


class AppearanceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        QCoreApplication.setOrganizationName("LumaContractTests")
        QCoreApplication.setApplicationName(uuid.uuid4().hex)
        cls.application = QApplication.instance() or QApplication([])

    def _cached_gallery_window(self) -> MainWindow:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        cache = workspace / "cache"
        photos.mkdir()
        cache.mkdir()
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        thumbnail = QImage(80, 60, QImage.Format.Format_RGB32)
        thumbnail.fill(QColor("#6688aa"))
        image_ids = []
        for index in range(60):
            image_id, _ = database.upsert_scanned_image(
                library_id=library.id,
                path=str(photos / f"image-{index:04}.jpg"),
                device_id=1, inode=index + 1, file_size=4096, modified_ns=1,
                mime_type="image/jpeg", width=800, height=600,
                date_taken="2026-07-21T12:00:00+00:00",
            )
            image_ids.append(image_id)
            self.assertTrue(thumbnail.save(str(cache / f"{image_id}.jpg"), "JPEG", 80))
        database.finish_library_scan(library.id, image_ids)
        database.close()
        window = MainWindow(workspace / "library.sqlite3", cache)
        self.addCleanup(window.close)
        window._startup_scan_timer.stop()
        window.thumbnail_manager.request = Mock()
        window.resize(1200, 760)
        window.show()
        self.application.processEvents()
        window.gallery.doItemsLayout()
        window._load_visible_thumbnails()
        return window

    def test_closing_viewer_preserves_gallery_thumbnails_selection_and_scroll(self) -> None:
        window = self._cached_gallery_window()
        scrollbar = window.gallery.verticalScrollBar()
        scrollbar.setValue(400)
        window._load_visible_thumbnails()
        self.assertGreater(scrollbar.value(), 0)
        image = next(
            image for image in window.current_images
            if window.gallery.visualItemRect(window._items_by_id[image.id]).intersects(window.gallery.viewport().rect())
            and isinstance(window._items_by_id[image.id].data(Qt.ItemDataRole.DecorationRole), QPixmap)
        )
        item = window._items_by_id[image.id]
        item.setSelected(True)
        pixmap = item.data(Qt.ItemDataRole.DecorationRole)
        self.assertIsInstance(pixmap, QPixmap)
        self.assertFalse(pixmap.isNull())
        selected_ids = [selected.data(ROLE_IMAGE_ID) for selected in window.gallery.selectedItems()]
        before_scroll = scrollbar.value()
        before_items = dict(window._items_by_id)

        def viewer_factory(*args):
            viewer = ImageViewer(*args)
            QTimer.singleShot(0, viewer.reject)
            return viewer

        with patch("simple_gallery.main_window.ImageViewer", side_effect=viewer_factory):
            window.open_viewer(image.id)
        self.assertEqual(scrollbar.value(), before_scroll)
        self.assertEqual([selected.data(ROLE_IMAGE_ID) for selected in window.gallery.selectedItems()], selected_ids)
        for image_id, original_item in before_items.items():
            self.assertIs(window._items_by_id[image_id], original_item)
        self.assertEqual(item.data(Qt.ItemDataRole.DecorationRole).cacheKey(), pixmap.cacheKey())

    def test_viewer_favorite_updates_badge_and_inspector_without_clearing_thumbnail(self) -> None:
        window = self._cached_gallery_window()
        image = window.current_images[0]
        item = window._items_by_id[image.id]
        item.setSelected(True)
        pixmap = item.data(Qt.ItemDataRole.DecorationRole)
        self.assertFalse(pixmap.isNull())

        def viewer_factory(*args):
            viewer = ImageViewer(*args)

            def toggle_and_close():
                viewer._toggle_favorite()
                viewer.reject()

            QTimer.singleShot(0, toggle_and_close)
            return viewer

        with patch("simple_gallery.main_window.ImageViewer", side_effect=viewer_factory):
            window.open_viewer(image.id)
        self.assertIs(window._items_by_id[image.id], item)
        self.assertEqual(item.data(Qt.ItemDataRole.DecorationRole).cacheKey(), pixmap.cacheKey())
        self.assertTrue(item.data(ROLE_FAVORITE))
        self.assertTrue(window.database.image(image.id).favorite)
        self.assertTrue(window.current_images[0].favorite)
        self.assertTrue(window.inspector.favorite_button.isChecked())

    def test_viewer_arrow_keys_navigate_on_first_press_from_every_focused_control(self) -> None:
        window = self._cached_gallery_window()
        viewer = ImageViewer(window.database, window.current_images, window.current_images[10].id, window.cache_dir)
        self.addCleanup(viewer.close)
        viewer.show()
        viewer.activateWindow()
        self.application.processEvents()
        controls = [viewer.focusWidget(), viewer.filmstrip, *viewer.findChildren(QToolButton)]
        self.assertIsNotNone(controls[0])
        for control in controls:
            with self.subTest(control=control.objectName()):
                control.setFocus()
                self.application.processEvents()
                before = viewer.index
                QTest.keyClick(control, Qt.Key.Key_Right)
                self.assertEqual(viewer.index, before + 1)
                self.assertEqual(viewer.name_label.text(), viewer.current.file_name)
                QTest.keyClick(control, Qt.Key.Key_Left)
                self.assertEqual(viewer.index, before)

        viewer.image_view.setFocus()
        for _ in range(5):
            before = viewer.index
            QTest.keyClick(viewer.image_view, Qt.Key.Key_Right)
            self.assertEqual(viewer.index, before + 1)
        viewer.filmstrip.setCurrentRow(len(viewer.images) - 1)
        QTest.keyClick(viewer.filmstrip, Qt.Key.Key_Right)
        self.assertEqual(viewer.index, 0)
        QTest.keyClick(viewer.filmstrip, Qt.Key.Key_Left)
        self.assertEqual(viewer.index, len(viewer.images) - 1)

    def test_viewer_navigation_does_not_read_originals_on_ui_thread_and_ignores_stale_results(self) -> None:
        window = self._cached_gallery_window()
        image_pool = Mock()
        tasks = []
        image_pool.start.side_effect = tasks.append
        original_paths = {image.path for image in window.current_images}
        path_exists = Path.exists

        def cached_file_exists(path):
            self.assertNotIn(str(path), original_paths, "The UI must not probe an original on a slow drive")
            return path_exists(path)

        with (
            patch("simple_gallery.viewer.QThreadPool.globalInstance", return_value=image_pool),
            patch("simple_gallery.viewer.QImageReader") as reader,
            patch("simple_gallery.viewer.Path.exists", cached_file_exists),
        ):
            viewer = ImageViewer(window.database, window.current_images, window.current_images[0].id, window.cache_dir)
            self.addCleanup(viewer.close)
            viewer.show()
            viewer.activateWindow()
            self.application.processEvents()
            QTest.keyClick(viewer.image_view, Qt.Key.Key_Right)
            self.assertEqual(viewer.index, 1)
            self.assertEqual(viewer.name_label.text(), window.current_images[1].file_name)
            self.assertFalse(viewer.image_view._pixmap.isNull())
            self.assertEqual(len(tasks), 2)
            reader.assert_not_called()

        before_preview = viewer.image_view._pixmap.cacheKey()
        decoded = QImage(640, 420, QImage.Format.Format_RGB32)
        decoded.fill(QColor("#cc3311"))
        tasks[0].signals.metadata_ready.emit(tasks[0].image_id, "Old camera", "Old lens")
        tasks[0].signals.ready.emit(tasks[0].image_id, decoded)
        self.assertEqual(viewer.detail_labels["Camera"].text(), "—")
        self.assertEqual(viewer.image_view._pixmap.cacheKey(), before_preview)
        tasks[1].signals.metadata_ready.emit(tasks[1].image_id, "Current camera", "Current lens")
        tasks[1].signals.ready.emit(tasks[1].image_id, decoded)
        self.assertEqual(viewer.detail_labels["Camera"].text(), "Current camera")
        self.assertEqual(viewer.detail_labels["Lens"].text(), "Current lens")
        self.assertEqual(viewer.image_view._pixmap.size(), decoded.size())
        viewer.previous()
        self.assertEqual(viewer.detail_labels["Camera"].text(), "Old camera")

    def test_full_image_worker_loads_embedded_metadata_and_reports_unavailable_originals(self) -> None:
        workspace = fresh_workspace()
        source = workspace / "metadata.png"
        image = QImage(640, 420, QImage.Format.Format_RGB32)
        image.fill(QColor("#6688aa"))
        image.setText("Make", "Test camera")
        image.setText("Model", "Model 1")
        image.setText("LensModel", "Test lens")
        self.assertTrue(image.save(str(source), "PNG"))
        task = FullImageTask("image-id", str(source))
        metadata = []
        decoded = []
        task.signals.metadata_ready.connect(lambda *values: metadata.append(values))
        task.signals.ready.connect(lambda image_id, image: decoded.append((image_id, image)))
        task.run()
        self.assertEqual(metadata, [("image-id", "Test camera Model 1", "Test lens")])
        self.assertEqual(decoded[0][0], "image-id")
        self.assertEqual(decoded[0][1].size(), image.size())

        source.unlink()
        unavailable = []
        task.signals.failed.connect(lambda *values: unavailable.append(values))
        task.run()
        self.assertEqual(unavailable, [("image-id", "Original file is unavailable")])

    def test_viewer_unfavorite_filters_gallery_in_place_and_preserves_viewer_navigation(self) -> None:
        window = self._cached_gallery_window()
        first, second = window.current_images[:2]
        window.database.set_favorite([first.id, second.id], True)
        window.select_mode("favorites", None, "Favorites", window.favorite_button)
        self.application.processEvents()
        window._load_visible_thumbnails()
        second_item = window._items_by_id[second.id]
        pixmap = second_item.data(Qt.ItemDataRole.DecorationRole)
        self.assertFalse(pixmap.isNull())
        viewers = []

        def viewer_factory(*args):
            viewer = ImageViewer(*args)
            viewers.append(viewer)

            def toggle_and_close():
                viewer._toggle_favorite()
                viewer.next()
                viewer.reject()

            QTimer.singleShot(0, toggle_and_close)
            return viewer

        with patch("simple_gallery.main_window.ImageViewer", side_effect=viewer_factory):
            window.open_viewer(first.id)
            self.assertEqual(len(viewers[0].images), 2)
            self.assertEqual(viewers[0].current.id, second.id)
            self.assertEqual(window.gallery.count(), 1)
            self.assertEqual([image.id for image in window.current_images], [second.id])
            self.assertIs(window._items_by_id[second.id], second_item)
            self.assertEqual(second_item.data(Qt.ItemDataRole.DecorationRole).cacheKey(), pixmap.cacheKey())
            self.assertTrue(window.page_subtitle.text().startswith("1 photo"))
            window.open_viewer(second.id)
        self.assertEqual(window.gallery.count(), 0)
        self.assertEqual(window.current_images, [])
        self.assertEqual(window.content_stack.currentIndex(), 2)
        self.assertEqual(window.inspector.stack.currentIndex(), 0)
        self.assertTrue(window.page_subtitle.text().startswith("0 photos"))

    def test_sidebar_context_menu_unlinks_selected_subfolder_library_and_cancel_keeps_it(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        child = photos / "child"
        child.mkdir(parents=True)
        original = child / "original.jpg"
        create_image(original)
        window = MainWindow(workspace / "library.sqlite3", workspace / "cache")
        library = window.database.add_library(photos)
        scan_library(window.database, library)
        window.expanded_folders.update([str(photos), str(child)])
        window.reload_sidebar()
        window.select_mode("folder_path", str(child), "child", window._folder_rows_by_path[str(child)].nav_button)
        window._scan_queue.append(library)

        with patch("simple_gallery.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            window.unlink_library(library)
        self.assertIsNotNone(window.database.library(library.id))
        self.assertEqual(window.mode, "folder_path")

        unlink_action = QAction("Unlink folder…")
        menu = Mock()
        menu.addAction.side_effect = lambda text: unlink_action if text == unlink_action.text() else QAction(text)
        menu.exec.return_value = unlink_action
        with (
            patch("simple_gallery.main_window.QMenu", return_value=menu),
            patch("simple_gallery.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
        ):
            window._library_context_menu(library, QPoint())
        self.assertIsNone(window.database.library(library.id))
        self.assertEqual(window.mode, "all")
        self.assertEqual(window.gallery.count(), 0)
        self.assertEqual(window._scan_queue, [])
        self.assertNotIn(str(child), window.expanded_folders)
        self.assertTrue(window.all_button.isChecked())
        self.assertTrue(original.exists())
        window.close()

    def test_unlink_interrupts_active_scan_and_allows_remaining_library_to_scan(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        other = workspace / "other"
        photos.mkdir()
        other.mkdir()
        create_image(photos / "first.jpg")
        create_image(other / "second.jpg")
        window = MainWindow(workspace / "library.sqlite3", workspace / "cache")
        library = window.database.add_library(photos)
        other_library = window.database.add_library(other)
        window.queue_scan([library, other_library])
        with patch("simple_gallery.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            window.unlink_library(library)
        self.application.processEvents()
        if window._scan_thread:
            window._scan_thread.wait()
            self.application.processEvents()
        self.assertIsNone(window.database.library(library.id))
        self.assertEqual([image.file_name for image in window.database.images()], ["second.jpg"])
        self.assertTrue(window.rescan_button.isEnabled())
        self.assertTrue((photos / "first.jpg").exists())
        window.close()

    def test_gallery_wheel_scroll_is_smaller_animated_and_touchpad_remains_precise(self) -> None:
        gallery = GalleryList()
        gallery.resize(500, 450)
        for index in range(80):
            gallery.addItem(QListWidgetItem(str(index)))
        gallery.show()
        self.application.processEvents()
        scrollbar = gallery.verticalScrollBar()

        def wheel(angle=0, pixels=0):
            event = QWheelEvent(
                QPointF(100, 100), QPointF(100, 100), QPoint(0, pixels), QPoint(0, angle),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
            )
            self.application.sendEvent(gallery.viewport(), event)

        wheel(angle=-120)
        self.assertEqual(scrollbar.value(), 0)
        loop = QEventLoop()
        QTimer.singleShot(75, loop.quit)
        loop.exec()
        self.assertGreater(scrollbar.value(), 0)
        self.assertLess(scrollbar.value(), 72)
        QTimer.singleShot(150, loop.quit)
        loop.exec()
        self.assertEqual(scrollbar.value(), 72)
        wheel(angle=-120)
        wheel(angle=-120)
        QTimer.singleShot(200, loop.quit)
        loop.exec()
        self.assertEqual(scrollbar.value(), 216)
        wheel(pixels=-11)
        self.assertEqual(scrollbar.value(), 227)
        wheel(angle=-120)
        wheel(angle=120)  # Reversing direction must cancel the queued forward motion.
        QTimer.singleShot(200, loop.quit)
        loop.exec()
        self.assertEqual(scrollbar.value(), 155)
        gallery.clear()
        self.application.processEvents()
        self.assertEqual(scrollbar.value(), 0)
        gallery.close()

    def test_dark_theme_persists_and_window_uses_integrated_chrome(self) -> None:
        QSettings().setValue("appearance/theme", "light")
        first_workspace = fresh_workspace()
        window = MainWindow(first_workspace / "library.sqlite3", first_workspace / "cache")
        window.show()
        self.application.processEvents()
        self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertEqual(window.title_bar.height(), 44)

        window.toggle_theme()
        self.assertEqual(window.theme, "dark")
        self.assertEqual(self.application.property("theme"), "dark")
        window.close()

        second_workspace = fresh_workspace()
        restored = MainWindow(second_workspace / "library.sqlite3", second_workspace / "cache")
        self.assertEqual(restored.theme, "dark")
        self.assertEqual(restored.title_bar.theme_button.text(), "☀")
        restored.close()

    def test_offline_library_can_be_located_and_rescanned_from_navigation(self) -> None:
        workspace = fresh_workspace()
        drive = workspace / "external-drive"
        drive.mkdir()
        create_image(drive / "archive.jpg")
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(drive)
        scan_library(database, library)
        image_id = database.images()[0].id
        remounted = workspace / "remounted" / "external-drive"
        remounted.parent.mkdir()
        drive.rename(remounted)
        scan_library(database, library)
        database.close()

        window = MainWindow(workspace / "library.sqlite3", workspace / "cache")
        window.show()
        self.application.processEvents()
        root_button = window._library_buttons[0]
        self.assertTrue(root_button.property("offline"))
        self.assertIn("Offline", root_button.text())

        current = window.database.library(library.id)
        with patch(
            "simple_gallery.main_window.QFileDialog.getExistingDirectory",
            return_value=str(remounted),
        ):
            window.locate_library(current)
        thread = window._scan_thread
        if thread:
            thread.wait()
            self.application.processEvents()
        reconnected = window.database.library(library.id)
        self.assertTrue(reconnected.available)
        self.assertEqual(reconnected.path, str(remounted))
        self.assertEqual(window.database.image(image_id).id, image_id)
        self.assertNotIn("Offline", window._library_buttons[0].text())
        window.close()

    def test_photo_information_panel_collapses_and_restores_space(self) -> None:
        QSettings().setValue("appearance/inspector-visible", True)
        workspace = fresh_workspace()
        window = MainWindow(workspace / "library.sqlite3", workspace / "cache")
        window.resize(1200, 760)
        window.show()
        self.application.processEvents()
        expanded_width = window.content_stack.width()
        self.assertTrue(window.inspector.isVisible())
        self.assertTrue(window.title_bar.information_button.isChecked())

        window.title_bar.information_button.click()
        self.application.processEvents()
        self.assertFalse(window.inspector.isVisible())
        self.assertFalse(window.title_bar.information_button.isChecked())
        self.assertGreater(window.content_stack.width(), expanded_width + 250)
        self.assertFalse(QSettings().value("appearance/inspector-visible", True, type=bool))
        window.close()

        restored_workspace = fresh_workspace()
        restored = MainWindow(restored_workspace / "library.sqlite3", restored_workspace / "cache")
        self.assertFalse(restored.inspector_visible)
        restored.close()

    def test_folder_tree_expands_per_node_and_search_reveals_matches(self) -> None:
        QSettings().setValue("folders/expanded", [])
        workspace = fresh_workspace()
        photos = workspace / "photos"
        for folder in (
            photos / "trips" / "iceland",
            photos / "trips" / "mexico",
            photos / "scans",
        ):
            folder.mkdir(parents=True)
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        paths = (
            photos / "loose.jpg",
            photos / "trips" / "iceland" / "waterfall.jpg",
            photos / "trips" / "mexico" / "market.jpg",
            photos / "scans" / "negative.jpg",
        )
        image_ids: list[str] = []
        for index, path in enumerate(paths):
            image_id, _ = database.upsert_scanned_image(
                library_id=library.id,
                path=str(path),
                device_id=1,
                inode=index + 1,
                file_size=4096,
                modified_ns=1,
                mime_type="image/jpeg",
                width=800,
                height=600,
                date_taken=None,
            )
            image_ids.append(image_id)
        database.finish_library_scan(library.id, image_ids)
        database.close()

        window = MainWindow(workspace / "library.sqlite3", workspace / "cache")
        window.show()
        self.application.processEvents()
        self.assertEqual(len(window._library_buttons), 1)
        window._library_buttons[0].click()
        self.assertEqual(len(window.current_images), 4)

        window._folder_rows_by_path[str(photos)].disclosure.click()
        self.assertEqual(len(window._library_buttons), 3)
        window._folder_rows_by_path[str(photos / "trips")].disclosure.click()
        self.assertEqual(len(window._library_buttons), 5)
        trips = window._folder_rows_by_path[str(photos / "trips")].nav_button
        trips.click()
        self.assertEqual(
            {image.file_name for image in window.current_images},
            {"waterfall.jpg", "market.jpg"},
        )

        window._folder_rows_by_path[str(photos)].disclosure.click()
        self.assertEqual(len(window._library_buttons), 1)
        window.folder_search_toggle.click()
        window.folder_search.setText("ice")
        window.reload_sidebar()
        self.assertEqual(len(window._library_buttons), 1)
        result = window._library_buttons[0]
        self.assertIn("iceland  ·  trips", result.text())
        result.click()
        self.assertEqual(
            [image.file_name for image in window.current_images],
            ["waterfall.jpg"],
        )

        window.folder_search_toggle.click()
        self.assertEqual(len(window._library_buttons), 5)
        self.assertIn(
            str(photos / "trips" / "iceland"),
            window._folder_rows_by_path,
        )
        window.close()

        restored = MainWindow(workspace / "library.sqlite3", workspace / "cache")
        self.assertEqual(len(restored._library_buttons), 5)
        restored.close()

    def test_viewer_uses_integrated_chrome_and_wheel_zoom(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        source = photos / "viewer.jpg"
        create_image(source)
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        image = database.images()[0]

        viewer = ImageViewer(database, [image], image.id, workspace / "cache")
        self.assertIsNone(viewer.parent())
        viewer.set_launch_window_state(Qt.WindowState.WindowMaximized)
        viewer.show()
        mapped = QEventLoop()
        QTimer.singleShot(30, mapped.quit)
        mapped.exec()
        self.assertTrue(viewer.isMaximized())
        viewer.showNormal()
        self.application.processEvents()
        if viewer.image_view._pixmap.isNull():
            loaded = QEventLoop()
            viewer.full_image_ready.connect(lambda _image_id: loaded.quit())
            QTimer.singleShot(1000, loaded.quit)
            loaded.exec()
        self.assertTrue(viewer.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertIsNotNone(viewer.chrome.maximize_button)
        self.assertFalse(viewer.image_view._pixmap.isNull())

        center = QPointF(viewer.image_view.rect().center())
        anchor = center + QPointF(110, 55)
        global_anchor = QPointF(viewer.image_view.mapToGlobal(anchor.toPoint()))
        global_center = QPointF(viewer.image_view.mapToGlobal(center.toPoint()))
        wheel = QWheelEvent(
            anchor,
            global_anchor,
            QPoint(),
            QPoint(0, 360),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        self.application.sendEvent(viewer.image_view, wheel)
        self.assertGreater(viewer.image_view.zoom_factor, 1.0)
        self.assertNotEqual(viewer.zoom_label.text(), "Fit")
        self.assertNotEqual(viewer.image_view._offset, QPointF())
        offset_before_pan = QPointF(viewer.image_view._offset)

        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            center,
            global_center,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        moved = center + QPointF(30, 20)
        move = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            moved,
            QPointF(viewer.image_view.mapToGlobal(moved.toPoint())),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            moved,
            QPointF(viewer.image_view.mapToGlobal(moved.toPoint())),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.application.sendEvent(viewer.image_view, press)
        self.application.sendEvent(viewer.image_view, move)
        self.application.sendEvent(viewer.image_view, release)
        self.assertNotEqual(viewer.image_view._offset, offset_before_pan)

        double_click = QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            moved,
            QPointF(viewer.image_view.mapToGlobal(moved.toPoint())),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.application.sendEvent(viewer.image_view, double_click)
        self.assertEqual(viewer.image_view.zoom_factor, 1.0)
        self.assertEqual(viewer.zoom_label.text(), "Fit")
        viewer.close()
        database.close()

    def test_viewer_is_independent_and_inherits_each_main_window_state(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        source = photos / "viewer.jpg"
        create_image(source)
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        scan_library(database, library)
        image_id = database.images()[0].id
        database.close()

        window = MainWindow(workspace / "library.sqlite3", workspace / "cache")
        window._startup_scan_timer.stop()
        window.showFullScreen()
        self.application.processEvents()
        self.assertTrue(window.isFullScreen())
        fullscreen_viewer = Mock()
        fullscreen_state = window.windowState()
        with patch("simple_gallery.main_window.ImageViewer") as viewer_type:
            viewer_type.return_value = fullscreen_viewer
            window.open_viewer(image_id)
        viewer_type.assert_called_once_with(
            window.database,
            window.current_images,
            image_id,
            window.cache_dir,
        )
        fullscreen_viewer.set_launch_window_state.assert_called_once_with(fullscreen_state)
        fullscreen_viewer.exec.assert_called_once_with()

        window.showMaximized()
        self.application.processEvents()
        self.assertFalse(window.isFullScreen())
        maximized_viewer = Mock()
        maximized_state = window.windowState()
        with patch("simple_gallery.main_window.ImageViewer", return_value=maximized_viewer):
            window.open_viewer(image_id)
        maximized_viewer.set_launch_window_state.assert_called_once_with(maximized_state)
        maximized_viewer.exec.assert_called_once_with()

        window.showNormal()
        self.application.processEvents()
        normal_viewer = Mock()
        normal_state = window.windowState()
        with patch("simple_gallery.main_window.ImageViewer", return_value=normal_viewer):
            window.open_viewer(image_id)
        normal_viewer.set_launch_window_state.assert_called_once_with(normal_state)
        normal_viewer.exec.assert_called_once_with()
        window.close()

    def test_viewer_loads_only_nearby_filmstrip_thumbnails(self) -> None:
        workspace = fresh_workspace()
        photos = workspace / "photos"
        cache = workspace / "cache"
        photos.mkdir()
        cache.mkdir()
        source = photos / "current.jpg"
        create_image(source)
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        image_ids: list[str] = []
        current_id = ""
        thumbnail = QImage(80, 60, QImage.Format.Format_RGB32)
        thumbnail.fill(QColor("#6688aa"))
        for index in range(80):
            path = source if index == 40 else photos / f"image-{index:03}.jpg"
            image_id, _ = database.upsert_scanned_image(
                library_id=library.id,
                path=str(path),
                device_id=1,
                inode=index + 1,
                file_size=4096,
                modified_ns=1,
                mime_type="image/jpeg",
                width=640,
                height=420,
                date_taken="2026-07-21T12:00:00+00:00",
            )
            image_ids.append(image_id)
            self.assertTrue(thumbnail.save(str(cache / f"{image_id}.jpg"), "JPEG", 80))
            if index == 40:
                current_id = image_id
        database.finish_library_scan(library.id, image_ids)

        image_pool = Mock()
        with patch("simple_gallery.viewer.QThreadPool.globalInstance", return_value=image_pool):
            viewer = ImageViewer(database, database.images(), current_id, cache)
            viewer.show()
            self.application.processEvents()
        self.assertEqual(viewer.filmstrip.count(), 80)
        expected = min(len(viewer.images), viewer.index + 13) - max(0, viewer.index - 12)
        self.assertEqual(len(viewer._filmstrip_loaded), expected)
        self.assertLessEqual(len(viewer._filmstrip_loaded), 25)
        self.assertEqual(viewer.image_view._pixmap.size(), thumbnail.size())
        image_pool.start.assert_called_once()
        viewer.close()
        database.close()

    def test_desktop_installer_writes_gnome_entry_and_icon_theme(self) -> None:
        workspace = fresh_workspace()
        data_home = workspace / "share"
        executable = workspace / "tool bin" / APP_ID
        executable.parent.mkdir()
        executable.touch()

        icon_path = _install_icons(data_home)
        desktop_entry = _install_desktop_entry(data_home, executable, icon_path)

        self.assertTrue(ICON_PATH.is_file())
        self.assertEqual(desktop_entry, data_home / "applications" / f"{APP_ID}.desktop")
        desktop_text = desktop_entry.read_text(encoding="utf-8")
        self.assertIn("Name=Luma", desktop_text)
        self.assertIn(f'Exec="{executable}"', desktop_text)
        self.assertIn(f"Icon={icon_path}", desktop_text)
        self.assertIn("StartupWMClass=Luma", desktop_text)
        self.assertEqual(QImage(str(icon_path)).size(), QSize(1024, 1024))
        for size in ICON_SIZES:
            icon = data_home / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
            self.assertEqual(QImage(str(icon)).size(), QSize(size, size))

    def test_large_gallery_only_requests_visible_thumbnails(self) -> None:
        QSettings().setValue("appearance/theme", "light")
        workspace = fresh_workspace()
        photos = workspace / "photos"
        photos.mkdir()
        database = Database(workspace / "library.sqlite3")
        library = database.add_library(photos)
        image_ids: list[str] = []
        for index in range(180):
            image_id, _ = database.upsert_scanned_image(
                library_id=library.id,
                path=str(photos / f"image-{index:04}.jpg"),
                device_id=1,
                inode=index + 1,
                file_size=4096,
                modified_ns=1,
                mime_type="image/jpeg",
                width=800,
                height=600,
                date_taken="2026-07-21T12:00:00+00:00",
            )
            image_ids.append(image_id)
        database.finish_library_scan(library.id, image_ids)
        database.close()

        window = MainWindow(workspace / "library.sqlite3", workspace / "cache")
        requested: list[str] = []
        window.thumbnail_manager.request = (
            lambda image_id, _source, size=640: requested.append(image_id)
        )
        window.show()
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
        self.assertEqual(window.gallery.count(), 180)
        self.assertGreater(len(set(requested)), 0)
        self.assertLess(len(set(requested)), 60)
        window.close()


if __name__ == "__main__":
    unittest.main()
