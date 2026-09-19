"""Archive Migration workbench UI.

This page is intentionally separate from the regular Archive workflows and from
Backup Monitor. It keeps durable first-level-folder migration state, scans in a
worker thread, and executes selected folders through the existing upstream
``upload-folder`` command builder and hidden runner.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
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
    QPlainTextEdit,
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
from core.archive_migration_queue import (
    ArchiveQueueOptions,
    build_archive_queue_items,
    prepare_archive_queue,
    run_archive_queue,
)
from core.archive_migration_verify import verify_archive_album
from core.config_manager import default_config_dir
from core.folder_runner import RunnerState, run_folder_upload
from core.monitor_config import MonitorConfig
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
        "root_files": "Files directly in archive root: {files} · {size} · not included in the folder queue",
        "no_root": "Choose an archive root, then scan it.",
        "scanning": "Scanning {index}/{total}: {name} · {files} files · {size}",
        "scan_started": "Scanning archive...",
        "scan_done": "Scan complete: {folders} folders · {files} root files",
        "scan_cancelled": "Scan cancelled. Existing state was kept unchanged.",
        "scan_failed": "Archive scan failed",
        "invalid_root": "Choose an existing folder before scanning.",
        "queue_tag": "Shared tag",
        "queue_tag_hint": "optional, e.g. cloud/krasnodar",
        "session_tag": "Add session tag",
        "stop_on_error": "Stop on first error",
        "start_queue": "Upload selected",
        "cancel_queue": "Cancel queue",
        "queue_idle": "Select folders to prepare the migration queue.",
        "queue_starting": "Preparing {folders} folders...",
        "queue_progress": "{index}/{total} · {name} · {status}",
        "queue_cancelling": "Cancelling the current upload and stopping the queue...",
        "queue_finished": "Queue complete: DONE {done} · PARTIAL {partial} · ERROR {errors} · not started {not_started}",
        "queue_finished_cancelled": "Queue cancelled: DONE {done} · PARTIAL {partial} · ERROR {errors} · not started {not_started}",
        "queue_error": "Archive queue error",
        "queue_no_selection": "Select at least one visible folder first.",
        "queue_no_host": "Queue execution is available only inside the main application window.",
        "queue_no_binary": "Immich-Go binary is not available. Configure or download it first.",
        "queue_log": "Queue log",
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
        "root_files": "Файлы прямо в корне архива: {files} · {size} · в очередь папок не входят",
        "no_root": "Выберите корень архива и запустите сканирование.",
        "scanning": "Сканирование {index}/{total}: {name} · {files} файлов · {size}",
        "scan_started": "Сканирование архива...",
        "scan_done": "Сканирование завершено: {folders} папок · {files} файлов в корне",
        "scan_cancelled": "Сканирование остановлено. Старое состояние сохранено без изменений.",
        "scan_failed": "Ошибка сканирования архива",
        "invalid_root": "Перед сканированием выберите существующую папку.",
        "queue_tag": "Общий тег",
        "queue_tag_hint": "необязательно, например cloud/krasnodar",
        "session_tag": "Добавить тег сессии",
        "stop_on_error": "Остановиться на первой ошибке",
        "start_queue": "Загрузить выбранное",
        "cancel_queue": "Остановить очередь",
        "queue_idle": "Выберите папки для подготовки очереди миграции.",
        "queue_starting": "Подготовка очереди: {folders} папок...",
        "queue_progress": "{index}/{total} · {name} · {status}",
        "queue_cancelling": "Останавливаю текущую загрузку и очередь...",
        "queue_finished": "Очередь завершена: DONE {done} · PARTIAL {partial} · ERROR {errors} · не запущено {not_started}",
        "queue_finished_cancelled": "Очередь остановлена: DONE {done} · PARTIAL {partial} · ERROR {errors} · не запущено {not_started}",
        "queue_error": "Ошибка очереди миграции",
        "queue_no_selection": "Сначала выберите хотя бы одну видимую папку.",
        "queue_no_host": "Запуск очереди доступен только внутри основного окна программы.",
        "queue_no_binary": "Immich-Go не найден. Сначала настройте или загрузите бинарник.",
        "queue_log": "Лог очереди",
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


class _ArchiveQueueThread(QThread):
    progress = Signal(int, int, str, str, str)
    log_line = Signal(str, str)
    queue_finished = Signal(object)
    queue_failed = Signal(str)

    def __init__(
        self,
        *,
        state,
        items,
        profile_name: str,
        monitor_config: MonitorConfig,
        server_url: str,
        api_key: str,
        log_dir: str,
        skip_ssl: bool,
        stop_on_error: bool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.state = state
        self.items = list(items)
        self.profile_name = profile_name
        self.monitor_config = monitor_config
        self.server_url = server_url
        self.api_key = api_key
        self.log_dir = log_dir
        self.skip_ssl = skip_ssl
        self.stop_on_error = stop_on_error
        self._cancel_event = threading.Event()
        self.runner_state = RunnerState()

    def cancel(self) -> None:
        self._cancel_event.set()
        self.runner_state.cancel_event.set()
        self.runner_state.pause_event.set()

    def _persist(self, state) -> None:
        ArchiveMigrationStateStore.save(state, self.profile_name)

    def _execute(self, item):
        self.log_line.emit(item.name, "Starting upload")
        return run_folder_upload(
            folder=item.path,
            config=self.monitor_config,
            server_url=self.server_url,
            api_key=self.api_key,
            since_utc=datetime.now(UTC),
            log_dir=self.log_dir,
            state=self.runner_state,
            on_log=lambda folder, message: self.log_line.emit(folder, message),
            prepared_plan=item.plan,
        )

    def _verify(self, item, result):
        expected_assets = int(getattr(result, "assets_found", 0) or 0)
        if expected_assets <= 0:
            expected_assets = int(getattr(result, "files_uploaded", 0) or 0) + int(
                getattr(result, "files_skipped", 0) or 0
            )
        if expected_assets <= 0:
            expected_assets = item.file_count

        self.log_line.emit(
            item.name,
            (
                "Verifying target album on Immich "
                f"(expected at least {expected_assets} assets)"
            ),
        )
        verification = verify_archive_album(
            self.server_url,
            self.api_key,
            item.album_name,
            expected_assets,
            skip_ssl=self.skip_ssl,
        )
        self.log_line.emit(
            item.name,
            (
                "immich-go report: "
                f"assets found {getattr(result, 'assets_found', 0)}, "
                f"uploaded {getattr(result, 'files_uploaded', 0)}, "
                f"server duplicates {getattr(result, 'files_skipped', 0)}, "
                f"reported added to album {getattr(result, 'album_added', 0)}"
            ),
        )
        self.log_line.emit(item.name, verification.message)
        return verification

    def run(self) -> None:
        self.runner_state.reset()
        self.runner_state.set_total_folders(len(self.items))
        try:
            summary = run_archive_queue(
                self.state,
                self.items,
                execute=self._execute,
                persist=self._persist,
                verify=self._verify,
                stop_on_error=self.stop_on_error,
                cancel_event=self._cancel_event,
                on_progress=lambda index, total, item, status: self.progress.emit(
                    index, total, item.path, item.name, status
                ),
            )
            self.queue_finished.emit(summary)
        except Exception as exc:
            self.queue_failed.emit(str(exc))
        finally:
            self.runner_state.set_running(False)
            self.runner_state.set_current_folder("")
            self.runner_state.set_current_file("")


class ArchiveMigrationPage(QWidget):
    """Workbench for controlled one-time archive migration."""

    def __init__(self, host=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ArchiveMigrationPage")
        # Custom QWidget subclasses do not reliably paint a stylesheet background
        # unless WA_StyledBackground is enabled. Without it, the page stays
        # transparent and exposes the native QStackedWidget backing surface on
        # Windows (black in the System/light theme).
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.host = host
        self.profile_name = active_profile_name()
        self.state = ArchiveMigrationStateStore.load(self.profile_name)
        if self.state.recover_interrupted_uploads():
            ArchiveMigrationStateStore.save(self.state, self.profile_name)
        self._scan_thread: _ArchiveScanThread | None = None
        self._queue_thread: _ArchiveQueueThread | None = None
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

        queue_frame = QFrame()
        queue_frame.setObjectName("Card")
        queue_layout = QVBoxLayout(queue_frame)
        queue_layout.setContentsMargins(16, 12, 16, 12)
        queue_layout.setSpacing(8)

        queue_controls = QHBoxLayout()
        self.queue_tag_label = QLabel()
        queue_controls.addWidget(self.queue_tag_label)
        self.queue_tag_edit = QLineEdit()
        self.queue_tag_edit.setClearButtonEnabled(True)
        queue_controls.addWidget(self.queue_tag_edit, 1)
        self.session_tag_check = QCheckBox()
        self.session_tag_check.setChecked(False)
        queue_controls.addWidget(self.session_tag_check)
        self.stop_on_error_check = QCheckBox()
        self.stop_on_error_check.setChecked(True)
        queue_controls.addWidget(self.stop_on_error_check)
        self.queue_start_button = QPushButton()
        self.queue_start_button.setObjectName("BtnRun")
        self.queue_start_button.clicked.connect(self.start_queue)
        self.queue_start_button.setEnabled(False)
        queue_controls.addWidget(self.queue_start_button)
        self.queue_cancel_button = QPushButton()
        self.queue_cancel_button.clicked.connect(self.cancel_queue)
        self.queue_cancel_button.setVisible(False)
        queue_controls.addWidget(self.queue_cancel_button)
        queue_layout.addLayout(queue_controls)

        self.queue_progress_label = QLabel()
        self.queue_progress_label.setObjectName("MutedText")
        queue_layout.addWidget(self.queue_progress_label)
        self.queue_progress_bar = QProgressBar()
        self.queue_progress_bar.setVisible(False)
        queue_layout.addWidget(self.queue_progress_bar)
        self.queue_log = QPlainTextEdit()
        self.queue_log.setReadOnly(True)
        self.queue_log.setMaximumBlockCount(1000)
        self.queue_log.setMaximumHeight(130)
        queue_layout.addWidget(self.queue_log)
        outer.addWidget(queue_frame)

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
        self.queue_progress_label.setText(self._tr("queue_idle"))

    def reload_profile_state(self) -> None:
        """Reload state after the active upstream profile changes."""
        if self._queue_thread is not None and self._queue_thread.isRunning():
            return
        profile_name = active_profile_name()
        if profile_name == self.profile_name:
            return
        self.profile_name = profile_name
        self.state = ArchiveMigrationStateStore.load(self.profile_name)
        if self.state.recover_interrupted_uploads():
            ArchiveMigrationStateStore.save(self.state, self.profile_name)
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
        self.queue_tag_label.setText(self._tr("queue_tag") + ":")
        self.queue_tag_edit.setPlaceholderText(self._tr("queue_tag_hint"))
        self.session_tag_check.setText(self._tr("session_tag"))
        self.stop_on_error_check.setText(self._tr("stop_on_error"))
        self.queue_start_button.setText(self._tr("start_queue"))
        self.queue_cancel_button.setText(self._tr("cancel_queue"))
        self.queue_log.setPlaceholderText(self._tr("queue_log"))
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
        if self._queue_thread is None and not self.queue_progress_bar.isVisible():
            self.queue_progress_label.setText(self._tr("queue_idle"))

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
        if self._queue_thread is not None and self._queue_thread.isRunning():
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

    def start_queue(self) -> None:
        if self._queue_thread is not None and self._queue_thread.isRunning():
            return
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return

        selected_paths = self.selected_folder_paths()
        if not selected_paths:
            QMessageBox.warning(
                self, self._tr("queue_error"), self._tr("queue_no_selection")
            )
            return
        if self.host is None:
            QMessageBox.warning(
                self, self._tr("queue_error"), self._tr("queue_no_host")
            )
            return

        binary_manager = getattr(self.host, "binary_manager", None)
        binary_path = binary_manager.resolve_binary_path() if binary_manager else ""
        if not binary_path:
            QMessageBox.warning(
                self, self._tr("queue_error"), self._tr("queue_no_binary")
            )
            return

        try:
            config_state = self.host._collect_config_state()
            if hasattr(self.host, "_resolve_monitor_credentials"):
                server_url, api_key = self.host._resolve_monitor_credentials()
                if server_url:
                    config_state["server"] = server_url
                if api_key:
                    config_state["api_key"] = api_key
            server_url = str(config_state.get("server") or "")
            api_key = str(config_state.get("api_key") or "")
            advanced_state = (
                self.host._collect_advanced_state("upload-folder")
                if hasattr(self.host, "_collect_advanced_state")
                else None
            )
            options = ArchiveQueueOptions(
                tag=self.queue_tag_edit.text().strip(),
                session_tag=self.session_tag_check.isChecked(),
                stop_on_error=self.stop_on_error_check.isChecked(),
            )
            items = build_archive_queue_items(
                self.state,
                selected_paths,
                config_state=config_state,
                binary_path=binary_path,
                options=options,
                base_advanced_state=advanced_state,
            )
            if not items:
                raise ValueError(self._tr("queue_no_selection"))
            prepare_archive_queue(
                self.state,
                items,
                persist=lambda state: ArchiveMigrationStateStore.save(
                    state, self.profile_name
                ),
            )
        except Exception as exc:
            QMessageBox.critical(self, self._tr("queue_error"), str(exc))
            return

        monitor_config = getattr(self.host, "monitor_config", MonitorConfig())
        log_dir = monitor_config.log_dir or str(Path(default_config_dir()) / "logs")

        self._refresh_status_cells()
        self.queue_log.clear()
        self.queue_progress_label.setText(
            self._tr("queue_starting", folders=len(items))
        )
        self.queue_progress_bar.setRange(0, len(items))
        self.queue_progress_bar.setValue(0)
        self.queue_progress_bar.setVisible(True)
        self._append_queue_log("", f"Queue prepared: {len(items)} folders")

        thread = _ArchiveQueueThread(
            state=self.state,
            items=items,
            profile_name=self.profile_name,
            monitor_config=monitor_config,
            server_url=server_url,
            api_key=api_key,
            log_dir=log_dir,
            skip_ssl=bool(config_state.get("skip-ssl", False)),
            stop_on_error=options.stop_on_error,
            parent=self,
        )
        self._queue_thread = thread
        thread.progress.connect(self._on_queue_progress)
        thread.log_line.connect(self._append_queue_log)
        thread.queue_finished.connect(self._on_queue_finished)
        thread.queue_failed.connect(self._on_queue_failed)
        thread.finished.connect(self._on_queue_thread_finished)
        self._set_queue_running(True)
        thread.start()

    def cancel_queue(self) -> None:
        if self._queue_thread is None or not self._queue_thread.isRunning():
            return
        self.queue_cancel_button.setEnabled(False)
        self.queue_progress_label.setText(self._tr("queue_cancelling"))
        self._append_queue_log("", self._tr("queue_cancelling"))
        self._queue_thread.cancel()

    def _set_queue_running(self, running: bool) -> None:
        self.root_edit.setEnabled(not running)
        self.choose_button.setEnabled(not running)
        self.scan_button.setEnabled(not running)
        self.table.setEnabled(not running)
        self.queue_tag_edit.setEnabled(not running)
        self.session_tag_check.setEnabled(not running)
        self.stop_on_error_check.setEnabled(not running)
        self.queue_cancel_button.setVisible(running)
        self.queue_cancel_button.setEnabled(running)
        if running:
            self.queue_start_button.setEnabled(False)
        else:
            self._update_selection_summary()

    def _on_queue_progress(
        self, index: int, total: int, path: str, name: str, status: str
    ) -> None:
        self._set_status_cell(path, status)
        self.queue_progress_bar.setRange(0, max(total, 1))
        value = index if status != ArchiveFolderStatus.UPLOADING.value else index - 1
        self.queue_progress_bar.setValue(max(0, min(value, total)))
        self.queue_progress_label.setText(
            self._tr(
                "queue_progress",
                index=index,
                total=total,
                name=name,
                status=status,
            )
        )
        self._apply_filters()

    def _on_queue_finished(self, summary) -> None:
        self._refresh_status_cells()
        key = "queue_finished_cancelled" if summary.cancelled else "queue_finished"
        self.queue_progress_label.setText(
            self._tr(
                key,
                done=summary.done,
                partial=summary.partial,
                errors=summary.errors,
                not_started=summary.not_started,
            )
        )
        completed = summary.done + summary.partial + summary.errors
        self.queue_progress_bar.setValue(min(completed, summary.total))
        self._append_queue_log("", self.queue_progress_label.text())
        self._apply_filters()

    def _on_queue_failed(self, message: str) -> None:
        self._refresh_status_cells()
        self.queue_progress_label.setText(message)
        self._append_queue_log("", f"ERROR: {message}")
        QMessageBox.critical(self, self._tr("queue_error"), message)

    def _on_queue_thread_finished(self) -> None:
        thread = self._queue_thread
        self._queue_thread = None
        self._set_queue_running(False)
        if thread is not None:
            thread.deleteLater()

    def _append_queue_log(self, folder: str, message: str) -> None:
        prefix = f"[{folder}] " if folder else ""
        self.queue_log.appendPlainText(prefix + message)

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
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if entry.status not in {ArchiveFolderStatus.DONE, ArchiveFolderStatus.SKIP}:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            check.setFlags(flags)
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

    def _set_status_cell(self, path: str, status: str) -> None:
        for row in range(self.table.rowCount()):
            check = self.table.item(row, 0)
            if check is None or str(check.data(Qt.ItemDataRole.UserRole) or "") != path:
                continue
            status_item = self.table.item(row, 4)
            if status_item is not None:
                status_item.setText(status)
                status_item.setData(Qt.ItemDataRole.UserRole, status)
            if status in {
                ArchiveFolderStatus.DONE.value,
                ArchiveFolderStatus.SKIP.value,
            }:
                check.setCheckState(Qt.CheckState.Unchecked)
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            return

    def _refresh_status_cells(self) -> None:
        for path, entry in self.state.folders.items():
            self._set_status_cell(path, entry.status.value)
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
        queue_running = (
            self._queue_thread is not None and self._queue_thread.isRunning()
        )
        self.queue_start_button.setEnabled(folders > 0 and not queue_running)

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
