"""提示词页 — 文件列表、编辑器、保存热加载、恢复默认、跳转 AI 助手。"""
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from app.core import prompts
from app.gui import strings as S

# 展示顺序与显示名（文件名 → 中文显示名）
PROMPT_FILES: list[tuple[str, str]] = [
    ("enrich", S.PR_NAME_ENRICH),
    ("sentence_gen", S.PR_NAME_SENTENCE_GEN),
    ("check_accuracy", S.PR_NAME_CHECK_ACCURACY),
    ("check_completeness", S.PR_NAME_CHECK_COMPLETENESS),
    ("fix", S.PR_NAME_FIX),
    ("format_assist", S.PR_NAME_FORMAT_ASSIST),
    ("assistant", S.PR_NAME_ASSISTANT),
]


class PromptsPage(QWidget):
    sig_request_assistant = Signal(dict)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._current_name: str = ""
        self._build_ui()
        self._populate_files()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        note = QLabel(S.PR_NOTE)
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        body = QHBoxLayout()
        self.pr_file_list = QListWidget()
        self.pr_file_list.setMaximumWidth(240)
        self.pr_file_list.currentItemChanged.connect(self._on_file_selected)
        body.addWidget(self.pr_file_list)

        right = QVBoxLayout()
        self.pr_title_label = QLabel("")
        right.addWidget(self.pr_title_label)
        self.pr_editor = QPlainTextEdit()
        self.pr_editor.setProperty("role", "mono")
        self.pr_editor.setPlaceholderText(S.PR_EMPTY_EDITOR)
        self.pr_editor.textChanged.connect(self._on_edited)
        right.addWidget(self.pr_editor, 1)

        btn_row = QHBoxLayout()
        self.pr_assist_btn = QPushButton(S.BTN_PR_ASSIST)
        self.pr_assist_btn.clicked.connect(self._goto_assistant)
        btn_row.addWidget(self.pr_assist_btn)
        btn_row.addStretch(1)
        self.pr_restore_btn = QPushButton(S.BTN_RESET)
        self.pr_restore_btn.clicked.connect(self._restore_default)
        self.pr_revert_btn = QPushButton(S.BTN_PR_REVERT)
        self.pr_revert_btn.clicked.connect(self._revert)
        self.pr_save_btn = QPushButton(S.BTN_PR_SAVE)
        self.pr_save_btn.setProperty("role", "primary")
        self.pr_save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.pr_restore_btn)
        btn_row.addWidget(self.pr_revert_btn)
        btn_row.addWidget(self.pr_save_btn)
        right.addLayout(btn_row)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        bottom = QHBoxLayout()
        self.pr_open_prompts_btn = QPushButton(S.BTN_OPEN_PROMPTS)
        self.pr_open_prompts_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(prompts.PROMPTS_DIR)))
        )
        self.pr_open_templates_btn = QPushButton(S.BTN_OPEN_TEMPLATES)
        from app.core import templates as _templates
        self.pr_open_templates_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(_templates.TEMPLATES_DIR)))
        )
        bottom.addWidget(self.pr_open_prompts_btn)
        bottom.addWidget(self.pr_open_templates_btn)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self._set_editor_enabled(False)

    def _populate_files(self) -> None:
        for name, display in PROMPT_FILES:
            item = QListWidgetItem(display)
            item.setData(32, name)  # Qt.ItemDataRole.UserRole
            self.pr_file_list.addItem(item)
        if self.pr_file_list.count():
            self.pr_file_list.setCurrentRow(0)

    # ==================================================================
    # 文件装载 / 编辑状态
    # ==================================================================
    def _on_file_selected(self, current: QListWidgetItem, _previous) -> None:
        if current is None:
            return
        if self._dirty() and not self._confirm_discard():
            # 放弃切换：回选到之前的文件
            self.pr_file_list.blockSignals(True)
            self._reselect(self._current_name)
            self.pr_file_list.blockSignals(False)
            return
        self._load_file(current.data(32))

    def _reselect(self, name: str) -> None:
        for i in range(self.pr_file_list.count()):
            if self.pr_file_list.item(i).data(32) == name:
                self.pr_file_list.setCurrentRow(i)
                return

    def _load_file(self, name: str) -> None:
        self._current_name = name
        path = prompts.PROMPTS_DIR / f"{name}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = prompts.DEFAULT_PROMPTS.get(name, "")
        self.pr_editor.blockSignals(True)
        self.pr_editor.setPlainText(text)
        self.pr_editor.blockSignals(False)
        self._set_editor_enabled(True)
        self._update_title(saved=True)

    def _dirty(self) -> bool:
        return self.pr_editor.document().isModified()

    def _confirm_discard(self) -> bool:
        ret = QMessageBox.question(self, self.windowTitle(), S.CONFIRM_DISCARD)
        return ret == QMessageBox.StandardButton.Yes

    def _on_edited(self) -> None:
        self._update_title(saved=False)

    def _update_title(self, saved: bool) -> None:
        display = dict(PROMPT_FILES).get(self._current_name, self._current_name)
        self.pr_title_label.setText(display if saved else f"{display}    {S.DIRTY_BADGE}")
        has_content = bool(self._current_name)
        self.pr_save_btn.setEnabled(has_content and not saved)
        self.pr_revert_btn.setEnabled(has_content and not saved)
        self.pr_restore_btn.setEnabled(has_content)
        self.pr_assist_btn.setEnabled(has_content)

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.pr_editor.setEnabled(enabled)

    # ==================================================================
    # 操作
    # ==================================================================
    def _save(self) -> None:
        if not self._current_name:
            return
        path = prompts.PROMPTS_DIR / f"{self._current_name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.pr_editor.toPlainText(), encoding="utf-8")
        prompts.reload_prompt(self._current_name)
        self.pr_editor.document().setModified(False)
        self._update_title(saved=True)
        self.window().statusBar().showMessage(S.TIP_PR_SAVED, 5000)

    def _revert(self) -> None:
        self._load_file(self._current_name)

    def _restore_default(self) -> None:
        ret = QMessageBox.question(
            self, self.windowTitle(),
            S.CONFIRM_PR_RESTORE.format(file=f"{self._current_name}.md"),
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        text = prompts.restore_default(self._current_name)
        path = prompts.PROMPTS_DIR / f"{self._current_name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        prompts.reload_prompt(self._current_name)
        self.pr_editor.blockSignals(True)
        self.pr_editor.setPlainText(text)
        self.pr_editor.blockSignals(False)
        self.pr_editor.document().setModified(False)
        self._update_title(saved=True)

    def _goto_assistant(self) -> None:
        self.sig_request_assistant.emit({
            "file": f"prompts/{self._current_name}.md",
            "content": self.pr_editor.toPlainText(),
            "prefill": S.PR_ASSIST_PREFILL,
        })

    # ==================================================================
    # AI 助手「应用到编辑器」入口
    # ==================================================================
    def apply_code(self, code_text: str) -> None:
        """把 AI 回复的代码块写入编辑器（不自动保存）。"""
        if self._dirty() and not self._confirm_discard():
            return
        self.pr_editor.blockSignals(True)
        self.pr_editor.setPlainText(code_text)
        self.pr_editor.blockSignals(False)
        self.pr_editor.document().setModified(True)
        self._update_title(saved=False)
        self.window().statusBar().showMessage(S.TIP_APPLIED, 8000)
