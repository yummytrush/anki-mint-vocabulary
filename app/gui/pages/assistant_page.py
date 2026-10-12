"""AI 助手页 — 流式对话、上下文卡片、代码块「应用到编辑器」。

系统提示词来自 prompts/assistant.md；历史仅存内存（最近 20 条）。
"""
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from app.core import config, prompts
from app.gui import strings as S
from app.gui.widgets import MessageBubble
from app.gui.workers import ChatWorker

_HISTORY_LIMIT = 20


class AssistantPage(QWidget):
    sig_apply_code = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._history: list[dict] = []          # 发给 API 的消息（system 除外）
        self._context: dict | None = None       # 提示词页跳转带入的上下文
        self._worker: ChatWorker | None = None
        self._current_bubble: MessageBubble | None = None
        self._has_api_key = False
        self._build_ui()
        self._show_welcome()

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        note = QLabel(S.AS_NOTE)
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        # 上下文卡片
        self.as_context_card = QFrame()
        self.as_context_card.setProperty("role", "panel")
        card_row = QHBoxLayout(self.as_context_card)
        self.as_context_label = QLabel("")
        self.as_context_remove_btn = QPushButton(S.BTN_CONTEXT_REMOVE)
        self.as_context_remove_btn.clicked.connect(self._remove_context)
        card_row.addWidget(self.as_context_label, 1)
        card_row.addWidget(self.as_context_remove_btn)
        self.as_context_card.hide()
        root.addWidget(self.as_context_card)

        # 对话区
        self.as_chat_scroll = QScrollArea()
        self.as_chat_scroll.setWidgetResizable(True)
        self._chat_host = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_host)
        self._chat_layout.setContentsMargins(4, 4, 4, 4)
        self._chat_layout.setSpacing(8)
        self._chat_layout.addStretch(1)
        self.as_chat_scroll.setWidget(self._chat_host)
        root.addWidget(self.as_chat_scroll, 1)

        # 输入区
        input_row = QHBoxLayout()
        self.as_input = QPlainTextEdit()
        self.as_input.setPlaceholderText(S.AS_INPUT_PH)
        self.as_input.setMaximumHeight(80)
        self.as_input.textChanged.connect(self._on_input_changed)
        input_row.addWidget(self.as_input, 1)
        btn_col = QVBoxLayout()
        self.as_send_btn = QPushButton(S.BTN_SEND)
        self.as_send_btn.setProperty("role", "primary")
        self.as_send_btn.clicked.connect(self.send_message)
        self.as_stop_btn = QPushButton(S.BTN_STOP)
        self.as_stop_btn.setProperty("role", "danger")
        self.as_stop_btn.clicked.connect(self._stop_stream)
        self.as_stop_btn.hide()
        btn_col.addWidget(self.as_send_btn)
        btn_col.addWidget(self.as_stop_btn)
        input_row.addLayout(btn_col)
        root.addLayout(input_row)

        bottom = QHBoxLayout()
        self.as_new_btn = QPushButton(S.BTN_NEW_CHAT)
        self.as_new_btn.clicked.connect(self.new_chat)
        self.as_clear_btn = QPushButton(S.BTN_CLEAR_CHAT)
        self.as_clear_btn.clicked.connect(self._clear_chat)
        bottom.addWidget(self.as_new_btn)
        bottom.addWidget(self.as_clear_btn)
        bottom.addStretch(1)
        root.addLayout(bottom)

    # ==================================================================
    # 欢迎卡片与示例
    # ==================================================================
    def _show_welcome(self) -> None:
        bubble = MessageBubble(MessageBubble.ROLE_ASSISTANT)
        bubble.set_text(S.AS_WELCOME)
        bubble.finalize()
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        row = QHBoxLayout()
        for example in (S.AS_EXAMPLE_1, S.AS_EXAMPLE_2, S.AS_EXAMPLE_3):
            btn = QPushButton(example)
            btn.setProperty("role", "muted")
            btn.clicked.connect(lambda _c=False, t=example: self._fill_example(t))
            row.addWidget(btn)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, holder)

    def _fill_example(self, text: str) -> None:
        self.as_input.setPlainText(text)
        self.as_input.setFocus()

    # ==================================================================
    # 上下文（提示词页 / 工作台问题行 跳转）
    # ==================================================================
    def set_context(self, payload: dict) -> None:
        """payload: {"file": str, "content": str, "prefill": str}"""
        self._context = payload
        file_name = payload.get("file") or "（来自工作台的笔记内容）"
        self.as_context_label.setText(S.AS_CONTEXT_FILE.format(file=file_name))
        self.as_context_card.show()
        prefill = payload.get("prefill", "")
        if prefill:
            self.as_input.setPlainText(prefill)
        self.as_input.setFocus()

    def _remove_context(self) -> None:
        self._context = None
        self.as_context_card.hide()

    # ==================================================================
    # 发送与流式
    # ==================================================================
    def _on_input_changed(self) -> None:
        self.as_send_btn.setEnabled(
            bool(self.as_input.toPlainText().strip()) and self._has_api_key and self._worker is None
        )

    def set_api_key_available(self, available: bool) -> None:
        self._has_api_key = available
        if not available:
            self.as_send_btn.setEnabled(False)
            self.as_send_btn.setToolTip(S.ERR_NO_API_KEY)
            self.as_send_btn.setStatusTip(S.ERR_NO_API_KEY)
        else:
            self.as_send_btn.setToolTip("")
        self._on_input_changed()

    def send_message(self) -> None:
        text = self.as_input.toPlainText().strip()
        if not text or self._worker is not None:
            return
        # 上下文包装
        api_text = text
        if self._context and self._context.get("content"):
            api_text = (
                f"【{self._context.get('file') or '笔记内容'} 当前内容】\n"
                f"{self._context['content']}\n\n【我的要求】\n{text}"
            )
        self._add_bubble(MessageBubble.ROLE_USER, text)
        self._history.append({"role": "user", "content": api_text})

        messages = [{"role": "system", "content": prompts.get_prompt("assistant")}]
        messages.extend(self._history[-_HISTORY_LIMIT:])

        cfg = config.AppConfig.load()
        self._worker = ChatWorker(cfg, messages, self)
        self._worker.sig_chunk.connect(self._on_chunk)
        self._worker.sig_done.connect(self._on_done)
        self._worker.sig_error.connect(self._on_stream_error)
        self._current_bubble = self._add_bubble(MessageBubble.ROLE_ASSISTANT, "")
        self.as_input.clear()
        self.as_input.setEnabled(False)
        self.as_send_btn.setEnabled(False)
        self.as_stop_btn.show()
        self._worker.start()

    def _add_bubble(self, role: str, text: str) -> MessageBubble:
        bubble = MessageBubble(role)
        bubble.set_text(text)
        if role == MessageBubble.ROLE_ASSISTANT:
            bubble.sig_apply_code.connect(self.sig_apply_code)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self) -> None:
        bar = self.as_chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_chunk(self, chunk: str) -> None:
        if self._current_bubble:
            self._current_bubble.append_text(chunk)
            self._scroll_to_bottom()

    def _on_done(self, full_text: str) -> None:
        stopped = self._worker and self._worker._stop_event.is_set()
        display = full_text + (S.AS_STOPPED if stopped else "")
        if self._current_bubble:
            self._current_bubble.finalize(display)
        self._history.append({"role": "assistant", "content": full_text})
        self._finish_stream()

    def _on_stream_error(self, message: str) -> None:
        if self._current_bubble:
            self._current_bubble.set_text(S.AS_STREAM_ERROR.format(msg=message))
            self._current_bubble.finalize()
        # 用户消息保留在历史中，方便修正后重发
        self._finish_stream()

    def _finish_stream(self) -> None:
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        self._current_bubble = None
        self.as_input.setEnabled(True)
        self.as_stop_btn.hide()
        self._on_input_changed()
        self.as_input.setFocus()

    def _stop_stream(self) -> None:
        if self._worker:
            self._worker.request_stop()

    # ==================================================================
    # 对话管理
    # ==================================================================
    def new_chat(self) -> None:
        self._history.clear()
        self._remove_context()
        self._clear_bubbles()
        self._show_welcome()

    def _clear_chat(self) -> None:
        ret = QMessageBox.question(self, self.windowTitle(), S.CONFIRM_CLEAR_CHAT)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._history.clear()
        self._clear_bubbles()
        self._show_welcome()

    def _clear_bubbles(self) -> None:
        while self._chat_layout.count() > 1:  # 保留末尾 stretch
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def shutdown(self) -> None:
        if self._worker:
            self._worker.request_stop()
            self._worker.wait(2000)
            if self._worker.isRunning():
                self._worker.terminate()
