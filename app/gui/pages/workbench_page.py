"""工作台页 — 拖拽、预检、输出设置、运行/取消、日志与失败词列表。

状态机（docs/designs/gui-frontend-design.md §3.1）：
idle / previewed / running / cancelled / failed / success
"""
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QProgressBar, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from app.core import cache, config, parser
from app.core.anki_packager import DEFAULT_DECK_NAME
from app.core.pipeline import RunConfig
from app.gui import strings as S
from app.gui.widgets import DropZone, LogPanel, ProgressStages
from app.gui.workers import PipelineWorker

_FORMAT_ITEMS = [
    (S.FORMAT_AUTO, None),
    (S.FORMAT_SINGLE, "single_line"),
    (S.FORMAT_TWO, "two_line"),
    (S.FORMAT_COLON, "colon"),
    (S.FORMAT_WORDS, "word_list"),
]

_STAGE_ORDER = [
    ("parse", S.STAGE_PARSE),
    ("sentence_gen", S.STAGE_SENTGEN),
    ("audio", S.STAGE_AUDIO),
    ("enrich", S.STAGE_ENRICH),
    ("package", S.STAGE_PACKAGE),
]


class WorkbenchPage(QWidget):
    sig_request_assistant = Signal(dict)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._file_path: str = ""
        self._preview = None
        self._state = "idle"
        self._worker: PipelineWorker | None = None
        self._has_api_key = False
        self._build_ui()
        self._set_state("idle")

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # --- 拖拽区 ---
        self.wb_drop = DropZone()
        self.wb_drop.sig_file_dropped.connect(self.set_file)
        self.wb_drop.sig_clicked.connect(self._pick_file)
        self.wb_drop.sig_rejected.connect(lambda: self._show_banner(S.DROP_REJECT, "warning"))
        root.addWidget(self.wb_drop)

        file_row = QHBoxLayout()
        self.wb_file_label = QLabel("")
        self.wb_file_label.setProperty("role", "muted")
        self.wb_repick_btn = QPushButton(S.BTN_REPICK)
        self.wb_repick_btn.clicked.connect(self._pick_file)
        file_row.addWidget(self.wb_file_label, 1)
        file_row.addWidget(self.wb_repick_btn)
        self._file_row_widget = QWidget()
        self._file_row_widget.setLayout(file_row)
        root.addWidget(self._file_row_widget)
        self._file_row_widget.hide()

        # --- 解析预检 ---
        self.wb_preview_group = QGroupBox(S.PREVIEW_TITLE)
        pv = QVBoxLayout(self.wb_preview_group)
        sum_row = QHBoxLayout()
        self.wb_preview_summary = QLabel("")
        self.wb_problems_toggle = QToolButton()
        self.wb_problems_toggle.setText(S.PROBLEMS_TOGGLE)
        self.wb_problems_toggle.setCheckable(True)
        sum_row.addWidget(self.wb_preview_summary, 1)
        sum_row.addWidget(self.wb_problems_toggle)
        pv.addLayout(sum_row)

        self.wb_problems_list = QListWidget()
        self.wb_problems_list.setProperty("role", "mono")
        self.wb_problems_list.setMaximumHeight(110)
        self.wb_problems_list.hide()
        self.wb_problems_toggle.toggled.connect(self.wb_problems_list.setVisible)
        pv.addWidget(self.wb_problems_list)

        prob_row = QHBoxLayout()
        prob_note = QLabel(S.PROBLEMS_IGNORE_NOTE)
        prob_note.setProperty("role", "muted")
        self.wb_goto_assistant_btn = QPushButton(S.BTN_GOTO_ASSISTANT)
        self.wb_goto_assistant_btn.clicked.connect(self._goto_assistant)
        prob_row.addWidget(prob_note, 1)
        prob_row.addWidget(self.wb_goto_assistant_btn)
        self._prob_row_widget = QWidget()
        self._prob_row_widget.setLayout(prob_row)
        pv.addWidget(self._prob_row_widget)

        self.wb_resume_hint = QLabel("")
        self.wb_resume_hint.setProperty("role", "warning")
        self.wb_resume_hint.hide()
        pv.addWidget(self.wb_resume_hint)
        root.addWidget(self.wb_preview_group)
        self.wb_preview_group.hide()

        # --- 设置行 ---
        settings = QHBoxLayout()
        settings.addWidget(QLabel(S.FORMAT_LABEL))
        self.wb_format_combo = QComboBox()
        for label, _fmt in _FORMAT_ITEMS:
            self.wb_format_combo.addItem(label)
        self.wb_format_combo.currentIndexChanged.connect(self._on_format_changed)
        settings.addWidget(self.wb_format_combo)
        settings.addSpacing(12)
        settings.addWidget(QLabel(S.OUTPUT_DIR_LABEL))
        self.wb_output_dir_edit = QLineEdit(str(config.OUTPUT_DIR))
        settings.addWidget(self.wb_output_dir_edit, 1)
        self.wb_output_dir_btn = QPushButton(S.BTN_BROWSE)
        self.wb_output_dir_btn.clicked.connect(self._pick_output_dir)
        settings.addWidget(self.wb_output_dir_btn)
        root.addLayout(settings)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(S.FILENAME_LABEL))
        self.wb_filename_edit = QLineEdit(S.FILENAME_DEFAULT)
        self.wb_filename_edit.setToolTip(S.FILENAME_NOTE)
        self.wb_filename_edit.setStatusTip(S.FILENAME_NOTE)
        self.wb_filename_edit.textChanged.connect(self._update_filename_preview)
        name_row.addWidget(self.wb_filename_edit, 1)
        self.wb_filename_preview = QLabel("")
        self.wb_filename_preview.setProperty("role", "muted")
        name_row.addWidget(self.wb_filename_preview)
        self.wb_normalize_btn = QPushButton(S.BTN_NORMALIZE)
        self.wb_normalize_btn.setToolTip(S.TIP_NORMALIZE)
        self.wb_normalize_btn.setStatusTip(S.TIP_NORMALIZE)
        name_row.addWidget(self.wb_normalize_btn)
        self.wb_force_chk = QCheckBox(S.FORCE_CHK)
        self.wb_force_chk.toggled.connect(lambda _c: self._update_resume_hint())
        name_row.addWidget(self.wb_force_chk)
        root.addLayout(name_row)

        deck_row = QHBoxLayout()
        deck_row.addWidget(QLabel(S.DECK_NAME_LABEL))
        self.wb_deck_name_edit = QLineEdit()
        self.wb_deck_name_edit.setPlaceholderText(DEFAULT_DECK_NAME)
        self.wb_deck_name_edit.setToolTip(S.TIP_DECK_NAME)
        self.wb_deck_name_edit.setStatusTip(S.TIP_DECK_NAME)
        deck_row.addWidget(self.wb_deck_name_edit, 1)
        root.addLayout(deck_row)

        # --- 运行区 ---
        run_group = QGroupBox("")
        run = QVBoxLayout(run_group)
        self.wb_stage_widget = ProgressStages(_STAGE_ORDER)
        run.addWidget(self.wb_stage_widget)

        prog_row = QHBoxLayout()
        self.wb_progress_bar = QProgressBar()
        self.wb_progress_bar.setRange(0, 100)
        self.wb_progress_bar.setValue(0)
        prog_row.addWidget(self.wb_progress_bar, 1)
        self.wb_start_btn = QPushButton(S.BTN_START)
        self.wb_start_btn.setProperty("role", "primary")
        self.wb_start_btn.clicked.connect(self.start_run)
        self.wb_cancel_btn = QPushButton(S.BTN_CANCEL_RUN)
        self.wb_cancel_btn.setProperty("role", "danger")
        self.wb_cancel_btn.clicked.connect(self.cancel_run)
        self.wb_cancel_btn.hide()
        prog_row.addWidget(self.wb_start_btn)
        prog_row.addWidget(self.wb_cancel_btn)
        run.addLayout(prog_row)

        self.wb_banner = QLabel("")
        self.wb_banner.setWordWrap(True)
        self.wb_banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.wb_banner.setOpenExternalLinks(False)
        self.wb_banner.linkActivated.connect(self._open_output_dir)
        self.wb_banner.hide()
        run.addWidget(self.wb_banner)

        log_row = QHBoxLayout()
        log_row.addStretch(1)
        self.wb_log_clear_btn = QPushButton(S.BTN_LOG_CLEAR)
        self.wb_log_clear_btn.clicked.connect(lambda: self.wb_log_panel.clear())
        log_row.addWidget(self.wb_log_clear_btn)
        run.addLayout(log_row)
        self.wb_log_panel = LogPanel()
        run.addWidget(self.wb_log_panel)

        self.wb_failure_group = QGroupBox(S.FAILURE_TITLE.format(n=0))
        fail = QVBoxLayout(self.wb_failure_group)
        self.wb_failure_list = QListWidget()
        fail.addWidget(self.wb_failure_list)
        fail_row = QHBoxLayout()
        fail_note = QLabel(S.FAILURE_NOTE)
        fail_note.setProperty("role", "muted")
        self.wb_failure_copy_btn = QPushButton(S.BTN_COPY)
        self.wb_failure_copy_btn.clicked.connect(self._copy_failures)
        fail_row.addWidget(fail_note, 1)
        fail_row.addWidget(self.wb_failure_copy_btn)
        fail.addLayout(fail_row)
        self.wb_failure_group.hide()
        run.addWidget(self.wb_failure_group)

        root.addWidget(run_group, 1)

    # ==================================================================
    # 文件选择与预检
    # ==================================================================
    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, S.PREVIEW_TITLE, "", "Markdown / Text (*.md *.txt)"
        )
        if path:
            self.set_file(path)

    def _pick_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, S.OUTPUT_DIR_LABEL, self.wb_output_dir_edit.text())
        if path:
            self.wb_output_dir_edit.setText(path)

    def set_file(self, path: str) -> None:
        self._file_path = path
        self.wb_file_label.setText(path)
        self._file_row_widget.show()
        self.wb_format_combo.setCurrentIndex(0)  # 回到自动检测
        self._run_preview()

    def _on_format_changed(self, _idx: int) -> None:
        if self._file_path:
            self._run_preview()

    def _forced_format(self):
        return _FORMAT_ITEMS[self.wb_format_combo.currentIndex()][1]

    def _run_preview(self) -> None:
        try:
            self._preview = parser.parse_preview(self._file_path, force_format=self._forced_format())
        except Exception as e:  # noqa: BLE001
            self._show_banner(S.BANNER_FAILED.format(err=str(e)), "error")
            self._set_state("idle")
            return
        p = self._preview
        fmt_label = parser.FORMAT_LABELS.get(p.detected_format, p.detected_format)
        self.wb_preview_summary.setText(
            S.PREVIEW_SUMMARY.format(n=len(p.entries), fmt=fmt_label, p=len(p.problem_lines))
        )
        self.wb_problems_list.clear()
        for prob in p.problem_lines:
            self.wb_problems_list.addItem(f"[行 {prob.lineno}] {prob.reason}: {prob.text[:60]}")
        has_problems = bool(p.problem_lines)
        self.wb_problems_toggle.setVisible(has_problems)
        self.wb_problems_list.setVisible(has_problems and self.wb_problems_toggle.isChecked())
        self._prob_row_widget.setVisible(has_problems)
        self.wb_preview_group.show()
        self._update_resume_hint()
        self._update_filename_preview()
        self._set_state("previewed")

    def _update_resume_hint(self) -> None:
        if not self._preview or self.wb_force_chk.isChecked():
            self.wb_resume_hint.hide()
            return
        processed = cache.migrate_processed_keys(cache.load_processed(config.PROCESSED_FILE))
        done = 0
        for entry in self._preview.entries:
            record = processed.get(entry.word.lower())
            if record and cache.processed_record_complete(record, cache.compute_sentence_hash(entry.sentence)):
                done += 1
        if done > 0:
            self.wb_resume_hint.setText(S.RESUME_HINT.format(done=done, total=len(self._preview.entries)))
            self.wb_resume_hint.show()
        else:
            self.wb_resume_hint.hide()

    def _update_filename_preview(self) -> None:
        self.wb_filename_preview.setText("→ " + self._resolved_filename())

    def _resolved_filename(self) -> str:
        name = self.wb_filename_edit.text().strip() or S.FILENAME_DEFAULT
        count = len(self._preview.entries) if self._preview else 0
        name = name.replace("{日期}", datetime.now().strftime("%Y-%m-%d"))
        name = name.replace("{词数}", str(count))
        if not name.lower().endswith(".apkg"):
            name += ".apkg"
        return name

    def output_path(self) -> str:
        return str(Path(self.wb_output_dir_edit.text().strip() or str(config.OUTPUT_DIR)) / self._resolved_filename())

    # ==================================================================
    # 状态机
    # ==================================================================
    def _set_state(self, state: str) -> None:
        self._state = state
        running = state == "running"
        has_file = bool(self._file_path)

        self.wb_drop.setEnabled(not running)
        self.wb_repick_btn.setEnabled(not running)
        self.wb_format_combo.setEnabled(not running and state != "idle")
        for w in (self.wb_output_dir_edit, self.wb_output_dir_btn, self.wb_filename_edit,
                  self.wb_deck_name_edit, self.wb_normalize_btn, self.wb_force_chk):
            w.setEnabled(not running)

        self.wb_start_btn.setVisible(not running)
        self.wb_cancel_btn.setVisible(running)
        can_start = (not running) and state in ("previewed", "cancelled", "failed", "success") \
            and has_file and self._has_api_key
        self.wb_start_btn.setEnabled(can_start)
        if not self._has_api_key:
            self.wb_start_btn.setToolTip(S.ERR_NO_API_KEY)
            self.wb_start_btn.setStatusTip(S.ERR_NO_API_KEY)
        else:
            self.wb_start_btn.setToolTip("")
        self.wb_start_btn.setText(S.BTN_START if state in ("previewed",) else S.BTN_START_AGAIN)

        if state == "idle":
            self.wb_progress_bar.setRange(0, 100)
            self.wb_progress_bar.setValue(0)
            self.wb_stage_widget.reset()

    def set_api_key_available(self, available: bool) -> None:
        self._has_api_key = available
        self._set_state(self._state)

    # ==================================================================
    # 运行 / 取消
    # ==================================================================
    def start_run(self) -> None:
        if not self._preview or not self._preview.entries:
            return
        args = RunConfig(
            input=self._file_path,
            output=self.output_path(),
            force=self.wb_force_chk.isChecked(),
            word_list=(self._forced_format() == "word_list"),
            deck_name=self.wb_deck_name_edit.text().strip(),
        )
        self._worker = PipelineWorker(args, self)
        self._worker.sig_stage.connect(self._on_stage)
        self._worker.sig_progress.connect(self._on_progress)
        self._worker.sig_log.connect(self.wb_log_panel.append_line)
        self._worker.sig_finished.connect(self._on_finished)
        self._worker.sig_error.connect(self._on_error)
        self.wb_failure_group.hide()
        self.wb_failure_list.clear()
        self._show_banner("", "")
        self.wb_stage_widget.reset()
        self.wb_progress_bar.setRange(0, 0)  # 忙等样式
        self._set_state("running")
        self._worker.start()

    def cancel_run(self) -> None:
        if self._worker:
            self.wb_cancel_btn.setEnabled(False)
            self.wb_cancel_btn.setText(S.CANCELLING)
            self._worker.request_cancel()

    def _on_stage(self, stage: str) -> None:
        if stage in dict(_STAGE_ORDER):
            self.wb_stage_widget.set_active(stage)
        if stage == "enrich":
            self.wb_progress_bar.setRange(0, 100)
            self.wb_progress_bar.setValue(0)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.wb_progress_bar.setRange(0, total)
            self.wb_progress_bar.setValue(done)
            self.wb_progress_bar.setFormat(S.PROGRESS_ENRICH.format(done=done, total=total))

    def _on_finished(self, result: dict) -> None:
        self.wb_stage_widget.mark_all_done()
        cancelled = result.get("cancelled")
        failures = result.get("failures") or []
        if failures:
            self.wb_failure_group.setTitle(S.FAILURE_TITLE.format(n=len(failures)))
            for word, reason in failures:
                self.wb_failure_list.addItem(f"· {word}: {reason}")
            self.wb_failure_group.show()
        if cancelled:
            self._show_banner(S.BANNER_CANCELLED, "warning")
            self._set_state("cancelled")
        else:
            out = self.output_path()
            self._show_banner(
                f'{S.BANNER_SUCCESS.format(output=out)} — <a href="open">{S.OPEN_OUTPUT_DIR}</a>',
                "success",
            )
            self._set_state("success")
        self._cleanup_worker()

    def _on_error(self, message: str) -> None:
        self._show_banner(S.BANNER_FAILED.format(err=message), "error")
        self._set_state("failed")
        self._cleanup_worker()

    def _cleanup_worker(self) -> None:
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        self.wb_cancel_btn.setEnabled(True)
        self.wb_cancel_btn.setText(S.BTN_CANCEL_RUN)

    def shutdown(self) -> None:
        """窗口关闭时：请求取消并短暂等待。"""
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait(3000)
            if self._worker.isRunning():
                self._worker.terminate()
                self._worker.wait(1000)

    # ==================================================================
    # 其它交互
    # ==================================================================
    def _show_banner(self, text: str, kind: str) -> None:
        if not text:
            self.wb_banner.hide()
            return
        self.wb_banner.setText(text)
        self.wb_banner.setProperty("role", kind or "muted")
        self.wb_banner.style().unpolish(self.wb_banner)
        self.wb_banner.style().polish(self.wb_banner)
        self.wb_banner.show()

    def _open_output_dir(self, _link: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.output_path()).parent)))

    def _copy_failures(self) -> None:
        from PySide6.QtWidgets import QApplication
        lines = [self.wb_failure_list.item(i).text() for i in range(self.wb_failure_list.count())]
        QApplication.clipboard().setText("\n".join(lines))

    def _goto_assistant(self) -> None:
        if not self._preview or not self._preview.problem_lines:
            return
        content = "\n".join(
            f"[行 {p.lineno}] {p.reason}: {p.text}" for p in self._preview.problem_lines
        )
        self.sig_request_assistant.emit({
            "file": "",
            "content": content,
            "prefill": S.AS_PROBLEM_PREFILL,
        })

    # 供规范化对话框使用
    def reload_file(self, path: str) -> None:
        self.set_file(path)
