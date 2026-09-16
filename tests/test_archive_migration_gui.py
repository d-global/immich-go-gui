from core.archive_migration import (
    ArchiveFolderEntry,
    ArchiveFolderStatus,
    ArchiveMigrationState,
    ArchiveScanResult,
)
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


def test_format_bytes_is_human_readable():
    assert _format_bytes(0) == "0 B"
    assert _format_bytes(1024) == "1.00 KB"
    assert _format_bytes(1024**3) == "1.00 GB"
