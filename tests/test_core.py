from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEventLoop, QPoint, QPointF, QSettings, QTimer, Qt

from PySide6.QtGui import QColor, QImage, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from simple_gallery.database import Database
from simple_gallery.file_operations import FileOperationError, FileOperations
from simple_gallery.main_window import MainWindow
from simple_gallery.scanner import scan_library
from simple_gallery.viewer import ImageViewer


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
