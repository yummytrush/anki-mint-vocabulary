"""高级设置页 — ParamRow 参数行（悬停白话 + 单项重置）+ 全部恢复默认。"""
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.core import config
from app.gui import param_docs, strings as S
from app.gui.widgets import ParamRow


class AdvancedPage(QWidget):
    sig_save_requested = Signal(object)  # AppConfig

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: dict[str, ParamRow] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        note = QLabel(S.ADV_NOTE)
        note.setProperty("role", "muted")
        root.addWidget(note)

        for group_title, keys in param_docs.PARAM_GROUPS:
            group = QGroupBox(group_title)
            gl = QVBoxLayout(group)
            for key in keys:
                spec = dict(param_docs.PARAM_DOCS[key])
                row = ParamRow(key, spec)
                row.sig_changed.connect(self._on_edited)
                gl.addWidget(row)
                self._rows[key] = row
            root.addWidget(group)

        dirs = QGroupBox(S.ADV_DIRS_NOTE)
        dl = QVBoxLayout(dirs)
        dirs_label = QLabel(
            f"数据: {config.DATA_DIR}   缓存: {config.CACHE_DIR}   音频: {config.MEDIA_DIR}"
        )
        dirs_label.setProperty("role", "muted")
        dl.addWidget(dirs_label)
        root.addWidget(dirs)

        root.addStretch(1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.adv_dirty_badge = QLabel(S.DIRTY_BADGE)
        self.adv_dirty_badge.setProperty("role", "warning")
        self.adv_dirty_badge.hide()
        self.adv_reset_all_btn = QPushButton(S.BTN_ADV_RESET_ALL)
        self.adv_reset_all_btn.clicked.connect(self._reset_all)
        self.adv_save_btn = QPushButton(S.BTN_SAVE_CONFIG)
        self.adv_save_btn.setProperty("role", "primary")
        self.adv_save_btn.clicked.connect(self._save)
        bottom.addWidget(self.adv_dirty_badge)
        bottom.addWidget(self.adv_reset_all_btn)
        bottom.addWidget(self.adv_save_btn)
        root.addLayout(bottom)

    # ==================================================================
    # 数据装载与保存
    # ==================================================================
    def load_config(self, cfg: config.AppConfig) -> None:
        defaults = config.AppConfig()
        for key, row in self._rows.items():
            row.blockSignals(True)
            row.set_value(getattr(cfg, key))
            row.set_default(getattr(defaults, key))
            row.blockSignals(False)
        self._set_dirty(False)

    def form_config(self, base: config.AppConfig) -> config.AppConfig:
        overrides = {key: row.value() for key, row in self._rows.items()}
        return replace(base, **overrides)

    def _on_edited(self) -> None:
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self.adv_dirty_badge.setVisible(dirty)
        self.adv_save_btn.setEnabled(dirty)

    def _reset_all(self) -> None:
        ret = QMessageBox.question(self, self.windowTitle(), S.CONFIRM_ADV_RESET)
        if ret != QMessageBox.StandardButton.Yes:
            return
        defaults = config.AppConfig()
        for key, row in self._rows.items():
            row.set_value(getattr(defaults, key))
        self._set_dirty(True)

    def _save(self) -> None:
        self.sig_save_requested.emit(self.form_config(config.AppConfig.load()))

    def mark_saved(self) -> None:
        self._set_dirty(False)
