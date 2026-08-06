"""统一 BV 输出文件路径，避免 fanout 跳过判断与写入分散到两个地方导致漂移。"""
from __future__ import annotations

import re
from pathlib import Path

_SAFE_RE = re.compile(r'[<>:"/\\|?*]')


def output_path_for_bvid(output_dir: str | Path, bvid: str) -> Path:
    """返回某 BV 在指定输出目录下的转写文件路径（<bvid>.txt）。

    文件名清洗规则与 worker.normalize_write 完全一致（去掉文件系统非法字符，
    截断到 80 字符），保证 fanout 的跳过判断与实际写盘位置一一对应。
    """
    safe = _SAFE_RE.sub("_", bvid)[:80]
    return Path(output_dir) / f"{safe}.txt"