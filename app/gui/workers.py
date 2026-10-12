"""GUI 后台线程 — PipelineWorker / ConnectionTester / ChatWorker / NormalizeWorker。

线程安全约定（docs/designs/gui-frontend-design.md §7）：
  - worker → UI 一律经 Qt 信号（跨线程自动排队），worker 不触碰 QWidget。
  - 取消经 threading.Event（主线程 set、worker 读）。
  - 传入 worker 的 AppConfig 为快照（调用方负责 dataclasses.replace）。
"""
import threading

from PySide6.QtCore import QThread, Signal

from app.core import config, llm, pipeline
from app.core.pipeline import PipelineCallbacks, RunConfig


class PipelineWorker(QThread):
    """完整生成流水线。取消在 LLM 批次边界生效（缓存保留，可续跑）。"""

    sig_stage = Signal(str)          # parse/sentence_gen/audio/enrich/package
    sig_progress = Signal(int, int)  # done, total
    sig_log = Signal(str)
    sig_finished = Signal(dict)      # {"summary", "cancelled", "failures": [(word, reason)]}
    sig_error = Signal(str)

    def __init__(self, run_config: RunConfig, parent=None):
        super().__init__(parent)
        self._run_config = run_config
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        failures_box: list = []
        try:
            cb = PipelineCallbacks(
                on_progress=lambda done, total, word: self.sig_progress.emit(done, total),
                on_stage_changed=lambda stage: self.sig_stage.emit(stage),
                on_log=lambda line: self.sig_log.emit(line),
                on_failures=lambda failures: failures_box.extend(failures),
                cancel_requested=self.cancel_event.is_set,
            )
            summary = pipeline.run(self._run_config, cb)
            cancelled = bool(summary and "[CANCELLED]" in summary)
            self.sig_finished.emit({
                "summary": summary or "",
                "cancelled": cancelled,
                "failures": failures_box,
            })
        except Exception as e:  # noqa: BLE001 — 任何异常都要落到 UI 失败态
            self.sig_error.emit(str(e))


class ConnectionTester(QThread):
    """LLM 连通测试（错误分类渲染由页面负责）。"""

    sig_result = Signal(object)  # llm.ConnectionResult

    def __init__(self, cfg_snapshot, parent=None):
        super().__init__(parent)
        self._cfg = cfg_snapshot

    def run(self) -> None:
        self.sig_result.emit(llm.test_llm_connection(self._cfg))


class ChatWorker(QThread):
    """AI 助手流式对话。"""

    sig_chunk = Signal(str)
    sig_done = Signal(str)    # 完整（或被停止时的部分）文本
    sig_error = Signal(str)

    def __init__(self, cfg_snapshot, messages: list[dict], parent=None):
        super().__init__(parent)
        self._cfg = cfg_snapshot
        self._messages = messages
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        full: list[str] = []
        try:
            for chunk in llm.llm_chat_stream(self._cfg, self._messages):
                if self._stop_event.is_set():
                    break
                full.append(chunk)
                self.sig_chunk.emit(chunk)
            self.sig_done.emit("".join(full))
        except Exception as e:  # noqa: BLE001
            self.sig_error.emit(str(e))


class NormalizeWorker(QThread):
    """AI 格式规范化（normalize_any_text）。"""

    sig_done = Signal(str)
    sig_error = Signal(str)

    def __init__(self, text: str, target_format: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._target_format = target_format

    def run(self) -> None:
        try:
            result = pipeline.normalize_any_text(self._text, self._target_format)
            if result:
                self.sig_done.emit(result)
            else:
                self.sig_error.emit("API 调用失败（详见日志）")
        except Exception as e:  # noqa: BLE001
            self.sig_error.emit(str(e))
