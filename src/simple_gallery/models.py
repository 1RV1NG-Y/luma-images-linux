from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ImageRecord:
    id: str
    library_id: str
    path: str
    device_id: int | None
    inode: int | None
    file_size: int
    modified_ns: int
    file_name: str
    mime_type: str
    width: int
    height: int
    date_taken: str | None
    imported_at: str
    title: str | None
    description: str | None
    rating: int
    favorite: bool
    hidden: bool
    missing: bool

    @classmethod
    def from_row(cls, row: Any) -> "ImageRecord":
        values = dict(row)
        values["favorite"] = bool(values["favorite"])
        values["hidden"] = bool(values["hidden"])
        values["missing"] = bool(values["missing"])
        return cls(**values)

    @property
    def display_name(self) -> str:
        return self.title or self.file_name

    @property
    def parent_path(self) -> str:
        return str(Path(self.path).parent)


@dataclass(slots=True)
class Library:
    id: str
    path: str
    name: str
    created_at: str
    last_scan: str | None
    available: bool = True
    last_error: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Library":
        values = dict(row)
        values["available"] = bool(values["available"])
        return cls(**values)


@dataclass(slots=True)
class Album:
    id: str
    name: str
    description: str | None
    created_at: str
    image_count: int = 0

    @classmethod
    def from_row(cls, row: Any) -> "Album":
        return cls(**dict(row))
