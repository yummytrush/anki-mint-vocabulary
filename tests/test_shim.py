"""兼容 shim 冒烟测试 — 旧入口 vocab_anki_generator.py 可 import、--help 可解析。"""
import sys

import pytest


def test_shim_importable():
    import vocab_anki_generator  # noqa: F401  -- 导入即成功，且不触发 main()

    import app

    assert app.__version__


def test_shim_help_parses(monkeypatch, capsys):
    """--help 应正常解析并退出码 0（证明 argparse 参数与原版一致可用）。"""
    import vocab_anki_generator  # noqa: F401  -- 确保 app.cli 可导入

    from app.cli import main

    monkeypatch.setattr(sys, "argv", ["vocab_anki_generator.py", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "--quality-max-attempts" in out
    assert "--refresh-audio" in out
