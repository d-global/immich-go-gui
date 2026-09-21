from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt

from core.archive_migration import (
    ArchiveFolderEntry,
    ArchiveFolderStatus,
    ArchiveMigrationState,
    ArchiveScanResult,
)
from core.monitor_config import MonitorConfig
from gui.tabs.archive_migration_tab import ArchiveMigrationPage, _format_bytes


def _state_for(tmp_path):
    todo = tmp_path / "Anapa"
    done = tmp_path / "Azov"
    todo.mkdir()
    done.mkdir()
    state = ArchiveMigrationState()
    state.apply_scan(
        ArchiveScanResult(
            root_path=str(tmp_path),
            folders=[
                ArchiveFolderEntry(
                    path=str(todo),
                    name="Anapa",
                    file_count=19,
                    size_bytes=43 * 1024**2,
                ),
                ArchiveFolderEntry(
                    path=str(done),
                    name="Azov",
                    file_count=131,
                    size_bytes=313 * 1024**2,
                ),
            ],
            root_file_count=2,
            root_size_bytes=1024,
        )
    )
    state.get(str(done)).status = ArchiveFolderStatus.DONE
    return state


def _row_for(page, name):
    for row in range(page.table.rowCount()):
        if page.table.item(row, 1).text() == name:
            return row
    raise AssertionError(f"row not found: {name}")


def test_archive_migration_page_hides_done_by_default(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)

    assert page.objectName() == "ArchiveMigrationPage"
    assert page.testAttribute(Qt.WidgetAttribute.WA_StyledBackground) is True
    assert page.table.rowCount() == 2
    assert page.hide_done_check.isChecked() is True
    visible_names = {
        page.table.item(row, 1).text()
        for row in range(page.table.rowCount())
        if not page.table.isRowHidden(row)
    }
    assert visible_names == {"Anapa"}


def test_archive_migration_page_can_show_done_and_switch_to_russian(
    tmp_path, monkeypatch, qtbot
):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)

    page.hide_done_check.setChecked(False)
    assert all(not page.table.isRowHidden(row) for row in range(page.table.rowCount()))

    page.language_combo.setCurrentIndex(page.language_combo.findData("ru"))
    assert page.scan_button.text() == "Сканировать"
    assert page.choose_button.text() == "Выбрать папку"
    assert page.queue_start_button.text() == "Загрузить выбранное"
    assert page.queue_tag_label.text() == "Общий тег:"
    assert (
        page.restore_trashed_check.text()
        == "Восстанавливать найденные дубли из корзины"
    )
    assert page.restore_trashed_check.isChecked() is False
    assert page.sync_album_check.text() == "Синхронизировать альбом"
    assert page.sync_album_check.isChecked() is False
    assert page.trash_orphaned_check.text() == "Лишнее без других альбомов → в корзину"
    assert page.trash_orphaned_check.isEnabled() is False
    assert (
        page.queue_progress_label.text()
        == "Выберите папки для подготовки очереди миграции."
    )
    assert page.table.horizontalHeaderItem(1).text() == "Папка"


def test_archive_migration_table_numeric_sort(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    page.hide_done_check.setChecked(False)
    page.table.sortItems(2)

    assert page.table.item(0, 2).text() == "19"
    assert page.table.item(1, 2).text() == "131"


def test_archive_queue_selection_enables_start_and_done_is_not_requeueable(
    tmp_path, monkeypatch, qtbot
):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)

    assert page.queue_start_button.isEnabled() is False
    anapa_row = _row_for(page, "Anapa")
    page.table.item(anapa_row, 0).setCheckState(Qt.CheckState.Checked)
    assert page.queue_start_button.isEnabled() is True

    page.hide_done_check.setChecked(False)
    azov_row = _row_for(page, "Azov")
    azov_check = page.table.item(azov_row, 0)
    assert not bool(azov_check.flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_archive_queue_runs_selected_folder_to_done(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.save",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.run_folder_upload",
        lambda **_kwargs: SimpleNamespace(
            success=True,
            message="Completed",
            files_uploaded=19,
            files_skipped=0,
            files_errored=0,
            assets_found=19,
            album_added=19,
        ),
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.verify_archive_album",
        lambda *_args, **_kwargs: SimpleNamespace(
            success=True,
            message="Album verified: 19 assets",
            actual_assets=19,
        ),
    )

    class _BinaryManager:
        def resolve_binary_path(self):
            return "immich-go"

    class _Host:
        binary_manager = _BinaryManager()
        monitor_config = MonitorConfig(log_dir=str(tmp_path / "logs"))

        def _collect_config_state(self):
            return {
                "server": "http://immich.test:2283",
                "api_key": "test-key",
                "admin_api_key": "",
                "skip-ssl": False,
                "client_timeout_minutes": 60,
            }

        def _resolve_monitor_credentials(self):
            return "http://immich.test:2283", "test-key"

        def _collect_advanced_state(self, _tab_key):
            return None

    page = ArchiveMigrationPage(host=_Host())
    qtbot.addWidget(page)
    anapa_row = _row_for(page, "Anapa")
    anapa_path = page.table.item(anapa_row, 0).data(Qt.ItemDataRole.UserRole)
    page.table.item(anapa_row, 0).setCheckState(Qt.CheckState.Checked)

    page.start_queue()
    qtbot.waitUntil(lambda: page._queue_thread is None, timeout=3000)

    assert state.get(str(anapa_path)).status == ArchiveFolderStatus.DONE
    assert page.table.isRowHidden(_row_for(page, "Anapa")) is True
    assert page.queue_progress_label.text().startswith("Queue complete:")
    assert "Queue prepared: 1 folders" in page.queue_log.toPlainText()


def test_format_bytes_is_human_readable():
    assert _format_bytes(0) == "0 B"
    assert _format_bytes(1024) == "1.00 KB"
    assert _format_bytes(1024**3) == "1.00 GB"


def test_archive_migration_select_all_uses_header_checkbox(
    tmp_path, monkeypatch, qtbot
):
    state = _state_for(tmp_path)
    extra = tmp_path / "Gelendzhik"
    extra.mkdir()
    state.apply_scan(
        ArchiveScanResult(
            root_path=str(tmp_path),
            folders=[
                *state.folders.values(),
                ArchiveFolderEntry(
                    path=str(extra),
                    name="Gelendzhik",
                    file_count=7,
                    size_bytes=5 * 1024**2,
                ),
            ],
            root_file_count=state.root_file_count,
            root_size_bytes=state.root_size_bytes,
        )
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)

    assert page.table.horizontalHeader() is page.select_all_header
    assert page.select_all_header.checkState() == Qt.CheckState.Unchecked

    page.select_all_header.toggleCheckState()

    assert page.select_all_header.checkState() == Qt.CheckState.Checked
    assert (
        page.table.item(_row_for(page, "Anapa"), 0).checkState()
        == Qt.CheckState.Checked
    )
    assert (
        page.table.item(_row_for(page, "Gelendzhik"), 0).checkState()
        == Qt.CheckState.Checked
    )
    assert page.queue_start_button.isEnabled() is True

    page.hide_done_check.setChecked(False)
    azov_item = page.table.item(_row_for(page, "Azov"), 0)
    assert not bool(azov_item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert azov_item.checkState() == Qt.CheckState.Unchecked


def test_archive_migration_select_all_respects_filter_and_partial_state(
    tmp_path, monkeypatch, qtbot
):
    state = _state_for(tmp_path)
    extra = tmp_path / "Gelendzhik"
    extra.mkdir()
    state.apply_scan(
        ArchiveScanResult(
            root_path=str(tmp_path),
            folders=[
                *state.folders.values(),
                ArchiveFolderEntry(
                    path=str(extra),
                    name="Gelendzhik",
                    file_count=7,
                    size_bytes=5 * 1024**2,
                ),
            ],
            root_file_count=state.root_file_count,
            root_size_bytes=state.root_size_bytes,
        )
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)

    page.search_edit.setText("Anapa")
    page.select_all_header.toggleCheckState()

    assert (
        page.table.item(_row_for(page, "Anapa"), 0).checkState()
        == Qt.CheckState.Checked
    )
    assert (
        page.table.item(_row_for(page, "Gelendzhik"), 0).checkState()
        == Qt.CheckState.Unchecked
    )

    page.search_edit.clear()
    assert page.select_all_header.checkState() == Qt.CheckState.PartiallyChecked

    page.select_all_header.toggleCheckState()
    assert page.select_all_header.checkState() == Qt.CheckState.Checked
    assert (
        page.table.item(_row_for(page, "Gelendzhik"), 0).checkState()
        == Qt.CheckState.Checked
    )

    page.select_all_header.toggleCheckState()
    assert page.select_all_header.checkState() == Qt.CheckState.Unchecked
    assert page.selected_folder_paths() == []


def test_archive_migration_compact_layout_prioritizes_table(
    tmp_path, monkeypatch, qtbot
):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)

    assert page.subtitle_label.wordWrap() is False
    assert page.progress_bar.maximumHeight() == 12
    assert page.queue_progress_bar.maximumHeight() == 12
    assert page.queue_log.maximumHeight() == 78
    assert page.queue_log.minimumHeight() == 58
    assert page.table.minimumHeight() == 280


def test_archive_migration_shows_total_folder_inventory(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    page.language_combo.setCurrentIndex(page.language_combo.findData("ru"))

    text = page.root_files_label.text()
    assert "В папках: 2" in text
    assert "150 файлов" in text
    assert "356.00 MB" in text
    assert "В корне вне очереди: 2 файлов" in text


def test_archive_migration_exclude_is_persistent_skip(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    saves = []
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.save",
        lambda current, profile: saves.append((current, profile)),
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    row = _row_for(page, "Anapa")
    page.table.item(row, 0).setCheckState(Qt.CheckState.Checked)

    page.exclude_selected_folders()

    assert state.get(str(tmp_path / "Anapa")).status == ArchiveFolderStatus.SKIP
    assert saves
    row = _row_for(page, "Anapa")
    assert page.table.item(row, 4).text() == "SKIP"
    assert not bool(page.table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_archive_migration_restore_skip_to_todo(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    state.get(str(tmp_path / "Anapa")).status = ArchiveFolderStatus.SKIP
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.save",
        lambda *_: None,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    row = _row_for(page, "Anapa")
    page.table.selectRow(row)

    page.restore_selected_skips()

    assert state.get(str(tmp_path / "Anapa")).status == ArchiveFolderStatus.TODO


def test_archive_migration_open_folder_uses_system_file_manager(
    tmp_path, monkeypatch, qtbot
):
    state = _state_for(tmp_path)
    opened = []
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.QDesktopServices.openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    row = _row_for(page, "Anapa")
    page.table.selectRow(row)

    page.open_current_folder()

    assert len(opened) == 1
    assert Path(opened[0]) == tmp_path / "Anapa"


def test_select_all_header_uses_real_native_checkbox(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    page.show()
    qtbot.wait(20)

    checkbox = page.select_all_header.checkbox
    assert checkbox.parent() is page.select_all_header.viewport()
    assert checkbox.width() == 16
    assert checkbox.height() == 16
    assert checkbox.isVisible()
    assert page.select_all_header.sectionsClickable() is True


def test_archive_cleanup_options_are_dependency_safe(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)

    assert page.trash_orphaned_check.isEnabled() is False
    page.sync_album_check.setChecked(True)
    assert page.trash_orphaned_check.isEnabled() is True

    page.trash_orphaned_check.setChecked(True)
    page.sync_album_check.setChecked(False)
    assert page.trash_orphaned_check.isChecked() is False
    assert page.trash_orphaned_check.isEnabled() is False


def test_archive_migration_recheck_done_to_partial(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.save",
        lambda *_: None,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    page.hide_done_check.setChecked(False)
    row = _row_for(page, "Azov")
    page.table.selectRow(row)

    assert page.recheck_done_button.isEnabled() is True
    page.recheck_selected_done()

    entry = state.get(str(tmp_path / "Azov"))
    assert entry.status == ArchiveFolderStatus.PARTIAL
    row = _row_for(page, "Azov")
    assert page.table.item(row, 4).text() == "PARTIAL"
    assert bool(page.table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_done_folder_becomes_queueable_when_sync_is_enabled(
    tmp_path, monkeypatch, qtbot
):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    page.hide_done_check.setChecked(False)

    row = _row_for(page, "Azov")
    done_check = page.table.item(row, 0)
    assert not bool(done_check.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    page.sync_album_check.setChecked(True)

    row = _row_for(page, "Azov")
    done_check = page.table.item(row, 0)
    assert bool(done_check.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    done_check.setCheckState(Qt.CheckState.Checked)
    assert str(tmp_path / "Azov") in page.selected_folder_paths()
    assert page.queue_start_button.isEnabled() is True

    page.sync_album_check.setChecked(False)

    row = _row_for(page, "Azov")
    done_check = page.table.item(row, 0)
    assert done_check.checkState() == Qt.CheckState.Unchecked
    assert not bool(done_check.flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_enabling_sync_reveals_done_rows(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)

    row = _row_for(page, "Azov")
    assert page.hide_done_check.isChecked() is True
    assert page.table.isRowHidden(row) is True

    page.sync_album_check.setChecked(True)

    row = _row_for(page, "Azov")
    assert page.hide_done_check.isChecked() is False
    assert page.table.isRowHidden(row) is False
    assert bool(page.table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_archive_migration_header_click_sorts_columns(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    page.show()
    page.hide_done_check.setChecked(False)

    header = page.table.horizontalHeader()
    x = header.sectionViewportPosition(2) + header.sectionSize(2) // 2
    y = header.height() // 2

    qtbot.mouseClick(
        header.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(x, y),
    )
    first_order = [
        int(page.table.item(row, 2).text()) for row in range(page.table.rowCount())
    ]
    assert first_order in ([19, 131], [131, 19])

    qtbot.mouseClick(
        header.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(x, y),
    )
    second_order = [
        int(page.table.item(row, 2).text()) for row in range(page.table.rowCount())
    ]
    assert second_order == list(reversed(first_order))


def test_select_all_header_checkbox_has_clear_spacing(tmp_path, monkeypatch, qtbot):
    state = _state_for(tmp_path)
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.active_profile_name", lambda: "test"
    )
    monkeypatch.setattr(
        "gui.tabs.archive_migration_tab.ArchiveMigrationStateStore.load",
        lambda *_: state,
    )

    page = ArchiveMigrationPage()
    qtbot.addWidget(page)
    page.show()
    qtbot.wait(20)

    header = page.select_all_header
    checkbox = header.checkbox
    assert page.table.columnWidth(0) == 104
    assert checkbox.x() >= 8
    assert checkbox.width() == 16
