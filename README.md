# Luma

Luma is a filesystem-first photo library for Linux. It indexes the folders you already use and adds virtual organization without importing, copying, or rearranging the original images.

> **The filesystem owns the original files; Luma owns virtual organization and descriptions.**

Filesystem operations are deliberately distinct from application-only metadata. Renaming a file in Luma renames the original file, while albums, tags, ratings, favorites, titles, and descriptions remain in Luma's SQLite database.

## Current features

- Register existing picture folders and scan their subfolders recursively.
- Browse the indexed filesystem hierarchy or search for a specific folder.
- View a responsive gallery backed by cached, lazily loaded thumbnails.
- Search and sort photos; use multi-selection for bulk organization.
- Create virtual albums and add photos with drag and drop without moving or copying files.
- Add tags, ratings, favorites, titles, and descriptions as application metadata.
- Open photos in an integrated viewer with a filmstrip, metadata panel, wheel zoom, and drag panning.
- Rename original files with collision checks and database rollback on failure.
- Reconcile external renames using filesystem identity where possible.
- Preserve records and virtual metadata when files or registered drives are temporarily unavailable.
- Relink an unavailable library after a removable drive returns at a different mount path.
- Switch between persistent light and dark themes.

Supported image extensions currently include JPEG, PNG, WebP, GIF, BMP, and TIFF.

## Installation and running

Luma requires Linux and Python 3.11 or newer. [`uv`](https://docs.astral.sh/uv/) is the recommended environment and package manager.

```bash
git clone https://github.com/1RV1NG-Y/luma-images-linux.git
cd luma-images-linux
uv sync
uv run luma-gallery
```

On the first launch, choose **Add folder** and select a directory containing images. Luma will index it in the background and generate thumbnails as they become visible.

## File and metadata ownership

Luma does not import the selected images into a managed library. Their existing paths and folder structure remain authoritative.

| Operation | Stored in Luma | Changes an original file |
| --- | ---: | ---: |
| Add to an album | Yes | No |
| Add tags, ratings, titles, or descriptions | Yes | No |
| Mark as favorite | Yes | No |
| Rename file | Updated record | Yes |

The catalog database and thumbnail cache are stored in the platform's Qt/XDG application-data and cache locations. They are not created inside indexed picture folders.

Luma is still an early-stage application. Keep normal backups of important files, particularly when using operations that explicitly affect originals.

## Development

Install dependencies and run the application:

```bash
uv sync
uv run luma-gallery
```

Run the behavioral test suite and static checks:

```bash
uv run python -m unittest discover -s tests -v
uvx ruff check .
```

Build the source distribution and wheel:

```bash
uv build
```

## Planned work

Later development may include:

- Rule-driven actions that preview and organize matching photos into regular or smart albums.
- Album creation from tags, capture dates, years, months, and date ranges.
- Clearly labeled dry runs and execution history for organization actions.
- A consistent icon library for folders and albums.
- User-selectable, application-only icons for registered folders and virtual albums.

Filesystem-changing automation is intentionally outside the initial actions work; the first version will focus on safe virtual organization.
