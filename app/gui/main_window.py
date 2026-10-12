"""主窗口 — QTabWidget 五页签、AppConfig 单例、页间信号路由、无 Key 引导。

数据流（docs/designs/gui-frontend-design.md §1.3）：
AI 设置页 / 高级设置页 保存 → save_config() → data/config.json +
apply_to_config() + 重建 llm.rate_limiter → 各页只读引用。
"""
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QTabWidget

from app import __version__
from app.core import config, llm
from app.gui import strings as S
from app.gui.dialogs import NormalizeDialog
from app.gui.pages.advanced_page import AdvancedPage
from app.gui.pages.ai_settings_page import AiSettingsPage
from app.gui.pages.assistant_page import AssistantPage
from app.gui.pages.prompts_page import PromptsPage
from app.gui.pages.workbench_page import WorkbenchPage


class MainWindow(QMainWindow):
    def __init__(self, app_config: config.AppConfig | None = None, parent=None):
        super().__init__(parent)
        self.app_cfg = app_config if app_config is not None else config.AppConfig.load()
        # 启动即把配置应用到 core 全局（与 CLI 行为一致）
        self.app_cfg.apply_to_config()

        self.setWindowTitle(f"{S.APP_TITLE} v{__version__}")
        self.resize(1024, 720)
        self.setMinimumSize(860, 600)

        self.tabs = QTabWidget()
        self.workbench = WorkbenchPage()
        self.ai_settings = AiSettingsPage()
        self.prompts_page = PromptsPage()
        self.advanced = AdvancedPage()
        self.assistant = AssistantPage()
        self.tabs.addTab(self.workbench, S.TAB_WORKBENCH)
        self.tabs.addTab(self.ai_settings, S.TAB_AI)
        self.tabs.addTab(self.prompts_page, S.TAB_PROMPTS)
        self.tabs.addTab(self.advanced, S.TAB_ADVANCED)
        self.tabs.addTab(self.assistant, S.TAB_ASSISTANT)
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage(self._summary_text())

        self._wire_signals()
        self._refresh_pages()

    # ==================================================================
    # 信号路由
    # ==================================================================
    def _wire_signals(self) -> None:
        # 保存配置（两个设置页共用入口）
        self.ai_settings.sig_save_requested.connect(self.save_config)
        self.advanced.sig_save_requested.connect(self.save_config)
        self.ai_settings.sig_import_env.connect(self.import_env_config)
        # 跳转 AI 助手（提示词页 / 工作台问题行）
        self.workbench.sig_request_assistant.connect(self.open_assistant_with_context)
        self.prompts_page.sig_request_assistant.connect(self.open_assistant_with_context)
        # AI 助手代码块 → 提示词编辑器
        self.assistant.sig_apply_code.connect(self.apply_code_to_prompts)
        # 工作台 AI 规范化对话框
        self.workbench.wb_normalize_btn.clicked.connect(self._open_normalize_dialog)
        # 快捷键 Ctrl+1..5
        for i in range(5):
            self._add_tab_shortcut(i)

    def _add_tab_shortcut(self, index: int) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut
        sc = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
        sc.activated.connect(lambda i=index: self.tabs.setCurrentIndex(i))

    # ==================================================================
    # 配置生命周期
    # ==================================================================
    def save_config(self, new_cfg: config.AppConfig) -> None:
        """保存配置：落盘 + 应用 core 全局 + 刷新各页。"""
        self.app_cfg = new_cfg
        new_cfg.save()
        new_cfg.apply_to_config()
        llm.rate_limiter = llm.RateLimiter(new_cfg.rate_limit_per_min)
        self._refresh_pages(saved=True)

    def import_env_config(self) -> None:
        """一键导入：把当前生效（含 .env 的 Key）写入 config.json。"""
        self.save_config(self.app_cfg)
        self.statusBar().showMessage(S.TIP_IMPORTED, 8000)

    def has_api_key(self) -> bool:
        return bool(self.app_cfg.api_key)

    def _refresh_pages(self, saved: bool = False) -> None:
        self.ai_settings.load_config(self.app_cfg)
        self.ai_settings.update_summary(self.app_cfg)
        self.advanced.load_config(self.app_cfg)
        # 无 Key 引导横幅
        import os
        if not os.path.exists(config.CONFIG_FILE):
            self.ai_settings.show_banner("import" if self.app_cfg.api_key else "onboarding")
        else:
            self.ai_settings.show_banner(None)
        # Key 可用性广播
        self.workbench.set_api_key_available(self.has_api_key())
        self.assistant.set_api_key_available(self.has_api_key())
        self.statusBar().showMessage(
            S.TIP_CONFIG_SAVED if saved else self._summary_text(),
            8000 if saved else 0,
        )

    def _summary_text(self) -> str:
        if not self.app_cfg.api_key:
            return S.AI_SUMMARY_EMPTY
        label = config.PROVIDER_PRESETS.get(self.app_cfg.provider, {}).get("label", self.app_cfg.provider)
        return f"{label} · {self.app_cfg.model}"

    # ==================================================================
    # 页间跳转
    # ==================================================================
    def open_assistant_with_context(self, payload: dict) -> None:
        self.assistant.set_context(payload)
        self.tabs.setCurrentWidget(self.assistant)

    def apply_code_to_prompts(self, code_text: str) -> None:
        self.tabs.setCurrentWidget(self.prompts_page)
        self.prompts_page.apply_code(code_text)

    def _open_normalize_dialog(self) -> None:
        dlg = NormalizeDialog(self, on_use_result=self.workbench.reload_file)
        dlg.exec()

    # ==================================================================
    # 关闭
    # ==================================================================
    def closeEvent(self, event) -> None:
        self.workbench.shutdown()
        self.ai_settings.shutdown()
        self.assistant.shutdown()
        super().closeEvent(event)


def main() -> int:
    """GUI 入口：python -m app.gui"""
    import sys

    from PySide6.QtWidgets import QApplication

    from app.gui import theme

    app = QApplication(sys.argv)
    app.setApplicationName("AnkiMint")
    theme.apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()
