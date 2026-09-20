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

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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
from core.archive_migration_verify import (
    AlbumVerificationResult,
    finalize_archive_album_verification,
    repair_archive_album_membership,
    synchronize_archive_album_to_source,
    verify_archive_album,
)
from core.config_manager import default_config_dir
from core.folder_runner import RunnerState, run_folder_upload
from core.monitor_config import MonitorConfig
from core.profile_manager import active_profile_name

_TRANSLATIONS = {
    "en": {
        "title": "Archive Migration",
        "subtitle": "1 first-level folder = 1 Immich album.",
        "archive_root": "Archive root",
        "choose": "Choose folder",
        "scan": "Scan",
        "cancel": "Cancel scan",
        "search": "Search folders...",
        "all_statuses": "All statuses",
        "hide_done": "Hide DONE",
        "language": "Language",
        "select": "Select",
        "select_all_tip": "Select or clear all visible queueable folders",
        "exclude_selected": "Exclude",
        "exclude_tip": "Persistently mark checked or selected folders as SKIP",
        "restore_selected": "Restore",
        "restore_selected_tip": "Return selected SKIP folders to TODO",
        "open_folder": "Open folder",
        "open_folder_tip": "Open the current folder in the system file manager",
        "folder": "Folder",
        "files": "Files",
        "size": "Size",
        "status": "Status",
        "selected": "Selected: {folders} folders · {files} files · {size}",
        "root_files": "In folders: {folders} · {files} files · {size} | Root excluded: {root_files} files · {root_size}",
        "no_root": "Choose an archive root, then scan it.",
        "scanning": "Scanning {index}/{total}: {name} · {files} files · {size}",
        "scan_started": "Scanning archive...",
        "scan_done": "Scan complete: {folders} folders",
        "scan_cancelled": "Scan cancelled. Existing state was kept unchanged.",
        "scan_failed": "Archive scan failed",
        "invalid_root": "Choose an existing folder before scanning.",
        "queue_tag": "Shared tag",
        "queue_tag_hint": "optional, e.g. cloud/krasnodar",
        "session_tag": "Add session tag",
        "stop_on_error": "Stop on first error",
        "restore_trashed": "Restore matching duplicates from Immich trash",
        "restore_trashed_tip": "Restores only trash assets whose SHA1 matches files in the selected source folder.",
        "restore_trashed_enabled": "Targeted restore of matching trash duplicates is enabled.",
        "sync_album": "Sync album to folder",
        "sync_album_tip": "Remove target-album assets that are not present in the canonical local folder.",
        "trash_orphaned_extras": "Trash extras unused by other albums",
        "trash_orphaned_tip": "After sync, move an extra asset to Immich Trash only when it belongs to no other album.",
        "sync_album_enabled": "Exact album-to-folder synchronization is enabled.",
        "trash_orphaned_enabled": "Orphaned album extras will be moved to Immich Trash after cross-album checks.",
        "cleanup_confirm_title": "Immich cleanup",
        "cleanup_confirm_text": "After album sync, extra assets that are not used by any other album will be moved to Immich Trash. Continue?",
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
        "subtitle": "1 папка первого уровня = 1 альбом Immich.",
        "archive_root": "Корень архива",
        "choose": "Выбрать папку",
        "scan": "Сканировать",
        "cancel": "Остановить сканирование",
        "search": "Поиск по папкам...",
        "all_statuses": "Все статусы",
        "hide_done": "Скрыть DONE",
        "language": "Язык",
        "select": "Выбрать",
        "select_all_tip": "Выбрать или снять все видимые папки, доступные для очереди",
        "exclude_selected": "Исключить",
        "exclude_tip": "Навсегда пометить отмеченные или выделенные папки как SKIP",
        "restore_selected": "Вернуть",
        "restore_selected_tip": "Вернуть выделенные SKIP-папки в TODO",
        "open_folder": "Открыть папку",
        "open_folder_tip": "Открыть текущую папку в Проводнике",
        "folder": "Папка",
        "files": "Файлов",
        "size": "Размер",
        "status": "Статус",
        "selected": "Выбрано: {folders} папок · {files} файлов · {size}",
        "root_files": "В папках: {folders} · {files} файлов · {size} | В корне вне очереди: {root_files} файлов · {root_size}",
        "no_root": "Выберите корень архива и запустите сканирование.",
        "scanning": "Сканирование {index}/{total}: {name} · {files} файлов · {size}",
        "scan_started": "Сканирование архива...",
        "scan_done": "Сканирование завершено: {folders} папок",
        "scan_cancelled": "Сканирование остановлено. Старое состояние сохранено без изменений.",
        "scan_failed": "Ошибка сканирования архива",
        "invalid_root": "Перед сканированием выберите существующую папку.",
        "queue_tag": "Общий тег",
        "queue_tag_hint": "необязательно, например cloud/krasnodar",
        "session_tag": "Добавить тег сессии",
        "stop_on_error": "Остановиться на первой ошибке",
        "restore_trashed": "Восстанавливать найденные дубли из корзины",
        "restore_trashed_tip": "Восстанавливаются только assets из корзины, SHA1 которых совпал с файлами выбранной исходной папки.",
        "restore_trashed_enabled": "Включено точечное восстановление найденных дублей из корзины.",
        "sync_album": "Синхронизировать альбом",
        "sync_album_tip": "Убирает из целевого альбома assets, которых нет в канонической локальной папке.",
        "trash_orphaned_extras": "Лишнее без других альбомов → в корзину",
        "trash_orphaned_tip": "После синхронизации лишний asset попадёт в корзину Immich только если он не состоит ни в одном другом альбоме.",
        "sync_album_enabled": "Включена точная синхронизация альбома с локальной папкой.",
        "trash_orphaned_enabled": "Лишние assets без других альбомов будут перемещены в корзину Immich после проверки связей.",
        "cleanup_confirm_title": "Очистка Immich",
        "cleanup_confirm_text": "После синхронизации лишние assets, которые не используются ни в одном другом альбоме, будут перемещены в корзину Immich. Продолжить?",
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


class _HeaderCheckBox(QCheckBox):
    """Native checkbox with predictable partial-state click behavior."""

    def nextCheckState(self) -> None:
        target = (
            Qt.CheckState.Unchecked
            if self.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        self.setCheckState(target)


class _SelectAllHeader(QHeaderView):
    """Header with a real native checkbox in the first table section."""

    check_state_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.checkbox = _HeaderCheckBox(self.viewport())
        self.checkbox.setTristate(True)
        self.checkbox.setCheckState(Qt.CheckState.Unchecked)
        self.checkbox.stateChanged.connect(self.check_state_changed)
        self.sectionResized.connect(lambda *_args: self._position_checkbox())
        self.geometriesChanged.connect(self._position_checkbox)
        self._position_checkbox()

    def checkState(self) -> Qt.CheckState:
        return self.checkbox.checkState()

    def setCheckState(self, state: Qt.CheckState) -> None:
        self.checkbox.blockSignals(True)
        try:
            self.checkbox.setCheckState(Qt.CheckState(state))
        finally:
            self.checkbox.blockSignals(False)

    def toggleCheckState(self) -> None:
        self.checkbox.nextCheckState()

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self.checkbox.setEnabled(enabled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_checkbox()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_checkbox()
        self.checkbox.show()

    def _position_checkbox(self) -> None:
        if not hasattr(self, "checkbox"):
            return
        hint = self.checkbox.sizeHint()
        x = self.sectionViewportPosition(0) + 6
        y = max(0, (self.height() - hint.height()) // 2)
        self.checkbox.setGeometry(x, y, hint.width(), hint.height())


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
        restore_trashed: bool,
        sync_album: bool,
        trash_orphaned_extras: bool,
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
        self.restore_trashed = restore_trashed
        self.sync_album = sync_album
        self.trash_orphaned_extras = trash_orphaned_extras
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
        processed_assets = int(getattr(result, "files_uploaded", 0) or 0) + int(
            getattr(result, "files_skipped", 0) or 0
        )
        expected_assets = processed_assets or int(
            getattr(result, "assets_found", 0) or 0
        )
        if expected_assets <= 0:
            expected_assets = item.file_count

        self.log_line.emit(
            item.name,
            f"Verifying target album on Immich (expected {expected_assets} assets)",
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

        server_duplicates = int(getattr(result, "files_skipped", 0) or 0)
        needs_membership_check = (
            not verification.success or server_duplicates > 0 or self.sync_album
        )
        can_repair = (
            needs_membership_check
            and verification.album_id is not None
            and isinstance(verification.actual_assets, int)
            and not self._cancel_event.is_set()
        )
        if not can_repair:
            return verification

        if not verification.success:
            reason = "Album count differs"
        elif server_duplicates > 0:
            reason = f"{server_duplicates} server duplicates need membership proof"
        else:
            reason = "Album synchronization needs canonical source IDs"
        self.log_line.emit(
            item.name,
            f"{reason}; checking source membership by SHA1",
        )
        repair = repair_archive_album_membership(
            self.server_url,
            self.api_key,
            verification.album_id,
            item.path,
            expected_assets,
            skip_ssl=self.skip_ssl,
            restore_trashed=self.restore_trashed,
            cancel_event=self._cancel_event,
            on_log=lambda message: self.log_line.emit(item.name, message),
        )
        self.log_line.emit(item.name, repair.message)
        for detail in repair.details:
            self.log_line.emit(item.name, f"Repair detail: {detail}")

        final = verify_archive_album(
            self.server_url,
            self.api_key,
            item.album_name,
            expected_assets,
            skip_ssl=self.skip_ssl,
        )
        self.log_line.emit(item.name, f"Post-repair: {final.message}")
        reconciled = finalize_archive_album_verification(final, repair)
        self.log_line.emit(item.name, f"Membership result: {reconciled.message}")
        if reconciled.success and self.sync_album:
            sync_result = synchronize_archive_album_to_source(
                self.server_url,
                self.api_key,
                verification.album_id or "",
                repair.resolved_asset_ids,
                trash_orphaned_extras=self.trash_orphaned_extras,
                skip_ssl=self.skip_ssl,
                cancel_event=self._cancel_event,
                on_log=lambda message: self.log_line.emit(item.name, message),
            )
            self.log_line.emit(item.name, sync_result.message)
            for detail in sync_result.details:
                self.log_line.emit(item.name, f"Cleanup detail: {detail}")

            if not sync_result.success:
                return AlbumVerificationResult(
                    success=False,
                    album_name=reconciled.album_name,
                    expected_assets=reconciled.expected_assets,
                    actual_assets=sync_result.album_assets_after,
                    album_id=reconciled.album_id,
                    message=sync_result.message,
                )

            exact = verify_archive_album(
                self.server_url,
                self.api_key,
                item.album_name,
                expected_assets,
                skip_ssl=self.skip_ssl,
            )
            self.log_line.emit(item.name, f"Post-sync: {exact.message}")
            return exact

        if reconciled.success:
            return reconciled

        message = reconciled.message
        if repair.blocked_assets:
            message += (
                f"; {repair.blocked_assets} assets were rejected with no_permission"
            )
        if repair.trashed_assets:
            message += f"; {repair.trashed_assets} assets are in trash"
        return AlbumVerificationResult(
            success=False,
            album_name=reconciled.album_name,
            expected_assets=reconciled.expected_assets,
            actual_assets=reconciled.actual_assets,
            album_id=reconciled.album_id,
            message=message,
        )

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
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(8)

        heading = QHBoxLayout()
        heading.setSpacing(10)
        self.title_label = QLabel()
        self.title_label.setObjectName("PageTitle")
        heading.addWidget(self.title_label)

        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("MutedText")
        self.subtitle_label.setWordWrap(False)
        heading.addWidget(self.subtitle_label, 1)

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
        source_layout.setContentsMargins(12, 8, 12, 8)
        source_layout.setSpacing(5)

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
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

        source_meta_row = QHBoxLayout()
        source_meta_row.setSpacing(12)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("MutedText")
        source_meta_row.addWidget(self.progress_label, 1)
        self.root_files_label = QLabel()
        self.root_files_label.setObjectName("MutedText")
        source_meta_row.addWidget(self.root_files_label)
        source_layout.addLayout(source_meta_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setVisible(False)
        source_layout.addWidget(self.progress_bar)
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

        self.open_folder_button = QPushButton()
        self.open_folder_button.clicked.connect(self.open_current_folder)
        filter_row.addWidget(self.open_folder_button)

        self.exclude_button = QPushButton()
        self.exclude_button.clicked.connect(self.exclude_selected_folders)
        filter_row.addWidget(self.exclude_button)

        self.restore_skip_button = QPushButton()
        self.restore_skip_button.clicked.connect(self.restore_selected_skips)
        filter_row.addWidget(self.restore_skip_button)
        outer.addLayout(filter_row)

        queue_frame = QFrame()
        queue_frame.setObjectName("Card")
        queue_layout = QVBoxLayout(queue_frame)
        queue_layout.setContentsMargins(12, 8, 12, 8)
        queue_layout.setSpacing(5)

        queue_controls = QHBoxLayout()
        queue_controls.setSpacing(8)
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

        cleanup_controls = QHBoxLayout()
        cleanup_controls.setSpacing(12)
        self.restore_trashed_check = QCheckBox()
        self.restore_trashed_check.setChecked(False)
        cleanup_controls.addWidget(self.restore_trashed_check)

        self.sync_album_check = QCheckBox()
        self.sync_album_check.setChecked(False)
        self.sync_album_check.toggled.connect(self._on_sync_album_toggled)
        cleanup_controls.addWidget(self.sync_album_check)

        self.trash_orphaned_check = QCheckBox()
        self.trash_orphaned_check.setChecked(False)
        self.trash_orphaned_check.setEnabled(False)
        cleanup_controls.addWidget(self.trash_orphaned_check)
        cleanup_controls.addStretch()
        queue_layout.addLayout(cleanup_controls)

        queue_progress_row = QHBoxLayout()
        queue_progress_row.setSpacing(10)
        self.queue_progress_label = QLabel()
        self.queue_progress_label.setObjectName("MutedText")
        queue_progress_row.addWidget(self.queue_progress_label)
        self.queue_progress_bar = QProgressBar()
        self.queue_progress_bar.setFixedHeight(12)
        self.queue_progress_bar.setVisible(False)
        queue_progress_row.addWidget(self.queue_progress_bar, 1)
        queue_layout.addLayout(queue_progress_row)

        self.queue_log = QPlainTextEdit()
        self.queue_log.setReadOnly(True)
        self.queue_log.setMaximumBlockCount(1000)
        self.queue_log.setMinimumHeight(58)
        self.queue_log.setMaximumHeight(78)
        queue_layout.addWidget(self.queue_log)
        outer.addWidget(queue_frame)

        self.table = QTableWidget(0, 5)
        self.select_all_header = _SelectAllHeader(self.table)
        self.select_all_header.check_state_changed.connect(self._on_select_all_changed)
        self.table.setHorizontalHeader(self.select_all_header)
        self._syncing_select_all = False
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(280)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.itemSelectionChanged.connect(self._update_folder_action_buttons)
        self.table.itemDoubleClicked.connect(self._on_table_item_double_clicked)
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
        self.restore_trashed_check.setText(self._tr("restore_trashed"))
        self.restore_trashed_check.setToolTip(self._tr("restore_trashed_tip"))
        self.sync_album_check.setText(self._tr("sync_album"))
        self.sync_album_check.setToolTip(self._tr("sync_album_tip"))
        self.trash_orphaned_check.setText(self._tr("trash_orphaned_extras"))
        self.trash_orphaned_check.setToolTip(self._tr("trash_orphaned_tip"))
        self.select_all_header.setToolTip(self._tr("select_all_tip"))
        self.open_folder_button.setText(self._tr("open_folder"))
        self.open_folder_button.setToolTip(self._tr("open_folder_tip"))
        self.exclude_button.setText(self._tr("exclude_selected"))
        self.exclude_button.setToolTip(self._tr("exclude_tip"))
        self.restore_skip_button.setText(self._tr("restore_selected"))
        self.restore_skip_button.setToolTip(self._tr("restore_selected_tip"))
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

    def _on_sync_album_toggled(self, checked: bool) -> None:
        running = self._queue_thread is not None and self._queue_thread.isRunning()
        self.trash_orphaned_check.setEnabled(checked and not running)
        if not checked:
            self.trash_orphaned_check.setChecked(False)

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
                restore_trashed=self.restore_trashed_check.isChecked(),
                sync_album=(
                    self.sync_album_check.isChecked()
                    or self.trash_orphaned_check.isChecked()
                ),
                trash_orphaned_extras=self.trash_orphaned_check.isChecked(),
            )
            if options.trash_orphaned_extras:
                answer = QMessageBox.question(
                    self,
                    self._tr("cleanup_confirm_title"),
                    self._tr("cleanup_confirm_text"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

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
        if options.restore_trashed:
            self._append_queue_log("", self._tr("restore_trashed_enabled"))
        if options.sync_album:
            self._append_queue_log("", self._tr("sync_album_enabled"))
        if options.trash_orphaned_extras:
            self._append_queue_log("", self._tr("trash_orphaned_enabled"))

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
            restore_trashed=options.restore_trashed,
            sync_album=options.sync_album,
            trash_orphaned_extras=options.trash_orphaned_extras,
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
        self.select_all_header.setEnabled(not running)
        self.queue_tag_edit.setEnabled(not running)
        self.session_tag_check.setEnabled(not running)
        self.stop_on_error_check.setEnabled(not running)
        self.restore_trashed_check.setEnabled(not running)
        self.sync_album_check.setEnabled(not running)
        self.trash_orphaned_check.setEnabled(
            not running and self.sync_album_check.isChecked()
        )
        self.open_folder_button.setEnabled(
            not running and bool(self._current_folder_path())
        )
        self.exclude_button.setEnabled(not running)
        self.restore_skip_button.setEnabled(not running)
        self.queue_cancel_button.setVisible(running)
        self.queue_cancel_button.setEnabled(running)
        if running:
            self.queue_start_button.setEnabled(False)
        else:
            self._update_selection_summary()
            self._update_folder_action_buttons()

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

    def _visible_queueable_check_items(self) -> list[QTableWidgetItem]:
        items: list[QTableWidgetItem] = []
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item is None:
                continue
            if not bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            items.append(item)
        return items

    def _on_select_all_changed(self, state: int) -> None:
        if self._syncing_select_all:
            return
        target = (
            Qt.CheckState.Checked
            if state == Qt.CheckState.Checked.value
            else Qt.CheckState.Unchecked
        )
        self.table.blockSignals(True)
        try:
            for item in self._visible_queueable_check_items():
                item.setCheckState(target)
        finally:
            self.table.blockSignals(False)
        self._update_selection_summary()

    def _sync_select_all_checkbox(self) -> None:
        if not hasattr(self, "select_all_header"):
            return
        items = self._visible_queueable_check_items()
        checked = sum(item.checkState() == Qt.CheckState.Checked for item in items)
        if not items or checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == len(items):
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked

        self._syncing_select_all = True
        try:
            self.select_all_header.setCheckState(state)
            self.select_all_header.setEnabled(
                bool(items)
                and not (
                    self._queue_thread is not None and self._queue_thread.isRunning()
                )
            )
        finally:
            self._syncing_select_all = False

    def _selected_row_paths(self) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        selection = self.table.selectionModel()
        if selection is None:
            return paths
        for index in selection.selectedRows():
            item = self.table.item(index.row(), 0)
            path = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
        return paths

    def _action_paths(self) -> list[str]:
        checked = self.selected_folder_paths()
        return checked if checked else self._selected_row_paths()

    def _current_folder_path(self) -> str:
        selected = self._selected_row_paths()
        if selected:
            return selected[0]
        checked = self.selected_folder_paths()
        return checked[0] if len(checked) == 1 else ""

    def _update_folder_action_buttons(self) -> None:
        if not hasattr(self, "open_folder_button"):
            return
        queue_running = (
            self._queue_thread is not None and self._queue_thread.isRunning()
        )
        current = self._current_folder_path()
        action_paths = self._action_paths()
        self.open_folder_button.setEnabled(bool(current) and not queue_running)
        self.exclude_button.setEnabled(
            bool(action_paths)
            and not queue_running
            and any(
                (entry := self.state.get(path)) is not None
                and entry.status
                not in {ArchiveFolderStatus.DONE, ArchiveFolderStatus.SKIP}
                for path in action_paths
            )
        )
        self.restore_skip_button.setEnabled(
            bool(action_paths)
            and not queue_running
            and any(
                (entry := self.state.get(path)) is not None
                and entry.status == ArchiveFolderStatus.SKIP
                for path in action_paths
            )
        )

    def open_current_folder(self) -> None:
        path = self._current_folder_path()
        if not path or not Path(path).is_dir():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 1:
            self.open_current_folder()

    def exclude_selected_folders(self) -> None:
        changed = False
        for path in self._action_paths():
            entry = self.state.get(path)
            if entry is None or entry.status in {
                ArchiveFolderStatus.DONE,
                ArchiveFolderStatus.SKIP,
                ArchiveFolderStatus.UPLOADING,
            }:
                continue
            entry.status = ArchiveFolderStatus.SKIP
            entry.last_error = None
            changed = True
        if not changed:
            return
        ArchiveMigrationStateStore.save(self.state, self.profile_name)
        self._populate_table()

    def restore_selected_skips(self) -> None:
        changed = False
        for path in self._selected_row_paths():
            entry = self.state.get(path)
            if entry is None or entry.status != ArchiveFolderStatus.SKIP:
                continue
            entry.status = ArchiveFolderStatus.TODO
            entry.last_error = None
            changed = True
        if not changed:
            return
        ArchiveMigrationStateStore.save(self.state, self.profile_name)
        self._populate_table()

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
        self._sync_select_all_checkbox()
        self._update_folder_action_buttons()

    def _update_root_files_label(self) -> None:
        if not hasattr(self, "root_files_label"):
            return
        folder_entries = list(self.state.folders.values())
        total_files = sum(entry.file_count for entry in folder_entries)
        total_size = sum(entry.size_bytes for entry in folder_entries)
        self.root_files_label.setText(
            self._tr(
                "root_files",
                folders=len(folder_entries),
                files=total_files,
                size=_format_bytes(total_size),
                root_files=self.state.root_file_count,
                root_size=_format_bytes(self.state.root_size_bytes),
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
