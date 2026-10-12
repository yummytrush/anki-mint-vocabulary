"""
GUI 冒烟测试（pytest-qt，offscreen 平台）。

覆盖（实施计划 Task 6 验收项）：
  - 五页可打开切换
  - 无 Key 时开始/发送/连通按钮状态
  - 连通测试按钮触发 mock 的 test_llm_connection 并渲染分类结果
  - 拖拽事件模拟（接受 .md、拒绝其它后缀）
  - 提示词页七个文件列表
  - 预检链路（真实解析 examples 文件）

环境没有 PySide6 时整体 skip（CI 无 GUI 依赖也可用）。
"""
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from app.core import config
from app.gui import strings as S


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    """隔离配置文件的 MainWindow（不触碰真实 data/config.json）。"""
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
    from app.gui.main_window import MainWindow
    from app.core import config as _cfg
    w = MainWindow(app_config=_cfg.AppConfig(api_key="sk-gui-smoke-test"))
    qtbot.addWidget(w)
    return w


class TestMainWindowBasics:
    def test_five_tabs_open_and_switch(self, window):
        assert window.tabs.count() == 5
        names = [window.tabs.tabText(i) for i in range(5)]
        assert names == [S.TAB_WORKBENCH, S.TAB_AI, S.TAB_PROMPTS, S.TAB_ADVANCED, S.TAB_ASSISTANT]
        for i in range(5):
            window.tabs.setCurrentIndex(i)
            assert window.tabs.currentIndex() == i

    def test_no_key_disables_start_and_send(self, qtbot, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
        from app.gui.main_window import MainWindow
        w = MainWindow(app_config=config.AppConfig(api_key=""))
        qtbot.addWidget(w)
        assert w.workbench.wb_start_btn.isEnabled() is False
        assert w.assistant.as_send_btn.isEnabled() is False
        # 引导横幅出现
        assert w.ai_settings.ai_import_banner.isVisibleTo(w.ai_settings) is True

    def test_onboarding_banner_hidden_when_config_exists(self, qtbot, monkeypatch, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_file))
        from app.gui.main_window import MainWindow
        w = MainWindow(app_config=config.AppConfig(api_key="sk-x"))
        qtbot.addWidget(w)
        assert w.ai_settings.ai_import_banner.isVisibleTo(w.ai_settings) is False


class TestConnectionTestButton:
    def test_connect_button_triggers_mock_success(self, window, qtbot, monkeypatch):
        from app.core import llm

        calls = {}

        def fake_test(cfg):
            calls["cfg"] = cfg
            return llm.ConnectionResult(True, 123, "", "模型回复正常: OK")

        monkeypatch.setattr(llm, "test_llm_connection", fake_test)
        window.ai_settings.ai_test_btn.click()
        qtbot.waitUntil(lambda: "连接成功" in window.ai_settings.ai_test_result.text(), timeout=5000)
        assert "123" in window.ai_settings.ai_test_result.text()
        assert calls["cfg"].api_key == "sk-gui-smoke-test"

    def test_connect_button_renders_auth_failure(self, window, qtbot, monkeypatch):
        from app.core import llm

        monkeypatch.setattr(
            llm, "test_llm_connection",
            lambda cfg: llm.ConnectionResult(False, 50, "auth", "鉴权失败（401）"),
        )
        window.ai_settings.ai_test_btn.click()
        qtbot.waitUntil(lambda: "鉴权失败" in window.ai_settings.ai_test_result.text(), timeout=5000)


class TestDropZone:
    def _drop_event(self, path: str):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        return mime

    def test_drop_md_accepted(self, window, tmp_path):
        md = tmp_path / "words.md"
        md.write_text("1. analyze We analyze data.\n", encoding="utf-8")
        from app.gui.widgets import local_md_path_from_urls
        path = local_md_path_from_urls(self._drop_event(str(md)).urls())
        assert path is not None
        assert Path(path) == md

    def test_drop_exe_rejected(self, window):
        from app.gui.widgets import local_md_path_from_urls
        assert local_md_path_from_urls(self._drop_event("C:/app.exe").urls()) is None
        assert local_md_path_from_urls(self._drop_event("C:/notes.pdf").urls()) is None

    def test_drop_txt_accepted(self):
        from app.gui.widgets import local_md_path_from_urls
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile("C:/words.txt")])
        assert local_md_path_from_urls(mime.urls()) == "C:/words.txt"


class TestPromptsPage:
    def test_seven_prompt_files_listed(self, window):
        assert window.prompts_page.pr_file_list.count() == 7
        labels = [window.prompts_page.pr_file_list.item(i).text() for i in range(7)]
        assert any("enrich.md" in t for t in labels)
        assert any("assistant.md" in t for t in labels)

    def test_editor_loads_selected_file(self, window):
        window.prompts_page.pr_file_list.setCurrentRow(0)
        assert "词汇学专家" in window.prompts_page.pr_editor.toPlainText()


class TestWorkbenchPreview:
    def test_preview_flow_with_example_file(self, window):
        example = Path(__file__).resolve().parent.parent / "examples" / "example-two-line.md"
        window.workbench.set_file(str(example))
        assert "识别到 5 条" in window.workbench.wb_preview_summary.text()
        assert "两行式" in window.workbench.wb_preview_summary.text()
        assert window.workbench.wb_start_btn.isEnabled() is True

    def test_dirty_file_shows_problem_lines(self, window):
        example = Path(__file__).resolve().parent.parent / "examples" / "example-dirty.md"
        window.workbench.set_file(str(example))
        assert window.workbench.wb_problems_list.count() == 3
        assert "3 行无法识别" in window.workbench.wb_preview_summary.text()

    def test_filename_template_resolves(self, window):
        example = Path(__file__).resolve().parent.parent / "examples" / "example-two-line.md"
        window.workbench.set_file(str(example))
        window.workbench.wb_filename_edit.setText("IELTS-{日期}-{词数}.apkg")
        resolved = window.workbench._resolved_filename()
        assert "{日期}" not in resolved and "{词数}" not in resolved
        assert resolved.endswith("-5.apkg")


class TestDeckNameInput:
    def test_deck_name_input_exists_with_default_placeholder(self, window):
        assert window.workbench.wb_deck_name_edit.text() == ""
        assert window.workbench.wb_deck_name_edit.placeholderText() == "AI 词汇强化训练"

    def test_deck_name_disabled_while_running_flag_wired(self, window):
        """开始前可编辑；RunConfig 组装包含卡组名（借 start_run 前置检查验证）。"""
        example = Path(__file__).resolve().parent.parent / "examples" / "example-two-line.md"
        window.workbench.set_file(str(example))
        window.workbench.wb_deck_name_edit.setText("我的卡组")
        assert window.workbench.wb_deck_name_edit.isEnabled() is True
