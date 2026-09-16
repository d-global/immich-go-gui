"""State and scanning primitives for controlled archive migration.

The archive migration workflow is intentionally separate from the Backup
Monitor.  Monitor state answers "what changed since the last successful
backup?" while archive migration state answers "which first-level archive
folders have already been deliberately migrated?".

This module is Qt-free so the workflow can be tested headlessly and reused by
future UI/CLI integrations.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .config_manager import _atomic_write_text, default_config_path


class ArchiveFolderStatus(str, Enum):
    """Persistent lifecycle for a first-level archive folder."""

    TODO = "TODO"
    READY = "READY"
    UPLOADING = "UPLOADING"
    DONE = "DONE"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"
    SKIP = "SKIP"


@dataclass
class ArchiveFolderEntry:
    """Persistent state and latest scan totals for one first-level folder."""

    path: str
    name: str
    file_count: int = 0
    size_bytes: int = 0
    status: ArchiveFolderStatus = ArchiveFolderStatus.TODO
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
            "status": self.status.value,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchiveFolderEntry:
        raw_status = data.get("status", ArchiveFolderStatus.TODO.value)
        try:
            status = ArchiveFolderStatus(raw_status)
        except (TypeError, ValueError):
            status = ArchiveFolderStatus.TODO
        return cls(
            path=str(data.get("path", "")),
            name=str(data.get("name", "")),
            file_count=_coerce_non_negative_int(data.get("file_count", 0)),
            size_bytes=_coerce_non_negative_int(data.get("size_bytes", 0)),
            status=status,
            last_error=(
                str(data["last_error"])
                if data.get("last_error") not in (None, "")
                else None
            ),
        )


@dataclass
class ArchiveScanResult:
    """Fresh disk inventory for one archive root."""

    root_path: str
    folders: list[ArchiveFolderEntry] = field(default_factory=list)
    root_file_count: int = 0
    root_size_bytes: int = 0
    scan_errors: list[str] = field(default_factory=list)


@dataclass
class ArchiveMigrationState:
    """Persistent archive migration state for the active profile."""

    schema_version: int = 1
    root_path: str = ""
    folders: dict[str, ArchiveFolderEntry] = field(default_factory=dict)
    root_file_count: int = 0
    root_size_bytes: int = 0

    def get(self, folder_path: str) -> ArchiveFolderEntry | None:
        return self.folders.get(_path_key(folder_path))

    def apply_scan(self, result: ArchiveScanResult) -> None:
        """Replace inventory totals while preserving lifecycle decisions.

        Status and the last error are retained for folders that still exist at
        the same path.  Deleted folders disappear from the active state rather
        than lingering as phantom queue items.
        """

        previous = self.folders
        merged: dict[str, ArchiveFolderEntry] = {}
        for scanned in result.folders:
            key = _path_key(scanned.path)
            old = previous.get(key)
            if old is not None:
                scanned.status = old.status
                scanned.last_error = old.last_error
            merged[key] = scanned

        self.root_path = result.root_path
        self.root_file_count = result.root_file_count
        self.root_size_bytes = result.root_size_bytes
        self.folders = merged

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "root_path": self.root_path,
            "root_file_count": self.root_file_count,
            "root_size_bytes": self.root_size_bytes,
            "folders": {
                key: entry.to_dict() for key, entry in sorted(self.folders.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchiveMigrationState:
        state = cls(
            schema_version=_coerce_non_negative_int(data.get("schema_version", 1)) or 1,
            root_path=str(data.get("root_path", "")),
            root_file_count=_coerce_non_negative_int(data.get("root_file_count", 0)),
            root_size_bytes=_coerce_non_negative_int(data.get("root_size_bytes", 0)),
        )
        raw_folders = data.get("folders", {})
        if isinstance(raw_folders, dict):
            for value in raw_folders.values():
                if not isinstance(value, dict):
                    continue
                entry = ArchiveFolderEntry.from_dict(value)
                if entry.path:
                    state.folders[_path_key(entry.path)] = entry
        return state


class ArchiveMigrationStateStore:
    """Atomic per-profile persistence for archive migration state."""

    _write_lock = threading.Lock()

    @staticmethod
    def resolve_path(profile_name: str | None = None) -> Path:
        return default_config_path(profile_name).parent / "archive_migration_state.json"

    @classmethod
    def load(cls, profile_name: str | None = None) -> ArchiveMigrationState:
        import json

        path = cls.resolve_path(profile_name)
        if not path.exists():
            return ArchiveMigrationState()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return ArchiveMigrationState()
            return ArchiveMigrationState.from_dict(data)
        except (OSError, TypeError, ValueError):
            return ArchiveMigrationState()

    @classmethod
    def save(
        cls, state: ArchiveMigrationState, profile_name: str | None = None
    ) -> None:
        import json

        path = cls.resolve_path(profile_name)
        with cls._write_lock:
            _atomic_write_text(
                path,
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                mode=0o600,
            )


class ArchiveScanCancelled(RuntimeError):
    """Raised when a caller explicitly cancels an archive scan."""


ArchiveProgressCallback = Callable[[int, int, str, int, int], None]


def scan_archive_root(
    root_path: str,
    *,
    cancel_event: threading.Event | None = None,
    on_progress: ArchiveProgressCallback | None = None,
) -> ArchiveScanResult:
    """Scan immediate child folders and count their contents recursively.

    Only first-level directories become queue entries.  Nested folders are
    folded into their first-level parent so that the future queue can map one
    archive folder to one Immich album.  Files directly in the archive root
    are counted separately and are never silently mixed into a child album.
    """

    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    result = ArchiveScanResult(root_path=str(root))
    children: list[Path] = []

    try:
        with os.scandir(root) as entries:
            for entry in entries:
                _raise_if_cancelled(cancel_event)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        children.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        result.root_file_count += 1
                        try:
                            result.root_size_bytes += entry.stat(
                                follow_symlinks=False
                            ).st_size
                        except OSError as exc:
                            result.scan_errors.append(f"{entry.path}: {exc}")
                except OSError as exc:
                    result.scan_errors.append(f"{entry.path}: {exc}")
    except OSError as exc:
        raise OSError(f"Cannot scan archive root {root}: {exc}") from exc

    children.sort(key=lambda path: path.name.casefold())
    total = len(children)
    for index, child in enumerate(children, start=1):
        _raise_if_cancelled(cancel_event)
        file_count, size_bytes, errors = _scan_tree(
            child,
            cancel_event=cancel_event,
            progress=(
                lambda count, size, idx=index, path=child: on_progress(
                    idx, total, str(path), count, size
                )
                if on_progress is not None
                else None
            ),
        )
        result.scan_errors.extend(errors)
        result.folders.append(
            ArchiveFolderEntry(
                path=str(child.resolve()),
                name=child.name,
                file_count=file_count,
                size_bytes=size_bytes,
            )
        )
        if on_progress is not None:
            on_progress(index, total, str(child), file_count, size_bytes)

    return result


def _scan_tree(
    folder: Path,
    *,
    cancel_event: threading.Event | None,
    progress: Callable[[int, int], None] | None,
) -> tuple[int, int, list[str]]:
    file_count = 0
    size_bytes = 0
    errors: list[str] = []

    def on_walk_error(exc: OSError) -> None:
        errors.append(f"{getattr(exc, 'filename', folder)}: {exc}")

    for current_root, dirs, files in os.walk(
        folder, topdown=True, onerror=on_walk_error, followlinks=False
    ):
        _raise_if_cancelled(cancel_event)
        # Never recurse through directory symlinks/junction-like links when
        # Python can identify them.  Archive scans should stay inside the
        # selected first-level folder and must not accidentally traverse a
        # second copy of a library.
        dirs[:] = [
            name
            for name in dirs
            if not os.path.islink(os.path.join(current_root, name))
        ]
        for filename in files:
            _raise_if_cancelled(cancel_event)
            path = os.path.join(current_root, filename)
            try:
                if os.path.islink(path):
                    continue
                size_bytes += os.path.getsize(path)
                file_count += 1
            except OSError as exc:
                errors.append(f"{path}: {exc}")
            if progress is not None and file_count % 100 == 0:
                progress(file_count, size_bytes)

    return file_count, size_bytes, errors


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ArchiveScanCancelled("Archive scan cancelled")


def _coerce_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
