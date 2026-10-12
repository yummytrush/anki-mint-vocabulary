"""AI 设置页 — 供应商预设、Key/模型/并发参数表单、连通测试、保存配置。"""
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from app.core import config, llm
from app.gui import strings as S
from app.gui.workers import ConnectionTester

_PROVIDER_KEYS = ["deepseek", "openai_compatible", "siliconflow", "ollama", "custom"]


class AiSettingsPage(QWidget):
    sig_save_requested = Signal(object)   # AppConfig（未保存的表单值）
    sig_import_env = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._tester: ConnectionTester | None = None
        self._dirty = False
        self._build_ui()
        self._wire_dirty()

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # 引导横幅（一键导入 / 三步引导，由主窗口控制显示哪种）
        self.ai_import_banner = QFrame()
        self.ai_import_banner.setProperty("role", "banner")
        banner_row = QHBoxLayout(self.ai_import_banner)
        self.ai_banner_label = QLabel(S.AI_BANNER_ONBOARDING)
        self.ai_banner_label.setWordWrap(True)
        self.ai_import_btn = QPushButton(S.BTN_IMPORT_ENV)
        self.ai_import_btn.clicked.connect(self._on_import)
        banner_row.addWidget(self.ai_banner_label, 1)
        banner_row.addWidget(self.ai_import_btn)
        self.ai_import_banner.hide()
        root.addWidget(self.ai_import_banner)

        # 当前生效摘要
        self.ai_summary_label = QLabel(S.AI_SUMMARY_EMPTY)
        self.ai_summary_label.setProperty("role", "muted")
        root.addWidget(self.ai_summary_label)

        # 供应商与连接参数
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(S.LABEL_PROVIDER))
        self.ai_provider_combo = QComboBox()
        for key in _PROVIDER_KEYS:
            self.ai_provider_combo.addItem(config.PROVIDER_PRESETS[key]["label"], key)
        self.ai_provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        row1.addWidget(self.ai_provider_combo, 1)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(S.LABEL_BASE_URL))
        self.ai_base_url_edit = QLineEdit()
        row2.addWidget(self.ai_base_url_edit, 2)
        root.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel(S.LABEL_API_KEY))
        self.ai_api_key_edit = QLineEdit()
        self.ai_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        row3.addWidget(self.ai_api_key_edit, 2)
        self.ai_key_show_btn = QToolButton()
        self.ai_key_show_btn.setText("👁")
        self.ai_key_show_btn.setCheckable(True)
        self.ai_key_show_btn.setAccessibleName("显示 API Key")
        self.ai_key_show_btn.toggled.connect(self._toggle_key_echo)
        row3.addWidget(self.ai_key_show_btn)
        root.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel(S.LABEL_MODEL))
        self.ai_model_edit = QLineEdit()
        row4.addWidget(self.ai_model_edit, 2)
        root.addLayout(row4)

        # 性能参数
        perf = QHBoxLayout()
        self.ai_concurrency_spin = self._spin(perf, S.LABEL_CONCURRENCY, S.TIP_CONCURRENCY, 1, 32)
        self.ai_rate_limit_spin = self._spin(perf, S.LABEL_RATE, S.TIP_RATE, 1, 600)
        perf.addStretch(1)
        root.addLayout(perf)
        perf2 = QHBoxLayout()
        self.ai_batch_size_spin = self._spin(perf2, S.LABEL_BATCH_SIZE, S.TIP_BATCH_SIZE, 1, 20)
        self.ai_batch_tokens_spin = self._spin(perf2, S.LABEL_BATCH_TOKENS, S.TIP_BATCH_TOKENS, 1024, 16384)
        perf2.addStretch(1)
        root.addLayout(perf2)

        # 连通测试
        test_row = QHBoxLayout()
        self.ai_test_btn = QPushButton(S.BTN_TEST)
        self.ai_test_btn.clicked.connect(self._run_test)
        self.ai_test_result = QLabel("")
        self.ai_test_result.setWordWrap(True)
        test_row.addWidget(self.ai_test_btn)
        test_row.addWidget(self.ai_test_result, 1)
        root.addLayout(test_row)

        root.addStretch(1)

        # 底部操作
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.ai_dirty_badge = QLabel(S.DIRTY_BADGE)
        self.ai_dirty_badge.setProperty("role", "warning")
        self.ai_dirty_badge.hide()
        self.ai_reset_btn = QPushButton(S.BTN_RESET)
        self.ai_reset_btn.clicked.connect(self._reset_to_preset)
        self.ai_save_btn = QPushButton(S.BTN_SAVE_CONFIG)
        self.ai_save_btn.setProperty("role", "primary")
        self.ai_save_btn.clicked.connect(self._save)
        bottom.addWidget(self.ai_dirty_badge)
        bottom.addWidget(self.ai_reset_btn)
        bottom.addWidget(self.ai_save_btn)
        root.addLayout(bottom)

    def _spin(self, layout: QHBoxLayout, label: str, tip: str, lo: int, hi: int) -> QSpinBox:
        lbl = QLabel(label)
        lbl.setToolTip(tip)
        lbl.setStatusTip(tip)
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setToolTip(tip)
        spin.setStatusTip(tip)
        layout.addWidget(lbl)
        layout.addWidget(spin)
        layout.addSpacing(12)
        return spin

    # ==================================================================
    # 数据装载与表单值
    # ==================================================================
    def load_config(self, cfg: config.AppConfig) -> None:
        """从 AppConfig 填充表单（不触发 dirty）。"""
        self._loading = True
        idx = self.ai_provider_combo.findData(cfg.provider)
        self.ai_provider_combo.setCurrentIndex(idx if idx >= 0 else self.ai_provider_combo.count() - 1)
        self.ai_base_url_edit.setText(cfg.base_url)
        self.ai_api_key_edit.setText(cfg.api_key)
        self.ai_model_edit.setText(cfg.model)
        self.ai_concurrency_spin.setValue(cfg.max_concurrency)
        self.ai_rate_limit_spin.setValue(cfg.rate_limit_per_min)
        self.ai_batch_size_spin.setValue(cfg.batch_size)
        self.ai_batch_tokens_spin.setValue(cfg.batch_max_tokens)
        self._loading = False
        self._set_dirty(False)

    def form_config(self, base: config.AppConfig) -> config.AppConfig:
        """以 base 为底、表单值覆盖，返回 AppConfig 快照。"""
        return replace(
            base,
            provider=self.ai_provider_combo.currentData(),
            base_url=self.ai_base_url_edit.text().strip(),
            api_key=self.ai_api_key_edit.text().strip(),
            model=self.ai_model_edit.text().strip(),
            max_concurrency=self.ai_concurrency_spin.value(),
            rate_limit_per_min=self.ai_rate_limit_spin.value(),
            batch_size=self.ai_batch_size_spin.value(),
            batch_max_tokens=self.ai_batch_tokens_spin.value(),
        )

    def update_summary(self, cfg: config.AppConfig) -> None:
        masked = cfg.masked()
        if not masked.api_key:
            self.ai_summary_label.setText(S.AI_SUMMARY_EMPTY)
        else:
            label = config.PROVIDER_PRESETS.get(masked.provider, {}).get("label", masked.provider)
            self.ai_summary_label.setText(
                f"{S.AI_SUMMARY_PREFIX}{label} · {masked.model} · Key: {masked.api_key}"
            )

    def show_banner(self, mode: str | None) -> None:
        """mode: 'import'（.env 有 Key）/ 'onboarding'（无 Key）/ None 隐藏。"""
        if mode == "import":
            self.ai_banner_label.setText(S.AI_BANNER_IMPORT)
            self.ai_import_btn.setVisible(True)
            self.ai_import_banner.show()
        elif mode == "onboarding":
            self.ai_banner_label.setText(S.AI_BANNER_ONBOARDING)
            self.ai_import_btn.setVisible(False)
            self.ai_import_banner.show()
        else:
            self.ai_import_banner.hide()

    # ==================================================================
    # 交互
    # ==================================================================
    def _wire_dirty(self) -> None:
        self._loading = False
        for w in (self.ai_base_url_edit, self.ai_api_key_edit, self.ai_model_edit):
            w.textChanged.connect(lambda *_: self._on_edited())
        for w in (self.ai_concurrency_spin, self.ai_rate_limit_spin,
                  self.ai_batch_size_spin, self.ai_batch_tokens_spin):
            w.valueChanged.connect(lambda *_: self._on_edited())
        self.ai_provider_combo.currentIndexChanged.connect(lambda *_: self._on_edited())

    def _on_edited(self) -> None:
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.ai_dirty_badge.setVisible(dirty)
        self.ai_save_btn.setEnabled(dirty)

    def _on_provider_changed(self, _idx: int) -> None:
        key = self.ai_provider_combo.currentData()
        preset = config.PROVIDER_PRESETS.get(key, {})
        if preset.get("base_url"):
            self.ai_base_url_edit.setText(preset["base_url"])
        if preset.get("model"):
            self.ai_model_edit.setText(preset["model"])

    def _toggle_key_echo(self, show: bool) -> None:
        self.ai_api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password
        )

    def _reset_to_preset(self) -> None:
        key = self.ai_provider_combo.currentData()
        preset = config.PROVIDER_PRESETS.get(key, {})
        self.ai_base_url_edit.setText(preset.get("base_url", ""))
        self.ai_model_edit.setText(preset.get("model", ""))
        self.ai_api_key_edit.clear()
        defaults = config.AppConfig()
        self.ai_concurrency_spin.setValue(defaults.max_concurrency)
        self.ai_rate_limit_spin.setValue(defaults.rate_limit_per_min)
        self.ai_batch_size_spin.setValue(defaults.batch_size)
        self.ai_batch_tokens_spin.setValue(defaults.batch_max_tokens)
        self._set_dirty(True)

    def _run_test(self) -> None:
        if self._tester and self._tester.isRunning():
            return
        cfg = self.form_config(config.AppConfig())
        self.ai_test_btn.setEnabled(False)
        self.ai_test_btn.setText(S.TESTING)
        self.ai_test_result.setText("")
        self._tester = ConnectionTester(cfg, self)
        self._tester.sig_result.connect(self._on_test_result)
        self._tester.start()

    def _on_test_result(self, result: llm.ConnectionResult) -> None:
        self.ai_test_btn.setEnabled(True)
        self.ai_test_btn.setText(S.BTN_TEST)
        if result.ok:
            self.ai_test_result.setProperty("role", "success")
            self.ai_test_result.setText(S.TEST_OK.format(ms=result.latency_ms))
        else:
            self.ai_test_result.setProperty("role", "error")
            text = {
                "network": S.TEST_FAIL_NETWORK,
                "auth": S.TEST_FAIL_AUTH,
                "model_not_found": S.TEST_FAIL_MODEL,
            }.get(result.error_class, S.TEST_FAIL_OTHER.format(msg=result.message))
            self.ai_test_result.setText(text)
        self.ai_test_result.style().unpolish(self.ai_test_result)
        self.ai_test_result.style().polish(self.ai_test_result)
        if self._tester:
            self._tester.deleteLater()
            self._tester = None

    def _save(self) -> None:
        self.sig_save_requested.emit(self.form_config(config.AppConfig.load()))

    def _on_import(self) -> None:
        self.sig_import_env.emit()

    def mark_saved(self) -> None:
        self._set_dirty(False)

    def shutdown(self) -> None:
        if self._tester is not None:
            try:
                if self._tester.isRunning():
                    self._tester.wait(2000)
            except RuntimeError:
                pass  # 线程对象已随 deleteLater 销毁
            self._tester = None
