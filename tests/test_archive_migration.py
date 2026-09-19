import threading

import pytest

from core.archive_migration import (
    ArchiveFolderEntry,
    ArchiveFolderStatus,
    ArchiveMigrationState,
    ArchiveMigrationStateStore,
    ArchiveScanCancelled,
    ArchiveScanResult,
    scan_archive_root,
)


def test_scan_archive_root_creates_only_first_level_queue_entries(tmp_path):
    root_file = tmp_path / "root.jpg"
    root_file.write_bytes(b"root")

    trip = tmp_path / "Trip"
    trip.mkdir()
    (trip / "a.jpg").write_bytes(b"abc")
    nested = trip / "Nested"
    nested.mkdir()
    (nested / "b.mp4").write_bytes(b"12345")

    birthday = tmp_path / "Birthday"
    birthday.mkdir()
    (birthday / "c.jpg").write_bytes(b"12")

    result = scan_archive_root(str(tmp_path))

    assert [entry.name for entry in result.folders] == ["Birthday", "Trip"]
    assert result.root_file_count == 1
    assert result.root_size_bytes == 4

    birthday_entry = result.folders[0]
    assert birthday_entry.file_count == 1
    assert birthday_entry.size_bytes == 2

    trip_entry = result.folders[1]
    assert trip_entry.file_count == 2
    assert trip_entry.size_bytes == 8


def test_apply_scan_preserves_existing_folder_status(tmp_path):
    folder = tmp_path / "Already Uploaded"
    folder.mkdir()
    (folder / "photo.jpg").write_bytes(b"one")

    first_scan = scan_archive_root(str(tmp_path))
    state = ArchiveMigrationState()
    state.apply_scan(first_scan)

    entry = state.get(str(folder))
    assert entry is not None
    entry.status = ArchiveFolderStatus.DONE

    (folder / "second.jpg").write_bytes(b"two-two")
    state.apply_scan(scan_archive_root(str(tmp_path)))

    rescanned = state.get(str(folder))
    assert rescanned is not None
    assert rescanned.status == ArchiveFolderStatus.DONE
    assert rescanned.file_count == 2
    assert rescanned.size_bytes == 10


def test_apply_scan_drops_folders_that_no_longer_exist(tmp_path):
    old_folder = tmp_path / "Old"
    old_folder.mkdir()
    state = ArchiveMigrationState()
    state.apply_scan(scan_archive_root(str(tmp_path)))
    assert state.get(str(old_folder)) is not None

    old_folder.rmdir()
    state.apply_scan(scan_archive_root(str(tmp_path)))

    assert state.get(str(old_folder)) is None
    assert state.folders == {}


def test_archive_migration_state_store_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "archive_migration_state.json"
    monkeypatch.setattr(ArchiveMigrationStateStore, "resolve_path", lambda *_: path)

    folder = tmp_path / "Family"
    folder.mkdir()
    (folder / "photo.jpg").write_bytes(b"data")

    state = ArchiveMigrationState()
    state.apply_scan(scan_archive_root(str(tmp_path)))
    entry = state.get(str(folder))
    assert entry is not None
    entry.status = ArchiveFolderStatus.ERROR
    entry.last_error = "network failed"

    ArchiveMigrationStateStore.save(state)
    restored = ArchiveMigrationStateStore.load()

    restored_entry = restored.get(str(folder))
    assert restored.root_path == state.root_path
    assert restored_entry is not None
    assert restored_entry.status == ArchiveFolderStatus.ERROR
    assert restored_entry.last_error == "network failed"
    assert not path.with_suffix(".tmp").exists()


def test_archive_migration_state_is_profile_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.archive_migration.default_config_path",
        lambda profile_name=None: (
            tmp_path / (profile_name or "default") / "config.toml"
        ),
    )
    state = ArchiveMigrationState(root_path="C:/Archive")

    ArchiveMigrationStateStore.save(state, "denis")

    assert (tmp_path / "denis" / "archive_migration_state.json").exists()
    assert ArchiveMigrationStateStore.load("denis").root_path == "C:/Archive"
    assert ArchiveMigrationStateStore.load("alena").root_path == ""


def test_scan_archive_root_supports_explicit_cancellation(tmp_path):
    child = tmp_path / "Many"
    child.mkdir()
    for index in range(150):
        (child / f"{index:03}.jpg").write_bytes(b"x")

    cancel_event = threading.Event()

    def on_progress(_index, _total, _path, file_count, _size_bytes):
        if file_count >= 100:
            cancel_event.set()

    with pytest.raises(ArchiveScanCancelled):
        scan_archive_root(
            str(tmp_path), cancel_event=cancel_event, on_progress=on_progress
        )


def test_unknown_persisted_status_falls_back_to_todo():
    state = ArchiveMigrationState.from_dict(
        {
            "folders": {
                "x": {
                    "path": "C:/Archive/Test",
                    "name": "Test",
                    "status": "SOMETHING_OLD",
                }
            }
        }
    )

    entry = state.get("C:/Archive/Test")
    assert entry is not None
    assert entry.status == ArchiveFolderStatus.TODO


def test_recover_interrupted_uploads_marks_partial(tmp_path):
    folder = tmp_path / "Interrupted"
    folder.mkdir()
    state = ArchiveMigrationState()
    state.apply_scan(
        ArchiveScanResult(
            root_path=str(tmp_path),
            folders=[ArchiveFolderEntry(path=str(folder), name="Interrupted")],
        )
    )
    entry = state.get(str(folder))
    entry.status = ArchiveFolderStatus.UPLOADING

    recovered = state.recover_interrupted_uploads()

    assert recovered == 1
    assert entry.status == ArchiveFolderStatus.PARTIAL
    assert "interrupted" in (entry.last_error or "").lower()
    assert state.recover_interrupted_uploads() == 0
