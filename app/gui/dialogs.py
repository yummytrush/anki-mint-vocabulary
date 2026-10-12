"""AI 规范化对话框 — 把任意笔记文本交给 AI 转成标准格式（设计 §3.6）。"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from app.core import config, parser
from app.gui import strings as S
from app.gui.workers import NormalizeWorker

_TARGET_FORMATS = [
    ("two_line", S.FORMAT_TWO),
    ("single_line", S.FORMAT_SINGLE),
    ("colon", S.FORMAT_COLON),
    ("word_list", S.FORMAT_WORDS),
]


class NormalizeDialog(QDialog):
    """modal 对话框：输入文本 → AI 转换 → 保存新文件 / 直接使用。"""

    def __init__(self, parent=None, on_use_result=None):
        super().__init__(parent)
        self.setWindowTitle(S.NORM_TITLE)
        self.resize(640, 560)
        self._worker: NormalizeWorker | None = None
        self._on_use_result = on_use_result
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        root.addWidget(QLabel(S.NORM_INPUT_LABEL))
        self.dlg_input = QPlainTextEdit()
        root.addWidget(self.dlg_input, 2)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel(S.NORM_FORMAT_LABEL))
        self.dlg_format = QComboBox()
        for key, label in _TARGET_FORMATS:
            self.dlg_format.addItem(label, key)
        fmt_row.addWidget(self.dlg_format)
        fmt_row.addStretch(1)
        self.dlg_run_btn = QPushButton(S.BTN_NORM_RUN)
        self.dlg_run_btn.setProperty("role", "primary")
        self.dlg_run_btn.clicked.connect(self._run)
        fmt_row.addWidget(self.dlg_run_btn)
        root.addLayout(fmt_row)

        root.addWidget(QLabel(S.NORM_RESULT_LABEL))
        self.dlg_result = QPlainTextEdit()
        self.dlg_result.setReadOnly(True)
        self.dlg_result.setProperty("role", "mono")
        root.addWidget(self.dlg_result, 2)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.dlg_save_btn = QPushButton(S.BTN_NORM_SAVE)
        self.dlg_save_btn.clicked.connect(self._save_as)
        self.dlg_use_btn = QPushButton(S.BTN_NORM_USE)
        self.dlg_use_btn.clicked.connect(self._use_result)
        self.dlg_close_btn = QPushButton(S.BTN_CLOSE)
        self.dlg_close_btn.clicked.connect(self.reject)
        bottom.addWidget(self.dlg_save_btn)
        bottom.addWidget(self.dlg_use_btn)
        bottom.addWidget(self.dlg_close_btn)
        root.addLayout(bottom)

        self._update_buttons()

    def _update_buttons(self) -> None:
        has_result = bool(self.dlg_result.toPlainText().strip())
        running = self._worker is not None
        self.dlg_run_btn.setEnabled(bool(self.dlg_input.toPlainText().strip()) and not running)
        self.dlg_save_btn.setEnabled(has_result and not running)
        self.dlg_use_btn.setEnabled(has_result and not running)

    def _run(self) -> None:
        text = self.dlg_input.toPlainText().strip()
        if not text or self._worker:
            return
        self.dlg_run_btn.setText(S.NORM_RUNNING)
        self._worker = NormalizeWorker(text, self.dlg_format.currentData(), self)
        self._worker.sig_done.connect(self._on_done)
        self._worker.sig_error.connect(self._on_error)
        self._update_buttons()
        self._worker.start()

    def _on_done(self, result: str) -> None:
        self.dlg_result.setPlainText(result)
        self._finish()

    def _on_error(self, message: str) -> None:
        self.dlg_result.setPlainText(S.NORM_ERROR.format(msg=message))
        self._finish()

    def _finish(self) -> None:
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        self.dlg_run_btn.setText(S.BTN_NORM_RUN)
        self._update_buttons()

    def _save_as(self) -> None:
        default_dir = config.DATA_DIR / "intermediate"
        default_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self, S.BTN_NORM_SAVE, str(default_dir / "normalized.md"), "Markdown (*.md)"
        )
        if not path:
            return
        Path(path).write_text(self.dlg_result.toPlainText() + "\n", encoding="utf-8")
        if self.parent() and hasattr(self.parent(), "statusBar"):
            self.parent().statusBar().showMessage(S.TIP_NORM_SAVED.format(path=path), 8000)

    def _use_result(self) -> None:
        out_dir = config.DATA_DIR / "intermediate"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"normalized-{stamp}.md"
        path.write_text(self.dlg_result.toPlainText() + "\n", encoding="utf-8")
        if self._on_use_result:
            self._on_use_result(str(path))
        self.accept()

    def closeEvent(self, event) -> None:
        if self._worker:
            self._worker.wait(2000)
            if self._worker.isRunning():
                self._worker.terminate()
        super().closeEvent(event)
