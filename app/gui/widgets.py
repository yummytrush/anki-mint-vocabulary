"""GUI 自定义控件 — DropZone / LogPanel / ParamRow / ProgressStages / MessageBubble。

全部控件零业务逻辑，只负责交互与展示；信号定义见各控件 docstring。
"""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from app.gui import strings as S

ALLOWED_SUFFIXES = (".md", ".txt")


def local_md_path_from_urls(urls: list[QUrl]) -> str | None:
    """从拖拽 URL 列表中取第一个本地 .md/.txt 文件路径；非法返回 None。"""
    for url in urls:
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        if Path(path).suffix.lower() in ALLOWED_SUFFIXES:
            return path
    return None


class DropZone(QFrame):
    """文件拖拽区：接受本地 .md/.txt；点击发出 sig_clicked（页面负责弹文件对话框）。"""

    sig_file_dropped = Signal(str)
    sig_clicked = Signal()
    sig_rejected = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(110)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = QLabel(S.DROP_HINT)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setProperty("role", "muted")
        layout.addWidget(self._hint)
        self.setProperty("dragActive", False)

    def set_hint(self, text: str) -> None:
        self._hint.setText(text)

    def _set_drag_active(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:
        if local_md_path_from_urls(event.mimeData().urls()):
            self._set_drag_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if local_md_path_from_urls(event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._set_drag_active(False)
        path = local_md_path_from_urls(event.mimeData().urls())
        if path:
            self.sig_file_dropped.emit(path)
            event.acceptProposedAction()
        else:
            self.sig_rejected.emit()
            event.ignore()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.sig_clicked.emit()
        super().mouseReleaseEvent(event)


class LogPanel(QPlainTextEdit):
    """只读日志面板（等宽），支持占位提示与逐行追加。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setProperty("role", "mono")
        self.setPlaceholderText(S.LOG_PLACEHOLDER)
        self.setMinimumHeight(140)

    def append_line(self, line: str) -> None:
        self.appendPlainText(line)
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class ParamRow(QWidget):
    """高级设置参数行：标签 + 控件 + ↺ 单项重置。kind: check/spin/combo/line/hours。

    sig_changed() 任一改动时发出；value()/set_value() 统一取值（hours 返回秒）。
    """

    sig_changed = Signal()

    def __init__(self, key: str, spec: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self.kind = spec["kind"]
        self._default = spec.get("default")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self._check: QCheckBox | None = None
        if self.kind == "check":
            self._check = QCheckBox(spec["label"])
            self._check.setToolTip(spec["tip"])
            self._check.setStatusTip(spec["tip"])
            self._check.toggled.connect(lambda *_: self.sig_changed.emit())
            layout.addWidget(self._check, 1)
            self._label = self._check
        else:
            self._label = QLabel(spec["label"])
            self._label.setToolTip(spec["tip"])
            self._label.setStatusTip(spec["tip"])
            self._label.setMinimumWidth(130)
            layout.addWidget(self._label)

        if self.kind == "spin" or self.kind == "hours":
            self.control = QSpinBox()
            self.control.setRange(spec.get("min", 0), spec.get("max", 99))
            self.control.setToolTip(spec["tip"])
            self.control.setStatusTip(spec["tip"])
            self.control.valueChanged.connect(lambda *_: self.sig_changed.emit())
        elif self.kind == "combo":
            self.control = QComboBox()
            self.control.addItems(spec["options"])
            self.control.setToolTip(spec["tip"])
            self.control.setStatusTip(spec["tip"])
            self.control.currentTextChanged.connect(lambda *_: self.sig_changed.emit())
        else:  # line
            self.control = QLineEdit()
            if spec.get("placeholder"):
                self.control.setPlaceholderText(spec["placeholder"])
            self.control.setToolTip(spec["tip"])
            self.control.setStatusTip(spec["tip"])
            self.control.textChanged.connect(lambda *_: self.sig_changed.emit())

        layout.addWidget(self.control, 1)

        self.reset_btn = QToolButton()
        self.reset_btn.setText("↺")
        self.reset_btn.setToolTip(S.BTN_RESET)
        self.reset_btn.setStatusTip(S.BTN_RESET)
        self.reset_btn.setAccessibleName(S.BTN_RESET)
        self.reset_btn.clicked.connect(self.reset_to_default)
        layout.addWidget(self.reset_btn)

    def value(self):
        if self.kind == "check":
            return self._check.isChecked()
        if self.kind == "hours":
            return self.control.value() * 3600
        if self.kind == "spin":
            return self.control.value()
        if self.kind == "combo":
            return self.control.currentText()
        return self.control.text().strip()

    def set_value(self, value) -> None:
        if self.kind == "check":
            self._check.setChecked(bool(value))
        elif self.kind == "hours":
            self.control.setValue(round((value or 0) / 3600))
        elif self.kind == "spin":
            self.control.setValue(int(value))
        elif self.kind == "combo":
            idx = self.control.findText(str(value))
            self.control.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            self.control.setText(str(value or ""))

    def set_default(self, value) -> None:
        self._default = value

    def reset_to_default(self) -> None:
        if self._default is not None:
            self.set_value(self._default)


class ProgressStages(QWidget):
    """阶段指示器：pending(灰) / active(主色胶囊) / done(绿✓)。"""

    def __init__(self, stages: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self._labels: dict[str, QLabel] = {}
        self._order = [key for key, _ in stages]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for i, (key, label) in enumerate(stages):
            if i:
                arrow = QLabel("─")
                arrow.setProperty("role", "muted")
                layout.addWidget(arrow)
            chip = QLabel(label)
            chip.setProperty("stageState", "pending")
            layout.addWidget(chip)
            self._labels[key] = chip
        layout.addStretch(1)

    def _set_state(self, key: str, state: str) -> None:
        chip = self._labels[key]
        chip.setProperty("stageState", state)
        chip.style().unpolish(chip)
        chip.style().polish(chip)

    def reset(self) -> None:
        for key in self._order:
            self._set_state(key, "pending")

    def set_active(self, key: str) -> None:
        """key 之前的阶段标 done，key 标 active，之后 pending。"""
        seen = False
        for k in self._order:
            if k == key:
                self._set_state(k, "active")
                seen = True
            elif not seen:
                self._set_state(k, "done")
            else:
                self._set_state(k, "pending")

    def mark_done(self, key: str) -> None:
        self._set_state(key, "done")

    def mark_all_done(self) -> None:
        for key in self._order:
            self._set_state(key, "done")


class MessageBubble(QFrame):
    """AI 助手对话气泡。

    流式期间 append_text() 追加；finalize(full_text) 分段渲染：
    普通段落 → QLabel，``` 围栏代码块 → 等宽只读框 + 「应用到编辑器」按钮。
    sig_apply_code(str)：点击某代码块的「应用到编辑器」时发出块全文。
    """

    sig_apply_code = Signal(str)

    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"

    def __init__(self, role: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self._full_text = ""
        self.setProperty("role", "bubbleUser" if role == self.ROLE_USER else "bubbleAssistant")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(6)
        self._stream_label = QLabel("")
        self._stream_label.setWordWrap(True)
        self._stream_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._layout.addWidget(self._stream_label)

    def set_text(self, text: str) -> None:
        self._full_text = text
        self._stream_label.setText(text)

    def append_text(self, chunk: str) -> None:
        self._full_text += chunk
        self._stream_label.setText(self._full_text + "▌")

    def full_text(self) -> str:
        return self._full_text

    # ---- 完成渲染 ----
    _FENCE = "```"

    def finalize(self, full_text: str | None = None) -> None:
        """流式结束后分段渲染（文本段落 + 代码块 + 应用按钮）。"""
        if full_text is not None:
            self._full_text = full_text
        # 清空流式 label
        self._stream_label.deleteLater()
        self._stream_label = None

        segments = _split_code_segments(self._full_text)
        for kind, text in segments:
            if kind == "code":
                self._add_code_block(text)
            elif text.strip():
                label = QLabel(text.strip())
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self._layout.addWidget(label)

    def _add_code_block(self, code: str) -> None:
        block = QPlainTextEdit()
        block.setReadOnly(True)
        block.setProperty("role", "codeBlock")
        block.setPlainText(code.strip("\n"))
        # 按行数自适应高度（2~16 行）
        lines = max(2, min(16, code.strip("\n").count("\n") + 1))
        fm = QFontMetrics(block.font())
        block.setFixedHeight(fm.lineSpacing() * (lines + 1) + 14)
        self._layout.addWidget(block)

        btn = QPushButton(S.BTN_APPLY)
        btn.setFixedWidth(110)
        btn.clicked.connect(lambda _checked=False, c=code.strip("\n"): self.sig_apply_code.emit(c))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        self._layout.addLayout(row)


def _split_code_segments(text: str) -> list[tuple[str, str]]:
    """把消息按 ``` 围栏切成 [("text"|"code", 内容), ...]。未闭合的围栏按文本处理。"""
    segments: list[tuple[str, str]] = []
    parts = text.split(MessageBubble._FENCE)
    # 偶数下标 = 文本，奇数下标 = 代码块；奇数个部分说明有未闭合围栏
    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part:
                segments.append(("text", part))
        else:
            if i == len(parts) - 1 and len(parts) % 2 == 0:
                # 未闭合围栏 → 作为文本
                segments.append(("text", MessageBubble._FENCE + part))
            else:
                # 剥离首行语言标记（如 ```json）
                lines = part.split("\n", 1)
                if len(lines) == 2 and "\n" not in lines[0] and len(lines[0]) <= 20:
                    segments.append(("code", lines[1]))
                else:
                    segments.append(("code", part))
    return segments
