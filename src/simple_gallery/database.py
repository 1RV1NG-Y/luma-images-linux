from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence

from .models import Album, ImageRecord, Library


IMAGE_COLUMNS = """
    id, library_id, path, device_id, inode, file_size, modified_ns,
    file_name, mime_type, width, height, date_taken, imported_at,
    title, description, rating, favorite, hidden, missing
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS libraries (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_scan TEXT,
                available INTEGER NOT NULL DEFAULT 1,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY,
                library_id TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                device_id INTEGER,
                inode INTEGER,
                file_size INTEGER NOT NULL DEFAULT 0,
                modified_ns INTEGER NOT NULL DEFAULT 0,
                file_name TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                date_taken TEXT,
                imported_at TEXT NOT NULL,
                title TEXT,
                description TEXT,
                rating INTEGER NOT NULL DEFAULT 0 CHECK(rating BETWEEN 0 AND 5),
                favorite INTEGER NOT NULL DEFAULT 0,
                hidden INTEGER NOT NULL DEFAULT 0,
                missing INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (library_id) REFERENCES libraries(id)
            );

            CREATE TABLE IF NOT EXISTS albums (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS album_images (
                album_id TEXT NOT NULL,
                image_id TEXT NOT NULL,
                position INTEGER,
                PRIMARY KEY (album_id, image_id),
                FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
                FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );

            CREATE TABLE IF NOT EXISTS image_tags (
                image_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                PRIMARY KEY (image_id, tag_id),
                FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_images_library ON images(library_id);
            CREATE INDEX IF NOT EXISTS idx_images_identity ON images(library_id, device_id, inode);
            CREATE INDEX IF NOT EXISTS idx_images_date ON images(date_taken DESC);
            CREATE INDEX IF NOT EXISTS idx_images_favorite ON images(favorite);
            CREATE INDEX IF NOT EXISTS idx_images_missing ON images(missing);
            CREATE INDEX IF NOT EXISTS idx_album_images_image ON album_images(image_id);
            CREATE INDEX IF NOT EXISTS idx_image_tags_tag ON image_tags(tag_id);
            """
        )
        library_columns = {
            str(row["name"]) for row in self.connection.execute("PRAGMA table_info(libraries)")
        }
        if "available" not in library_columns:
            self.connection.execute(
                "ALTER TABLE libraries ADD COLUMN available INTEGER NOT NULL DEFAULT 1"
            )
        if "last_error" not in library_columns:
            self.connection.execute("ALTER TABLE libraries ADD COLUMN last_error TEXT")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    # Libraries ---------------------------------------------------------
    def libraries(self) -> list[Library]:
        rows = self.connection.execute(
            "SELECT id, path, name, created_at, last_scan, available, last_error FROM libraries ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [Library.from_row(row) for row in rows]

    def library(self, library_id: str) -> Library | None:
        row = self.connection.execute(
            "SELECT id, path, name, created_at, last_scan, available, last_error FROM libraries WHERE id = ?",
            (library_id,),
        ).fetchone()
        return Library.from_row(row) if row else None

    def indexed_folders(self, library_id: str) -> list[str]:
        rows = self.connection.execute(
            "SELECT DISTINCT path FROM images WHERE library_id = ?",
            (library_id,),
        ).fetchall()
        return sorted(
            {str(Path(str(row["path"])).parent) for row in rows},
            key=str.casefold,
        )


    def add_library(self, path: Path | str) -> Library:
        resolved = str(Path(path).expanduser().resolve())
        existing = self.connection.execute(
            "SELECT id, path, name, created_at, last_scan, available, last_error FROM libraries WHERE path = ?",
            (resolved,),
        ).fetchone()
        if existing:
            return Library.from_row(existing)
        library = Library(
            id=str(uuid.uuid4()),
            path=resolved,
            name=Path(resolved).name or resolved,
            created_at=utc_now(),
            last_scan=None,
            available=True,
            last_error=None,
        )
        self.connection.execute(
            "INSERT INTO libraries (id, path, name, created_at) VALUES (?, ?, ?, ?)",
            (library.id, library.path, library.name, library.created_at),
        )
        self.connection.commit()
        return library

    # Scanner-facing methods -------------------------------------------
    def set_library_availability(
        self,
        library_id: str,
        available: bool,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            "UPDATE libraries SET available = ?, last_error = ? WHERE id = ?",
            (int(available), error, library_id),
        )
        self.connection.commit()

    def relink_library(self, library_id: str, new_path: Path | str) -> Library:
        library = self.library(library_id)
        if library is None:
            raise ValueError("The registered folder no longer exists.")
        new_root = Path(new_path).expanduser().resolve()
        if not new_root.is_dir():
            raise ValueError("Choose an available folder.")
        old_root = Path(library.path)
        if new_root == old_root:
            self.set_library_availability(library_id, True)
            return self.library(library_id) or library

        rows = self.connection.execute(
            "SELECT id, path FROM images WHERE library_id = ?",
            (library_id,),
        ).fetchall()
        updates: list[tuple[str, str]] = []
        for row in rows:
            try:
                relative = Path(str(row["path"])).relative_to(old_root)
            except ValueError:
                continue
            destination = str(new_root / relative)
            conflict = self.connection.execute(
                "SELECT 1 FROM images WHERE path = ? AND library_id != ?",
                (destination, library_id),
            ).fetchone()
            if conflict:
                raise ValueError(f"Another indexed image already uses {destination}")
            updates.append((destination, str(row["id"])))

        with self.connection:
            self.connection.executemany(
                "UPDATE images SET path = ? WHERE id = ?",
                updates,
            )
            self.connection.execute(
                """
                UPDATE libraries
                SET path = ?, name = ?, available = 1, last_error = NULL
                WHERE id = ?
                """,
                (str(new_root), new_root.name or str(new_root), library_id),
            )
        updated = self.library(library_id)
        if updated is None:
            raise RuntimeError("The registered folder could not be updated.")
        return updated

    def upsert_scanned_image(
        self,
        *,
        library_id: str,
        path: str,
        device_id: int,
        inode: int,
        file_size: int,
        modified_ns: int,
        mime_type: str,
        width: int,
        height: int,
        date_taken: str | None,
    ) -> tuple[str, bool]:
        row = self.connection.execute(
            "SELECT id FROM images WHERE path = ?",
            (path,),
        ).fetchone()
        moved = False
        if row is None:
            row = self.connection.execute(
                """
                SELECT id FROM images
                WHERE library_id = ? AND device_id = ? AND inode = ?
                ORDER BY missing DESC, imported_at ASC LIMIT 1
                """,
                (library_id, device_id, inode),
            ).fetchone()
            moved = row is not None

        file_name = Path(path).name
        if row:
            image_id = str(row["id"])
            self.connection.execute(
                """
                UPDATE images SET
                    path = ?, device_id = ?, inode = ?, file_size = ?, modified_ns = ?,
                    file_name = ?, mime_type = ?, width = ?, height = ?, date_taken = ?, missing = 0
                WHERE id = ?
                """,
                (
                    path,
                    device_id,
                    inode,
                    file_size,
                    modified_ns,
                    file_name,
                    mime_type,
                    width,
                    height,
                    date_taken,
                    image_id,
                ),
            )
            return image_id, moved

        image_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO images (
                id, library_id, path, device_id, inode, file_size, modified_ns,
                file_name, mime_type, width, height, date_taken, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_id,
                library_id,
                path,
                device_id,
                inode,
                file_size,
                modified_ns,
                file_name,
                mime_type,
                width,
                height,
                date_taken,
                utc_now(),
            ),
        )
        return image_id, False

    def finish_library_scan(self, library_id: str, found_ids: Iterable[str]) -> None:
        found = [(image_id,) for image_id in found_ids]
        self.connection.execute("UPDATE images SET missing = 1 WHERE library_id = ?", (library_id,))
        if found:
            self.connection.executemany("UPDATE images SET missing = 0 WHERE id = ?", found)
        self.connection.execute(
            "UPDATE libraries SET last_scan = ?, available = 1, last_error = NULL WHERE id = ?",
            (utc_now(), library_id),
        )
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    # Queries -----------------------------------------------------------
    def images(
        self,
        *,
        mode: str = "all",
        value: str | None = None,
        search: str = "",
        sort: str = "date_desc",
    ) -> list[ImageRecord]:
        joins: list[str] = []
        conditions = ["images.hidden = 0"]
        params: list[object] = []

        if mode == "favorites":
            conditions.extend(["images.favorite = 1", "images.missing = 0"])
        elif mode == "missing":
            conditions.append("images.missing = 1")
        elif mode == "folder" and value:
            conditions.extend(["images.library_id = ?", "images.missing = 0"])
            params.append(value)
        elif mode == "folder_path" and value:
            prefix = f"{str(Path(value)).rstrip('/')}/"
            conditions.extend(
                ["SUBSTR(images.path, 1, ?) = ?", "images.missing = 0"]
            )
            params.extend([len(prefix), prefix])
        elif mode == "album" and value:
            joins.append("JOIN album_images ON album_images.image_id = images.id")
            conditions.append("album_images.album_id = ?")
            params.append(value)
        else:
            conditions.append("images.missing = 0")

        normalized = search.strip().casefold()
        if normalized:
            pattern = f"%{normalized}%"
            conditions.append(
                """
                (LOWER(images.file_name) LIKE ? OR LOWER(COALESCE(images.title, '')) LIKE ?
                 OR LOWER(COALESCE(images.description, '')) LIKE ? OR LOWER(images.path) LIKE ?
                 OR EXISTS (
                    SELECT 1 FROM image_tags
                    JOIN tags ON tags.id = image_tags.tag_id
                    WHERE image_tags.image_id = images.id AND LOWER(tags.name) LIKE ?
                 ))
                """
            )
            params.extend([pattern] * 5)

        order_by = {
            "date_desc": "COALESCE(images.date_taken, images.imported_at) DESC, images.file_name COLLATE NOCASE",
            "date_asc": "COALESCE(images.date_taken, images.imported_at) ASC, images.file_name COLLATE NOCASE",
            "name": "images.file_name COLLATE NOCASE ASC",
            "rating": "images.rating DESC, COALESCE(images.date_taken, images.imported_at) DESC",
            "size": "images.file_size DESC, images.file_name COLLATE NOCASE",
        }.get(sort, "COALESCE(images.date_taken, images.imported_at) DESC")

        sql = f"""
            SELECT {IMAGE_COLUMNS}
            FROM images
            {' '.join(joins)}
            WHERE {' AND '.join(conditions)}
            ORDER BY {order_by}
        """
        rows = self.connection.execute(sql, params).fetchall()
        return [ImageRecord.from_row(row) for row in rows]

    def image(self, image_id: str) -> ImageRecord | None:
        row = self.connection.execute(
            f"SELECT {IMAGE_COLUMNS} FROM images WHERE id = ?",
            (image_id,),
        ).fetchone()
        return ImageRecord.from_row(row) if row else None

    def image_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM images").fetchone()[0])

    def update_image_metadata(
        self,
        image_id: str,
        *,
        title: str,
        description: str,
        rating: int,
        favorite: bool,
    ) -> None:
        self.connection.execute(
            """
            UPDATE images SET title = ?, description = ?, rating = ?, favorite = ?
            WHERE id = ?
            """,
            (title.strip() or None, description.strip() or None, rating, int(favorite), image_id),
        )
        self.connection.commit()

    def set_favorite(self, image_ids: Sequence[str], favorite: bool) -> None:
        self.connection.executemany(
            "UPDATE images SET favorite = ? WHERE id = ?",
            [(int(favorite), image_id) for image_id in image_ids],
        )
        self.connection.commit()

    def update_file_path(self, image_id: str, new_path: str) -> None:
        stat = Path(new_path).stat()
        self.connection.execute(
            """
            UPDATE images SET path = ?, file_name = ?, device_id = ?, inode = ?,
                file_size = ?, modified_ns = ?, missing = 0
            WHERE id = ?
            """,
            (
                new_path,
                Path(new_path).name,
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                image_id,
            ),
        )
        self.connection.commit()

    # Tags --------------------------------------------------------------
    def tags_for_image(self, image_id: str) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT tags.name FROM tags
            JOIN image_tags ON image_tags.tag_id = tags.id
            WHERE image_tags.image_id = ? ORDER BY tags.name COLLATE NOCASE
            """,
            (image_id,),
        ).fetchall()
        return [str(row["name"]) for row in rows]

    def replace_tags(self, image_id: str, names: Iterable[str]) -> None:
        clean_names = sorted({name.strip() for name in names if name.strip()}, key=str.casefold)
        with self.connection:
            tag_ids: list[str] = []
            for name in clean_names:
                row = self.connection.execute(
                    "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
                ).fetchone()
                if row:
                    tag_ids.append(str(row["id"]))
                else:
                    tag_id = str(uuid.uuid4())
                    self.connection.execute("INSERT INTO tags (id, name) VALUES (?, ?)", (tag_id, name))
                    tag_ids.append(tag_id)
            self.connection.execute("DELETE FROM image_tags WHERE image_id = ?", (image_id,))
            self.connection.executemany(
                "INSERT INTO image_tags (image_id, tag_id) VALUES (?, ?)",
                [(image_id, tag_id) for tag_id in tag_ids],
            )
            self.connection.execute(
                "DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM image_tags WHERE image_tags.tag_id = tags.id)"
            )

    # Albums ------------------------------------------------------------
    def albums(self) -> list[Album]:
        rows = self.connection.execute(
            """
            SELECT albums.id, albums.name, albums.description, albums.created_at,
                   COUNT(album_images.image_id) AS image_count
            FROM albums LEFT JOIN album_images ON album_images.album_id = albums.id
            GROUP BY albums.id ORDER BY albums.name COLLATE NOCASE
            """
        ).fetchall()
        return [Album.from_row(row) for row in rows]

    def create_album(self, name: str, description: str = "") -> Album:
        album = Album(
            id=str(uuid.uuid4()),
            name=name.strip(),
            description=description.strip() or None,
            created_at=utc_now(),
        )
        if not album.name:
            raise ValueError("Album name cannot be empty")
        self.connection.execute(
            "INSERT INTO albums (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (album.id, album.name, album.description, album.created_at),
        )
        self.connection.commit()
        return album

    def delete_album(self, album_id: str) -> None:
        self.connection.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        self.connection.commit()

    def add_to_album(self, album_id: str, image_ids: Iterable[str]) -> int:
        before = self.connection.total_changes
        self.connection.executemany(
            "INSERT OR IGNORE INTO album_images (album_id, image_id) VALUES (?, ?)",
            [(album_id, image_id) for image_id in image_ids],
        )
        self.connection.commit()
        return self.connection.total_changes - before

    def remove_from_album(self, album_id: str, image_ids: Iterable[str]) -> int:
        before = self.connection.total_changes
        self.connection.executemany(
            "DELETE FROM album_images WHERE album_id = ? AND image_id = ?",
            [(album_id, image_id) for image_id in image_ids],
        )
        self.connection.commit()
        return self.connection.total_changes - before

    def albums_for_image(self, image_id: str) -> list[Album]:
        rows = self.connection.execute(
            """
            SELECT albums.id, albums.name, albums.description, albums.created_at, 0 AS image_count
            FROM albums JOIN album_images ON album_images.album_id = albums.id
            WHERE album_images.image_id = ? ORDER BY albums.name COLLATE NOCASE
            """,
            (image_id,),
        ).fetchall()
        return [Album.from_row(row) for row in rows]
