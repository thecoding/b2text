#!/usr/bin/env python3
"""B站视频对话转文本 — 向后兼容入口。

新代码请用 `b2text` 子命令。本脚本保留为 `python bilibili_to_text.py BV... -o ...`
的旧调用方式。
"""
import sys
from b2text.cli import main as cli_main

if __name__ == "__main__":
    sys.exit(cli_main())
