#!/usr/bin/env python3
"""B站视频对话转文本 — 向后兼容入口。

新代码请用 `b2text` 子命令。本脚本保留为 `python bilibili_to_text.py BV... -o ...`
的旧调用方式：第一个参数不是子命令时自动补 `run` 子命令，因此
`python bilibili_to_text.py <BV号|URL|本地文件> -o <输出> [--device cpu] [--spk-num N]
[--no-overwrite] [--keep-audio] [--batch]` 均可直接使用。
"""
import sys
from b2text.cli import main as cli_main


_KNOWN_SUBCOMMANDS = {"serve", "transcribe", "status", "list", "cancel", "clean", "run"}


def main(argv: list[str] | None = None) -> int:
    """兼容旧用法：第一个参数不是子命令时，插入 `run` 再交给 CLI。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in _KNOWN_SUBCOMMANDS:
        args.insert(0, "run")
    sys.argv[1:] = args
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
