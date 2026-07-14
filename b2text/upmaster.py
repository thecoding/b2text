# b2text/upmaster.py
"""UP 主视频列表抓取 + 批量展开为 bvid 列表。"""
from __future__ import annotations

import json
import subprocess
from urllib.parse import urlencode


_SPACE_API = "https://api.bilibili.com/x/space/arc/search"


def fetch_up_videos(uid: int, limit: int, *, cookie: str) -> list[str]:
    """调用 B 站 space/arc/search，返回最多 limit 条 bvid。

    参数：
        uid: UP 主 mid
        limit: 用户要的视频数（1-50，B 站单页最多 50）
        cookie: 完整 cookie 字符串

    返回：bvid 字符串列表（已按 B 站返回顺序）
    """
    if limit < 1:
        return []
    ps = min(limit, 50)  # B 站单页最多 50
    url = f"{_SPACE_API}?{urlencode({'mid': uid, 'ps': ps, 'pn': 1, 'order': 'pubdate'})}"
    cmd = [
        "curl", "-s", url,
        "-H", f"Cookie: {cookie}",
        "-H", "User-Agent: Mozilla/5.0",
        "-H", "Referer: https://space.bilibili.com/",
        "--max-time", "20",
    ]
    result = subprocess.run(cmd, capture_output=True)
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    if data.get("code") != 0:
        return []
    vlist = data.get("data", {}).get("list", {}).get("vlist", [])
    bvids = [v.get("bvid") for v in vlist if v.get("bvid")]
    return bvids[:limit]
