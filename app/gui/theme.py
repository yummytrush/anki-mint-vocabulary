"""主题 tokens 与 QSS 加载（浅色/暗色双模式，跟随系统）。

色板来源：docs/designs/gui-frontend-design.md §4.1。
"""
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

STYLE_QSS = Path(__file__).resolve().parent / "style.qss"

_TOKENS_LIGHT = {
    "PRIMARY": "#2980b9",
    "PRIMARY_HOVER": "#1a5276",
    "SUCCESS": "#27ae60",
    "WARNING": "#e67e22",
    "ERROR": "#c0392b",
    "TEXT": "#2c3e50",
    "TEXT_MUTED": "#7f8c8d",
    "BASE": "#ffffff",
    "SURFACE": "#f0f3f5",
    "BORDER": "#d5dbdb",
    "HINT_BG": "#fef9e7",
    "SELECTION": "#d6eaf8",
}

_TOKENS_DARK = {
    "PRIMARY": "#5b9bd5",
    "PRIMARY_HOVER": "#7db3e0",
    "SUCCESS": "#4ac07c",
    "WARNING": "#f0a54a",
    "ERROR": "#e06c5d",
    "TEXT": "#dddddd",
    "TEXT_MUTED": "#9a9a9a",
    "BASE": "#1e1e1e",
    "SURFACE": "#2a2a2a",
    "BORDER": "#444444",
    "HINT_BG": "#3a3520",
    "SELECTION": "#26425c",
}


def system_prefers_dark() -> bool:
    """读取系统当前色彩方案（Qt 6.5+ QStyleHints）。"""
    app = QApplication.instance()
    if app is None:
        return False
    try:
        from PySide6.QtCore import Qt
        scheme = app.styleHints().colorScheme()
        return scheme == Qt.ColorScheme.Dark
    except Exception:
        return False


def load_stylesheet(dark: bool | None = None) -> str:
    """读取 style.qss 并按模式替换 tokens。"""
    if dark is None:
        dark = system_prefers_dark()
    tokens = _TOKENS_DARK if dark else _TOKENS_LIGHT
    qss = STYLE_QSS.read_text(encoding="utf-8")
    for key, value in tokens.items():
        qss = qss.replace("{{" + key + "}}", value)
    return qss


def apply_theme(app: QApplication, dark: bool | None = None) -> None:
    """应用样式表，并设置 QPalette 兜底原生控件配色。"""
    if dark is None:
        dark = system_prefers_dark()
    app.setStyleSheet(load_stylesheet(dark))
    tokens = _TOKENS_DARK if dark else _TOKENS_LIGHT
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(tokens["BASE"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens["TEXT"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(tokens["BASE"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(tokens["TEXT"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(tokens["SURFACE"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens["TEXT"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens["PRIMARY"]))
    app.setPalette(palette)
