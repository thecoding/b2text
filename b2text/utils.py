import re
from pathlib import Path

_BVID_RE = re.compile(r"[Bb][Vv][a-zA-Z0-9]+")


def extract_bvid(url_or_bvid: str) -> str | None:
    """从 URL 或纯 BV 号中提取 BV 号。
    无效输入返回 None。
    """
    if not url_or_bvid:
        return None
    match = _BVID_RE.search(url_or_bvid)
    if not match:
        return None
    bvid = match.group(0)
    # 大小写归一：BV 前缀大写，保留后续字符原样大小写
    return "BV" + bvid[2:]


def format_timestamp(seconds: float) -> str:
    """把秒数格式化为 HH:MM:SS。截断亚秒。"""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"