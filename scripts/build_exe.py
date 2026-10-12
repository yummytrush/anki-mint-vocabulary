"""PyInstaller 打包脚本 — 产出单文件 Windows GUI 版 exe。

用法::

    python scripts/build_exe.py             # 默认 windowed（无控制台窗口，发布用）
    python scripts/build_exe.py --console   # 保留控制台（调试用：可看日志/异常输出）
    python scripts/build_exe.py --name AnkiMint-Dev   # 自定义产物名

产物：``dist/<name>.exe``

说明：
- 单文件（onefile）模式首次启动需解压到临时目录，启动比普通程序慢几秒，属正常现象。
- prompts/ 与 templates/ 会打包进 exe 作为内置默认值（加载器按 ``sys._MEIPASS`` 定位）。
  exe 版不支持「改文件自定义提示词」——需要自定义提示词/模板请用源码方式运行。
- 无签名条件下，Windows Defender 与部分杀软对 PyInstaller onefile 产物常见误报，
  这是该打包方式的普遍现象而非病毒；企业环境可自行代码签名，或改用
  ``pip install -e .`` 源码路线。
- data/（缓存、音频、输出、日志）写在 exe 运行时的工作目录下，双击启动时即 exe 所在目录。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 随 exe 分发的外部资源：提示词与卡片模板（缺失时加载器会回退内置默认）
ADD_DATA = [
    ("prompts", "prompts"),
    ("templates", "templates"),
    ("app/gui/style.qss", "app/gui"),
]

# 明确用不到的大模块，减小体积
EXCLUDES = ["tkinter", "matplotlib", "numpy", "IPython", "pytest"]


def build(name: str, console: bool) -> int:
    entry = ROOT / "app" / "gui" / "__main__.py"
    if not entry.exists():
        print(f"[build_exe] 找不到入口文件: {entry}", file=sys.stderr)
        return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build_exe] 未安装 PyInstaller，正在安装 ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name", name,
        # 让 PyInstaller 能解析 `from app.xxx import ...`（项目根加入 sys.path）
        "--paths", str(ROOT),
    ]
    args.append("--windowed" if not console else "--console")
    for src, dst in ADD_DATA:
        p = ROOT / src
        if not p.exists():
            print(f"[build_exe] 警告: 缺少 {src}，跳过（运行时将回退内置默认）")
            continue
        args += ["--add-data", f"{p}{os.pathsep}{dst}"]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    args.append(str(entry))

    print("[build_exe] PyInstaller", " ".join(args[4:]))
    result = subprocess.call(args, cwd=ROOT)
    if result != 0:
        print("[build_exe] 打包失败，请把上方完整输出贴到 Issue", file=sys.stderr)
        return result

    exe = ROOT / "dist" / f"{name}.exe"
    size_mb = exe.stat().st_size / 1048576 if exe.exists() else 0
    print()
    print(f"[build_exe] 完成: {exe} ({size_mb:.1f} MB)")
    print("[build_exe] 提示: 首次启动较慢为 onefile 解压，杀软误报见脚本头部说明。")
    return 0


def main() -> int:
    # Windows 控制台可能是 cp1252 等无法编码中文的代码页（GitHub runner 即如此），
    # 输出降级为占位符而不是让整个构建在打印成功信息时崩掉（exe 其实已构建完成）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="AnkiMint GUI 打包为单文件 exe")
    parser.add_argument(
        "--name", default="AnkiMint", help="产物名称（默认 AnkiMint）"
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="保留控制台窗口（默认隐藏；调试时用 --console 可直接看到日志输出）",
    )
    args = parser.parse_args()
    return build(args.name, args.console)


if __name__ == "__main__":
    raise SystemExit(main())
