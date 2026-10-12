#!/usr/bin/env python3
"""兼容入口：旧命令继续可用（保留至 v0.2.0）。实现已拆分至 app/ 包。"""
import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
