from __future__ import annotations

from pathlib import Path

from .database import Database
from .models import ImageRecord


class FileOperationError(RuntimeError):
    pass


class FileOperations:
    """The single boundary for operations that change original files."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def rename_file(self, image: ImageRecord, new_name: str) -> Path:
        clean_name = new_name.strip()
        if not clean_name or clean_name in {".", ".."}:
            raise FileOperationError("Enter a valid file name.")
        if any(separator in clean_name for separator in ("/", "\\", "\0")):
            raise FileOperationError("A file name cannot contain path separators.")

        source = Path(image.path)
        if not source.exists():
            raise FileOperationError("The original file is unavailable.")
        destination = source.with_name(clean_name)
        if destination == source:
            return source
        if destination.exists():
            raise FileOperationError("A file with that name already exists in this folder.")

        try:
            source.rename(destination)
        except OSError as error:
            raise FileOperationError(str(error)) from error

        try:
            self.database.update_file_path(image.id, str(destination))
        except Exception as error:
            try:
                destination.rename(source)
            except OSError:
                pass
            raise FileOperationError(f"The database could not be updated: {error}") from error
        return destination
