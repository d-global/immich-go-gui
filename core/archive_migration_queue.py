"""Queue planning and lifecycle orchestration for Archive Migration.

The module deliberately reuses the upstream ``upload-folder`` command builder.
It does not know about Qt and it does not store credentials.  Secrets stay in
the existing profile/keyring flow and are passed in through ``config_state``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .archive_migration import (
    ArchiveFolderEntry,
    ArchiveFolderStatus,
    ArchiveMigrationState,
)
from .cli_schema import TAB_COMMANDS
from .command_builder import build_plan_from_state, mask_command_for_display
from .models import CommandPlan


@dataclass(frozen=True)
class ArchiveQueueOptions:
    """Options shared by one sequential archive migration queue."""

    tag: str = ""
    session_tag: bool = True
    stop_on_error: bool = True


@dataclass
class ArchiveQueueItem:
    """One first-level folder and its prepared upstream command plan."""

    path: str
    name: str
    album_name: str
    file_count: int
    size_bytes: int
    plan: CommandPlan


@dataclass
class ArchiveQueueSummary:
    """Outcome of a queue run."""

    total: int = 0
    done: int = 0
    partial: int = 0
    errors: int = 0
    not_started: int = 0
    cancelled: bool = False
    processed_paths: list[str] = field(default_factory=list)


class QueueExecutionResult(Protocol):
    """Small compatibility surface shared with ``folder_runner.UploadResult``."""

    success: bool
    message: str
    files_uploaded: int


QueueExecutor = Callable[[ArchiveQueueItem], QueueExecutionResult]
QueuePersist = Callable[[ArchiveMigrationState], None]
QueueProgress = Callable[[int, int, ArchiveQueueItem, str], None]


_ALLOWED_PREPARE_STATUSES = {
    ArchiveFolderStatus.TODO,
    ArchiveFolderStatus.READY,
    ArchiveFolderStatus.ERROR,
    ArchiveFolderStatus.PARTIAL,
}


def build_archive_queue_items(
    state: ArchiveMigrationState,
    selected_paths: Iterable[str],
    *,
    config_state: dict[str, Any],
    binary_path: str,
    options: ArchiveQueueOptions | None = None,
    base_advanced_state: dict[str, Any] | None = None,
) -> list[ArchiveQueueItem]:
    """Build one validated upstream ``upload-folder`` plan per selected folder.

    Every first-level folder is mapped to exactly one Immich album named after
    the folder.  Nested folders remain recursive input inside that album.
    ``DONE`` and ``SKIP`` entries are intentionally rejected rather than being
    silently re-uploaded.
    """

    options = options or ArchiveQueueOptions()
    items: list[ArchiveQueueItem] = []
    seen: set[str] = set()

    for path in selected_paths:
        entry = state.get(path)
        if entry is None or entry.path in seen:
            continue
        seen.add(entry.path)
        _validate_prepare_status(entry)

        advanced_state = _merged_advanced_state(
            base_advanced_state,
            tag=options.tag,
            session_tag=options.session_tag,
        )
        tab_state = {
            "path": entry.path,
            "folder-album": "NONE",
            "into-album": entry.name,
            "manage-burst": "NoStack",
            "manage-raw-jpeg": "NoStack",
            "manage-heic-jpeg": "NoStack",
        }
        plan = build_plan_from_state(
            tab_key="upload-folder",
            config_state=config_state,
            tab_state=tab_state,
            binary_path=binary_path,
            dry_run=False,
            advanced_state=advanced_state,
        )
        if plan.errors:
            raise ValueError(f"{entry.name}: {'; '.join(plan.errors)}")

        # ``no-ui`` is an internal runner concern and is intentionally hidden
        # from the normal advanced flag registry.  Like the existing Monitor
        # runner, Archive Migration executes immich-go with captured pipes, so
        # the TUI must be disabled explicitly before the positional path.
        command_len = len(TAB_COMMANDS.get("upload-folder", ()))
        if "--no-ui" not in plan.argv:
            plan.argv.insert(command_len, "--no-ui")
            plan.display_argv = mask_command_for_display([binary_path] + plan.argv)

        items.append(
            ArchiveQueueItem(
                path=entry.path,
                name=entry.name,
                album_name=entry.name,
                file_count=entry.file_count,
                size_bytes=entry.size_bytes,
                plan=plan,
            )
        )

    return items


def prepare_archive_queue(
    state: ArchiveMigrationState,
    items: Iterable[ArchiveQueueItem],
    *,
    persist: QueuePersist | None = None,
) -> None:
    """Mark prepared queue entries READY and persist once."""

    changed = False
    for item in items:
        entry = state.get(item.path)
        if entry is None:
            continue
        _validate_prepare_status(entry)
        entry.status = ArchiveFolderStatus.READY
        entry.last_error = None
        changed = True
    if changed and persist is not None:
        persist(state)


def run_archive_queue(
    state: ArchiveMigrationState,
    items: list[ArchiveQueueItem],
    *,
    execute: QueueExecutor,
    persist: QueuePersist,
    stop_on_error: bool = True,
    cancel_event: threading.Event | None = None,
    on_progress: QueueProgress | None = None,
) -> ArchiveQueueSummary:
    """Execute prepared folders sequentially and persist after every folder.

    The executor is injected so the orchestration stays testable.  The GUI
    connects it to the existing upstream hidden ``folder_runner`` rather than
    implementing another subprocess/credential stack.
    """

    summary = ArchiveQueueSummary(total=len(items), not_started=len(items))

    for index, item in enumerate(items, start=1):
        if cancel_event is not None and cancel_event.is_set():
            summary.cancelled = True
            break

        entry = state.get(item.path)
        if entry is None:
            summary.errors += 1
            summary.not_started -= 1
            if stop_on_error:
                break
            continue
        if entry.status not in {
            ArchiveFolderStatus.READY,
            ArchiveFolderStatus.ERROR,
            ArchiveFolderStatus.PARTIAL,
        }:
            raise ValueError(
                f"Folder '{entry.name}' must be READY/ERROR/PARTIAL before execution, got {entry.status.value}"
            )

        entry.status = ArchiveFolderStatus.UPLOADING
        entry.last_error = None
        persist(state)
        if on_progress is not None:
            on_progress(index, len(items), item, ArchiveFolderStatus.UPLOADING.value)

        try:
            result = execute(item)
        except Exception as exc:
            result = _FailedExecutionResult(str(exc))

        summary.not_started -= 1
        summary.processed_paths.append(item.path)

        if result.success:
            entry.status = ArchiveFolderStatus.DONE
            entry.last_error = None
            summary.done += 1
        elif getattr(result, "files_uploaded", 0) > 0:
            entry.status = ArchiveFolderStatus.PARTIAL
            entry.last_error = result.message or "Upload stopped after partial progress"
            summary.partial += 1
        else:
            entry.status = ArchiveFolderStatus.ERROR
            entry.last_error = result.message or "Upload failed"
            summary.errors += 1

        persist(state)
        if on_progress is not None:
            on_progress(index, len(items), item, entry.status.value)

        if not result.success and stop_on_error:
            break

    if cancel_event is not None and cancel_event.is_set():
        summary.cancelled = True
    return summary


def _validate_prepare_status(entry: ArchiveFolderEntry) -> None:
    if entry.status not in _ALLOWED_PREPARE_STATUSES:
        raise ValueError(
            f"Folder '{entry.name}' with status {entry.status.value} cannot be queued"
        )


def _merged_advanced_state(
    base: dict[str, Any] | None,
    *,
    tag: str,
    session_tag: bool,
) -> dict[str, Any]:
    # Archive Migration has its own deliberately small policy surface. Do not
    # inherit arbitrary Upload Folder advanced settings (date ranges, extension
    # filters, overwrite, ban-file, etc.) from another workflow: doing so could
    # silently skip or alter archive content. The base argument remains in the
    # signature for compatibility with the GUI caller but is intentionally ignored.
    _ = base
    merged: dict[str, Any] = {
        # One first-level queue item must include all nested folders.
        "recursive": {"enabled": True, "value": True},
    }

    clean_tag = tag.strip()
    merged["tag"] = {"enabled": bool(clean_tag), "value": clean_tag}
    merged["session-tag"] = {"enabled": session_tag, "value": session_tag}
    return merged


@dataclass
class _FailedExecutionResult:
    message: str
    success: bool = False
    files_uploaded: int = 0
