import threading
from dataclasses import dataclass

import pytest

from core.archive_migration import (
    ArchiveFolderEntry,
    ArchiveFolderStatus,
    ArchiveMigrationState,
    ArchiveScanResult,
)
from core.archive_migration_queue import (
    ArchiveQueueOptions,
    build_archive_queue_items,
    prepare_archive_queue,
    run_archive_queue,
)


def _state(tmp_path):
    anapa = tmp_path / "Anapa"
    azov = tmp_path / "Azov"
    anapa.mkdir()
    azov.mkdir()
    state = ArchiveMigrationState()
    state.apply_scan(
        ArchiveScanResult(
            root_path=str(tmp_path),
            folders=[
                ArchiveFolderEntry(path=str(anapa), name="Anapa", file_count=19),
                ArchiveFolderEntry(path=str(azov), name="Azov", file_count=131),
            ],
        )
    )
    return state, anapa, azov


def _config_state():
    return {
        "server": "http://immich.test:2283",
        "api_key": "test-key",
        "admin_api_key": "",
        "skip-ssl": False,
        "client_timeout_minutes": 60,
    }


def test_queue_plan_reuses_upload_folder_and_maps_folder_to_album(tmp_path):
    state, anapa, _ = _state(tmp_path)

    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
        options=ArchiveQueueOptions(tag="cloud/krasnodar", session_tag=True),
    )

    assert len(items) == 1
    argv = items[0].plan.argv
    assert argv[:2] == ["upload", "from-folder"]
    assert "--into-album=Anapa" in argv
    assert "--tag=cloud/krasnodar" in argv
    assert "--session-tag" in argv
    assert "--no-ui" in argv
    assert "--recursive" in argv
    assert str(anapa) == argv[-1]
    assert not any(arg.startswith("--date-range") for arg in argv)


def test_queue_does_not_inherit_upload_folder_advanced_filters(tmp_path):
    state, anapa, _ = _state(tmp_path)

    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
        base_advanced_state={
            "date-range": {"enabled": True, "value": "2020-01-01,2020-01-31"},
            "exclude-ext": {"enabled": True, "value": ".jpg"},
            "overwrite": {"enabled": True, "value": True},
            "recursive": {"enabled": True, "value": False},
        },
    )

    argv = items[0].plan.argv
    assert "--recursive" in argv
    assert not any(arg.startswith("--date-range") for arg in argv)
    assert not any(arg.startswith("--exclude-extensions") for arg in argv)
    assert "--overwrite" not in argv


def test_queue_rejects_done_folder(tmp_path):
    state, anapa, _ = _state(tmp_path)
    state.get(str(anapa)).status = ArchiveFolderStatus.DONE

    with pytest.raises(ValueError, match="cannot be queued"):
        build_archive_queue_items(
            state,
            [str(anapa)],
            config_state=_config_state(),
            binary_path="immich-go",
        )


def test_prepare_marks_selected_folders_ready_and_persists(tmp_path):
    state, anapa, azov = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa), str(azov)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    snapshots = []

    prepare_archive_queue(
        state, items, persist=lambda current: snapshots.append(current.to_dict())
    )

    assert state.get(str(anapa)).status == ArchiveFolderStatus.READY
    assert state.get(str(azov)).status == ArchiveFolderStatus.READY
    assert len(snapshots) == 1


@dataclass
class _Result:
    success: bool
    message: str = ""
    files_uploaded: int = 0


def test_sequential_queue_persists_done_after_every_folder(tmp_path):
    state, anapa, azov = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa), str(azov)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    prepare_archive_queue(state, items)
    persisted = []
    executed = []

    summary = run_archive_queue(
        state,
        items,
        execute=lambda item: executed.append(item.name) or _Result(True),
        persist=lambda current: persisted.append(current.to_dict()),
    )

    assert executed == ["Anapa", "Azov"]
    assert state.get(str(anapa)).status == ArchiveFolderStatus.DONE
    assert state.get(str(azov)).status == ArchiveFolderStatus.DONE
    assert summary.done == 2
    assert summary.errors == 0
    # UPLOADING + terminal state for each item.
    assert len(persisted) == 4


def test_queue_stops_on_first_error_and_leaves_rest_ready(tmp_path):
    state, anapa, azov = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa), str(azov)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    prepare_archive_queue(state, items)

    summary = run_archive_queue(
        state,
        items,
        execute=lambda _item: _Result(False, "boom"),
        persist=lambda _current: None,
        stop_on_error=True,
    )

    assert state.get(str(anapa)).status == ArchiveFolderStatus.ERROR
    assert state.get(str(azov)).status == ArchiveFolderStatus.READY
    assert summary.errors == 1
    assert summary.not_started == 1


def test_failed_upload_with_progress_becomes_partial(tmp_path):
    state, anapa, _ = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    prepare_archive_queue(state, items)

    summary = run_archive_queue(
        state,
        items,
        execute=lambda _item: _Result(False, "connection lost", files_uploaded=3),
        persist=lambda _current: None,
    )

    assert state.get(str(anapa)).status == ArchiveFolderStatus.PARTIAL
    assert summary.partial == 1


@dataclass
class _Verification:
    success: bool
    message: str = ""
    actual_assets: int | None = None
    expected_assets: int = 19


def test_successful_cli_run_requires_album_verification_before_done(tmp_path):
    state, anapa, _ = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    prepare_archive_queue(state, items)

    summary = run_archive_queue(
        state,
        items,
        execute=lambda _item: _Result(True),
        verify=lambda _item, _result: _Verification(
            False,
            "Album verification failed: expected at least 19 assets, found 0",
            actual_assets=0,
        ),
        persist=lambda _current: None,
    )

    entry = state.get(str(anapa))
    assert entry.status == ArchiveFolderStatus.ERROR
    assert "expected at least 19 assets, found 0" in (entry.last_error or "")
    assert summary.done == 0
    assert summary.errors == 1


def test_verification_shortfall_with_progress_becomes_partial(tmp_path):
    state, anapa, _ = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    prepare_archive_queue(state, items)

    summary = run_archive_queue(
        state,
        items,
        execute=lambda _item: _Result(True, files_uploaded=3),
        verify=lambda _item, _result: _Verification(
            False,
            "Album verification failed: expected at least 19 assets, found 3",
            actual_assets=3,
        ),
        persist=lambda _current: None,
    )

    assert state.get(str(anapa)).status == ArchiveFolderStatus.PARTIAL
    assert summary.partial == 1


def test_verified_album_allows_done(tmp_path):
    state, anapa, _ = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    prepare_archive_queue(state, items)

    summary = run_archive_queue(
        state,
        items,
        execute=lambda _item: _Result(True),
        verify=lambda _item, _result: _Verification(
            True,
            "Album verified: 19 assets",
            actual_assets=19,
        ),
        persist=lambda _current: None,
    )

    assert state.get(str(anapa)).status == ArchiveFolderStatus.DONE
    assert summary.done == 1


def test_verification_extra_album_assets_is_error_not_partial(tmp_path):
    state, anapa, _ = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    prepare_archive_queue(state, items)

    summary = run_archive_queue(
        state,
        items,
        execute=lambda _item: _Result(True, files_uploaded=3),
        verify=lambda _item, _result: _Verification(
            False,
            "Album verification failed: expected 19 assets, found 20",
            actual_assets=20,
            expected_assets=19,
        ),
        persist=lambda _current: None,
    )

    assert state.get(str(anapa)).status == ArchiveFolderStatus.ERROR
    assert summary.errors == 1
    assert summary.partial == 0


def test_cancelled_current_folder_becomes_partial_not_error(tmp_path):
    state, anapa, _ = _state(tmp_path)
    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
    )
    prepare_archive_queue(state, items)
    cancel_event = threading.Event()

    def execute(_item):
        cancel_event.set()
        return _Result(False, message="Upload cancelled", files_uploaded=0)

    summary = run_archive_queue(
        state,
        items,
        execute=execute,
        persist=lambda _current: None,
        cancel_event=cancel_event,
    )

    entry = state.get(str(anapa))
    assert entry.status == ArchiveFolderStatus.PARTIAL
    assert entry.last_error == "Upload cancelled"
    assert summary.partial == 1
    assert summary.errors == 0
    assert summary.cancelled is True


def test_done_folder_can_be_queued_only_for_album_sync(tmp_path):
    state, anapa, _ = _state(tmp_path)
    state.get(str(anapa)).status = ArchiveFolderStatus.DONE

    items = build_archive_queue_items(
        state,
        [str(anapa)],
        config_state=_config_state(),
        binary_path="immich-go",
        options=ArchiveQueueOptions(sync_album=True),
    )

    assert len(items) == 1
    assert items[0].name == "Anapa"

    prepare_archive_queue(state, items, allow_done=True)

    assert state.get(str(anapa)).status == ArchiveFolderStatus.READY
