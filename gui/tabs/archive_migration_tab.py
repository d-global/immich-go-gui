"""Archive Migration workbench UI.

This page is intentionally separate from the regular Archive workflows and from
Backup Monitor.  It surfaces the persistent first-level-folder migration state
from :mod:`core.archive_migration` and performs scans in a worker thread so a
large or slow archive cannot freeze the GUI.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.archive_migration import (
    ArchiveFolderStatus,
    ArchiveMigrationStateStore,
    ArchiveScanCancelled,
    scan_archive_root,
)
from core.profile_manager import active_profile_name

_TRANSLATIONS = {
    "en": {
        "title": "Archive Migration",
        "subtitle": "Migrate a legacy folder archive safely, one first-level folder per Immich album.",
        "archive_root": "Archive root",
        "choose": "Choose folder",
        "scan": "Scan",
        "cancel": "Cancel scan",
        "search": "Search folders...",
        "all_statuses": "All statuses",
        "hide_done": "Hide DONE",
        "language": "Language",
        "select": "Select",
        "folder": "Folder",
        "files": "Files",
        "size": "Size",
        "status": "Status",
        "selected": "Selected: {folders} folders · {files} files · {size}",
        "root_files": "Files directly in archive root: {files} · {size}",
        "no_root": "Choose an archive root, then scan it.",
        "scanning": "Scanning {index}/{total}: {name} · {files} files · {size}",
        "scan_started": "Scanning archive...",
        "scan_done": "Scan complete: {folders} folders · {files} root files",
        "scan_cancelled": "Scan cancelled. Existing state was kept unchanged.",
        "scan_failed": "Archive scan failed",
        "invalid_root": "Choose an existing folder before scanning.",
    },
    "ru": {
        "title": "Миграция архива",
        "subtitle": "Безопасная миграция старого архива: одна папка первого уровня = один альбом Immich.",
        "archive_root": "Корень архива",
        "choose": "Выбрать папку",
        "scan": "Сканировать",
        "cancel": "Остановить сканирование",
        "search": "Поиск по папкам...",
        "all_statuses": "Все статусы",
        "hide_done": "Скрыть DONE",
        "language": "Язык",
        "select": "Выбрать",
        "folder": "Папка",
        "files": "Файлов",
        "size": "Размер",
        "status": "Статус",
        "selected": "Выбрано: {folders} папок · {files} файлов · {size}",
        "root_files": "Файлы прямо в корне архива: {files} · {size}",
        "no_root": "Выберите корень архива и запустите сканирование.",
        "scanning": "Сканирование {index}/{total}: {name} · {files} файлов · {size}",
        "scan_started": "Сканирование архива...",
        "scan_done": "Сканирование завершено: {folders} папок · {files} файлов в корне",
        "scan_cancelled": "Сканирование остановлено. Старое состояние сохранено без изменений.",
        "scan_failed": "Ошибка сканирования архива",
        "invalid_root": "Перед сканированием выберите существующую папку.",
    },
}


class _SortableItem(QTableWidgetItem):
    """Table item that sorts by the value stored in UserRole when present."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine = self.data(Qt.ItemDataRole.UserRole)
        theirs = other.data(Qt.ItemDataRole.UserRole)
        if mine is not None and theirs is not None:
            return mine < theirs
        return super().__lt__(other)


class _ArchiveScanThread(QThread):
    progress = Signal(int, int, str, int, int)
    scan_finished = Signal(object)
    scan_failed = Signal(str)
    scan_cancelled = Signal()

    def __init__(self, root_path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.root_path = root_path
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = scan_archive_root(
                self.root_path,
                cancel_event=self._cancel_event,
                on_progress=lambda i, total, path, count, size: self.progress.emit(
                    i, total, path, count, size
                ),
            )
        except ArchiveScanCancelled:
            self.scan_cancelled.emit()
            return
        except Exception as exc:
            self.scan_failed.emit(str(exc))
            return
        self.scan_finished.emit(result)


class ArchiveMigrationPage(QWidget):
    """Workbench for controlled one-time archive migration."""

    def __init__(self, host=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.host = host
        self.profile_name = active_profile_name()
        self.state = ArchiveMigrationStateStore.load(self.profile_name)
        self._scan_thread: _ArchiveScanThread | None = None
        self._language = self._load_language()
        self._build_ui()
        self._load_state_into_ui()
        self._retranslate()

    def _load_language(self) -> str:
        if self.host is not None and hasattr(self.host, "settings"):
            value = str(self.host.settings.value("fork/language", "en"))
            if value in _TRANSLATIONS:
                return value
        return "en"

    def _tr(self, key: str, **kwargs) -> str:
        text = _TRANSLATIONS[self._language][key]
        return text.format(**kwargs) if kwargs else text

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(12)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        self.title_label = QLabel()
        self.title_label.setObjectName("PageTitle")
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("MutedText")
        self.subtitle_label.setWordWrap(True)
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        heading.addLayout(title_box, 1)

        self.language_label = QLabel()
        heading.addWidget(self.language_label)
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Русский", "ru")
        idx = self.language_combo.findData(self._language)
        self.language_combo.setCurrentIndex(max(0, idx))
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        heading.addWidget(self.language_combo)
        outer.addLayout(heading)

        source_frame = QFrame()
        source_frame.setObjectName("Card")
        source_layout = QVBoxLayout(source_frame)
        source_layout.setContentsMargins(16, 14, 16, 14)
        source_layout.setSpacing(10)

        source_row = QHBoxLayout()
        self.root_label = QLabel()
        source_row.addWidget(self.root_label)
        self.root_edit = QLineEdit()
        self.root_edit.setClearButtonEnabled(True)
        source_row.addWidget(self.root_edit, 1)
        self.choose_button = QPushButton()
        self.choose_button.clicked.connect(self._choose_root)
        source_row.addWidget(self.choose_button)
        self.scan_button = QPushButton()
        self.scan_button.setObjectName("BtnRun")
        self.scan_button.clicked.connect(self.start_scan)
        source_row.addWidget(self.scan_button)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.cancel_button.setVisible(False)
        source_row.addWidget(self.cancel_button)
        source_layout.addLayout(source_row)

        self.progress_label = QLabel()
        self.progress_label.setObjectName("MutedText")
        source_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        source_layout.addWidget(self.progress_bar)
        self.root_files_label = QLabel()
        self.root_files_label.setObjectName("MutedText")
        source_layout.addWidget(self.root_files_label)
        outer.addWidget(source_frame)

        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.search_edit, 1)

        self.status_combo = QComboBox()
        self.status_combo.addItem("", "")
        for status in ArchiveFolderStatus:
            self.status_combo.addItem(status.value, status.value)
        self.status_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self.status_combo)

        self.hide_done_check = QCheckBox()
        self.hide_done_check.setChecked(True)
        self.hide_done_check.toggled.connect(self._apply_filters)
        filter_row.addWidget(self.hide_done_check)
        outer.addLayout(filter_row)

        self.table = QTableWidget(0, 5)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._on_table_item_changed)
        outer.addWidget(self.table, 1)

        summary_row = QHBoxLayout()
        self.summary_label = QLabel()
        self.summary_label.setObjectName("MutedText")
        summary_row.addWidget(self.summary_label)
        summary_row.addStretch()
        outer.addLayout(summary_row)

    def _load_state_into_ui(self) -> None:
        self.root_edit.setText(self.state.root_path)
        self._populate_table()
        self._update_root_files_label()
        self._update_selection_summary()
        if self.state.root_path:
            self.progress_label.setText("")
        else:
            self.progress_label.setText(self._tr("no_root"))

    def reload_profile_state(self) -> None:
        """Reload state after the active upstream profile changes."""
        profile_name = active_profile_name()
        if profile_name == self.profile_name:
            return
        self.profile_name = profile_name
        self.state = ArchiveMigrationStateStore.load(self.profile_name)
        self._load_state_into_ui()

    def _on_language_changed(self) -> None:
        language = self.language_combo.currentData()
        if language not in _TRANSLATIONS:
            return
        self._language = language
        if self.host is not None and hasattr(self.host, "settings"):
            self.host.settings.setValue("fork/language", language)
        self._retranslate()

    def _retranslate(self) -> None:
        self.title_label.setText(self._tr("title"))
        self.subtitle_label.setText(self._tr("subtitle"))
        self.language_label.setText(self._tr("language") + ":")
        self.root_label.setText(self._tr("archive_root") + ":")
        self.choose_button.setText(self._tr("choose"))
        self.scan_button.setText(self._tr("scan"))
        self.cancel_button.setText(self._tr("cancel"))
        self.search_edit.setPlaceholderText(self._tr("search"))
        self.hide_done_check.setText(self._tr("hide_done"))
        self.status_combo.setItemText(0, self._tr("all_statuses"))
        self.table.setHorizontalHeaderLabels(
            [
                self._tr("select"),
                self._tr("folder"),
                self._tr("files"),
                self._tr("size"),
                self._tr("status"),
            ]
        )
        self._update_root_files_label()
        self._update_selection_summary()
        if not self.root_edit.text().strip() and not self._scan_thread:
            self.progress_label.setText(self._tr("no_root"))

    def _choose_root(self) -> None:
        current = self.root_edit.text().strip()
        start_dir = current if Path(current).is_dir() else str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self, self._tr("archive_root"), start_dir
        )
        if selected:
            self.root_edit.setText(selected)

    def start_scan(self) -> None:
        root_path = self.root_edit.text().strip()
        if not root_path or not Path(root_path).is_dir():
            QMessageBox.warning(self, self._tr("scan_failed"), self._tr("invalid_root"))
            return
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return

        self.progress_label.setText(self._tr("scan_started"))
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.scan_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)

        thread = _ArchiveScanThread(root_path, self)
        self._scan_thread = thread
        thread.progress.connect(self._on_scan_progress)
        thread.scan_finished.connect(self._on_scan_finished)
        thread.scan_failed.connect(self._on_scan_failed)
        thread.scan_cancelled.connect(self._on_scan_cancelled)
        thread.finished.connect(self._on_scan_thread_finished)
        thread.start()

    def cancel_scan(self) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self.cancel_button.setEnabled(False)
            self._scan_thread.cancel()

    def _on_scan_progress(
        self, index: int, total: int, path: str, file_count: int, size_bytes: int
    ) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(min(index, total))
        self.progress_label.setText(
            self._tr(
                "scanning",
                index=index,
                total=total,
                name=Path(path).name,
                files=file_count,
                size=_format_bytes(size_bytes),
            )
        )

    def _on_scan_finished(self, result) -> None:
        # Atomic from the UI perspective: the old table/state stays visible until
        # the new scan completes successfully.
        self.state.apply_scan(result)
        ArchiveMigrationStateStore.save(self.state, self.profile_name)
        self.root_edit.setText(result.root_path)
        self._populate_table()
        self._update_root_files_label()
        self.progress_label.setText(
            self._tr(
                "scan_done",
                folders=len(result.folders),
                files=result.root_file_count,
            )
        )

    def _on_scan_failed(self, message: str) -> None:
        self.progress_label.setText(message)
        QMessageBox.critical(self, self._tr("scan_failed"), message)

    def _on_scan_cancelled(self) -> None:
        self.progress_label.setText(self._tr("scan_cancelled"))

    def _on_scan_thread_finished(self) -> None:
        thread = self._scan_thread
        self._scan_thread = None
        self.progress_bar.setVisible(False)
        self.scan_button.setEnabled(True)
        self.choose_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        if thread is not None:
            thread.deleteLater()

    def _populate_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        entries = sorted(
            self.state.folders.values(), key=lambda item: item.name.casefold()
        )
        for entry in entries:
            row = self.table.rowCount()
            self.table.insertRow(row)

            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check.setCheckState(Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, entry.path)
            self.table.setItem(row, 0, check)

            name = _SortableItem(entry.name)
            name.setData(Qt.ItemDataRole.UserRole, entry.name.casefold())
            name.setData(Qt.ItemDataRole.UserRole + 1, entry.path)
            self.table.setItem(row, 1, name)

            count = _SortableItem(str(entry.file_count))
            count.setData(Qt.ItemDataRole.UserRole, entry.file_count)
            self.table.setItem(row, 2, count)

            size = _SortableItem(_format_bytes(entry.size_bytes))
            size.setData(Qt.ItemDataRole.UserRole, entry.size_bytes)
            self.table.setItem(row, 3, size)

            status = _SortableItem(entry.status.value)
            status.setData(Qt.ItemDataRole.UserRole, entry.status.value)
            self.table.setItem(row, 4, status)

        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self._apply_filters()

    def _apply_filters(self) -> None:
        query = self.search_edit.text().strip().casefold()
        wanted_status = str(self.status_combo.currentData() or "")
        hide_done = self.hide_done_check.isChecked()
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 1)
            status_item = self.table.item(row, 4)
            if name_item is None or status_item is None:
                continue
            name = name_item.text().casefold()
            status = status_item.text()
            hidden = bool(query and query not in name)
            hidden = hidden or bool(wanted_status and status != wanted_status)
            hidden = hidden or (hide_done and status == ArchiveFolderStatus.DONE.value)
            self.table.setRowHidden(row, hidden)
        self._update_selection_summary()

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._update_selection_summary()

    def selected_folder_paths(self) -> list[str]:
        paths: list[str] = []
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                path = str(item.data(Qt.ItemDataRole.UserRole) or "")
                if path:
                    paths.append(path)
        return paths

    def _update_selection_summary(self) -> None:
        folders = 0
        files = 0
        size = 0
        for path in self.selected_folder_paths():
            entry = self.state.get(path)
            if entry is None:
                continue
            folders += 1
            files += entry.file_count
            size += entry.size_bytes
        self.summary_label.setText(
            self._tr("selected", folders=folders, files=files, size=_format_bytes(size))
        )

    def _update_root_files_label(self) -> None:
        if not hasattr(self, "root_files_label"):
            return
        self.root_files_label.setText(
            self._tr(
                "root_files",
                files=self.state.root_file_count,
                size=_format_bytes(self.state.root_size_bytes),
            )
        )


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def build_archive_migration_tab(host) -> QWidget:
    return ArchiveMigrationPage(host=host)
